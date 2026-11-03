import logging
import os
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse
from urllib.request import Request, urlopen

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


def _is_direct_image_url(url):
    return urlparse(url).hostname in _DIRECT_IMAGE_HOSTS


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
        with urlopen(Request(url, headers=IMAGE_HEADERS), timeout=IMAGE_TIMEOUT_SECONDS) as response, \
                open(part_path, "wb") as out:
            shutil.copyfileobj(response, out, IMAGE_CHUNK_BYTES)
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
    if _is_direct_image_url(job["url"]):
        return _download_direct_image(job["url"], _direct_image_path(job["subreddit"], job["url"], job["postId"]))
    return _download_with_ytdlp(job)


def _run_job(job_id):
    with _jobs_lock:
        job = _jobs[job_id]
        job["status"] = "downloading"
        job["startedAt"] = time.time()

    try:
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
