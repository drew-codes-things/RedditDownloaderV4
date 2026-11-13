import ipaddress
import logging
import os
import re
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

import yt_dlp

logger = logging.getLogger("reddit_relay.worker")

DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "/downloads")
MAX_CONCURRENT_DOWNLOADS = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "4"))
MAX_FINISHED_JOBS = 1000
FINISHED_STATUSES = ("done", "error")

_DIRECT_IMAGE_HOSTS = {"preview.redd.it", "i.redd.it", "external-preview.redd.it"}
IMAGE_TIMEOUT_SECONDS = 60
IMAGE_CHUNK_BYTES = 64 * 1024
IMAGE_HEADERS = {"User-Agent": "reddit-relay/1.0", "Accept": "image/*,*/*"}
ALLOWED_URL_SCHEMES = ("http", "https")
BYTES_PER_MB = 1024 * 1024
MAX_DOWNLOAD_BYTES = int(os.getenv("MAX_DOWNLOAD_MB", "1024")) * BYTES_PER_MB
MAX_REDIRECTS = 5
YTDLP_HOSTS = frozenset({
    "v.redd.it",
    "reddit.com",
    "www.reddit.com",
    "old.reddit.com",
    "imgur.com",
    "i.imgur.com",
})
DIRECT_MEDIA_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".mp3", ".mov")


def _resolved_addresses(hostname):
    try:
        return {info[4][0] for info in socket.getaddrinfo(hostname, None)}
    except (OSError, UnicodeError):
        return set()


def is_public_http_url(url):
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
    except ValueError:
        return False
    if parsed.scheme not in ALLOWED_URL_SCHEMES or not hostname:
        return False
    addresses = _resolved_addresses(hostname)
    return bool(addresses) and all(ipaddress.ip_address(address).is_global for address in addresses)


def _is_direct_image_url(url):
    return urlparse(url).hostname in _DIRECT_IMAGE_HOSTS


def _is_ytdlp_host(url):
    return urlparse(url).hostname in YTDLP_HOSTS


def _is_direct_media_link(url):
    return urlparse(url).path.lower().endswith(DIRECT_MEDIA_EXTENSIONS)


class PublicOnlyRedirectHandler(HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_public_http_url(newurl):
            raise URLError(f"refusing to follow a redirect to a non-public address: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_public_url(url):
    opener = build_opener(PublicOnlyRedirectHandler)
    return opener.open(Request(url, headers=IMAGE_HEADERS), timeout=IMAGE_TIMEOUT_SECONDS)


def _copy_capped(response, out):
    written = 0
    while chunk := response.read(IMAGE_CHUNK_BYTES):
        written += len(chunk)
        if written > MAX_DOWNLOAD_BYTES:
            raise OSError(f"download is larger than {MAX_DOWNLOAD_BYTES // BYTES_PER_MB} MB")
        out.write(chunk)


_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_DOWNLOADS)
_jobs_lock = threading.Lock()
_jobs = {}

_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe_path_segment(value, fallback):
    value = (value or "").strip()
    if not value:
        return fallback
    cleaned = _SAFE_SEGMENT.sub("_", value).strip("_")
    return cleaned or fallback


def _subreddit_dir(subreddit):
    subreddit_dir = os.path.join(DOWNLOAD_DIR, _safe_path_segment(subreddit, "unsorted"))
    os.makedirs(subreddit_dir, exist_ok=True)
    return subreddit_dir


def _make_ydl_opts(subreddit, post_id):
    post_prefix = _safe_path_segment(post_id, uuid.uuid4().hex)
    return {
        "outtmpl": os.path.join(_subreddit_dir(subreddit), f"{post_prefix}_%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "merge_output_format": "mp4",
        "ignoreerrors": False,
        "max_filesize": MAX_DOWNLOAD_BYTES,
    }


def _direct_image_path(subreddit, url, post_id):
    file_name = _safe_path_segment(os.path.basename(urlparse(url).path), uuid.uuid4().hex)
    post_prefix = _safe_path_segment(post_id, uuid.uuid4().hex)
    return os.path.join(_subreddit_dir(subreddit), f"{post_prefix}_{file_name}")


def _image_candidates(url):
    parsed = urlparse(url)
    if parsed.hostname != "preview.redd.it":
        return [url]
    return [f"https://i.redd.it{parsed.path}", url]


def _download_direct_image(url, target):
    if os.path.exists(target):
        return target
    last_error = None
    for candidate in _image_candidates(url):
        try:
            return _fetch_to_file(candidate, target)
        except OSError as err:
            last_error = err
            logger.info("image fetch failed for %s: %s", candidate, err)
    raise last_error


def _fetch_to_file(url, target):
    part_path = target + ".part"
    try:
        with _open_public_url(url) as response, open(part_path, "wb") as out:
            _copy_capped(response, out)
        os.replace(part_path, target)
    except BaseException:
        if os.path.exists(part_path):
            os.remove(part_path)
        raise
    return target


def _download_with_ytdlp(job):
    with yt_dlp.YoutubeDL(_make_ydl_opts(job["subreddit"], job["postId"])) as ydl:
        info = ydl.extract_info(job["url"], download=True)
        return ydl.prepare_filename(info) if info else None


def _download(job):
    url = job["url"]
    if _is_direct_image_url(url):
        return _download_direct_image(url, _direct_image_path(job["subreddit"], url, job["postId"]))
    if _is_ytdlp_host(url):
        return _download_with_ytdlp(job)
    if _is_direct_media_link(url):
        return _download_direct_image(url, _direct_image_path(job["subreddit"], url, job["postId"]))
    raise ValueError(f"refusing to download: {urlparse(url).hostname} is not a supported media host")


def _run_job(job_id):
    with _jobs_lock:
        job = _jobs[job_id]
        job["status"] = "downloading"
        job["startedAt"] = time.time()

    try:
        if not is_public_http_url(job["url"]):
            raise ValueError("refusing to download: not a public http(s) address")
        filename = _download(job)
        with _jobs_lock:
            job["status"] = "done"
            job["finishedAt"] = time.time()
            job["file"] = filename
    except Exception as err:
        logger.error("job %s failed for %s: %s", job_id, job["url"], err)
        with _jobs_lock:
            job["status"] = "error"
            job["finishedAt"] = time.time()
            job["error"] = str(err)


def _prune_finished_jobs():
    finished = [job for job in _jobs.values() if job["status"] in FINISHED_STATUSES]
    excess = len(finished) - MAX_FINISHED_JOBS
    if excess <= 0:
        return
    for job in sorted(finished, key=lambda j: j["finishedAt"])[:excess]:
        del _jobs[job["id"]]


def enqueue(url, subreddit=None, post_id=None):
    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _prune_finished_jobs()
        _jobs[job_id] = {
            "id": job_id,
            "url": url,
            "subreddit": subreddit,
            "postId": post_id,
            "status": "queued",
            "queuedAt": time.time(),
        }
    _executor.submit(_run_job, job_id)
    return job_id


def get_job(job_id):
    with _jobs_lock:
        return dict(_jobs[job_id]) if job_id in _jobs else None


def list_jobs(limit=100):
    with _jobs_lock:
        jobs = sorted(_jobs.values(), key=lambda j: j["queuedAt"], reverse=True)
        return [dict(job) for job in jobs[:limit]]
