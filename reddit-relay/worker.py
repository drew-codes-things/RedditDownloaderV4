import logging
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import yt_dlp

logger = logging.getLogger("reddit_relay.worker")

DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "/downloads")
MAX_CONCURRENT_DOWNLOADS = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "4"))

_DIRECT_IMAGE_HOSTS = {"preview.redd.it", "i.redd.it", "external-preview.redd.it"}


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


def _make_ydl_opts(subreddit, url, post_id):
    subreddit_dir = os.path.join(DOWNLOAD_DIR, _safe_path_segment(subreddit, "unsorted"))
    os.makedirs(subreddit_dir, exist_ok=True)
    post_prefix = _safe_path_segment(post_id, uuid.uuid4().hex)
    opts = {
        "outtmpl": os.path.join(subreddit_dir, f"{post_prefix}_%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "merge_output_format": "mp4",
        "ignoreerrors": False,
    }
    if _is_direct_image_url(url):
        opts["http_headers"] = {"Accept": "*/*"}
    return opts


def _run_job(job_id):
    with _jobs_lock:
        job = _jobs[job_id]
        job["status"] = "downloading"
        job["startedAt"] = time.time()

    try:
        opts = _make_ydl_opts(job["subreddit"], job["url"], job["postId"])
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(job["url"], download=True)
            filename = ydl.prepare_filename(info) if info else None
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


def enqueue(url, subreddit=None, post_id=None):
    job_id = uuid.uuid4().hex
    with _jobs_lock:
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
