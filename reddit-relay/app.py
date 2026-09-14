import logging
import os
import secrets
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, jsonify, request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

import worker  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("reddit_relay.app")

RELAY_API_TOKEN = (os.getenv("RELAY_API_TOKEN") or "").strip()
if not RELAY_API_TOKEN:
    raise RuntimeError("RELAY_API_TOKEN is not set - refusing to start without an API token")

MAX_ITEMS_PER_REQUEST = int(os.getenv("MAX_ITEMS_PER_REQUEST", "200"))

app = Flask(__name__)

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Authorization, Content-Type",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
}


@app.after_request
def add_cors_headers(response):
    for header, value in CORS_HEADERS.items():
        response.headers[header] = value
    return response


@app.route("/jobs", methods=["OPTIONS"])
@app.route("/jobs/<job_id>", methods=["OPTIONS"])
def cors_preflight(job_id=None):
    return "", 204


def requires_bearer_token(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        prefix = "Bearer "
        token = auth_header[len(prefix):] if auth_header.startswith(prefix) else ""
        if not token or not secrets.compare_digest(token, RELAY_API_TOKEN):
            return jsonify(error="unauthorized"), 401
        return view_func(*args, **kwargs)

    return wrapped


@app.get("/health")
def health():
    return jsonify(status="ok")


@app.post("/jobs")
@requires_bearer_token
def create_jobs():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="request body must be a JSON object"), 400

    items = body.get("items")
    if not isinstance(items, list) or not items:
        return jsonify(error="'items' must be a non-empty array"), 400
    if len(items) > MAX_ITEMS_PER_REQUEST:
        return jsonify(error=f"too many items; max {MAX_ITEMS_PER_REQUEST} per request"), 400

    job_ids = []
    skipped = []
    for index, item in enumerate(items):
        if not isinstance(item, dict) or not isinstance(item.get("url"), str) or not item["url"].strip():
            skipped.append({"index": index, "reason": "missing or invalid 'url'"})
            continue
        job_id = worker.enqueue(
            url=item["url"].strip(),
            subreddit=item.get("subreddit") if isinstance(item.get("subreddit"), str) else None,
            post_id=item.get("postId") if isinstance(item.get("postId"), str) else None,
        )
        job_ids.append(job_id)

    logger.info("queued %d job(s), skipped %d", len(job_ids), len(skipped))
    return jsonify(queued=job_ids, skipped=skipped), 202


@app.get("/jobs")
@requires_bearer_token
def list_jobs():
    return jsonify(jobs=worker.list_jobs())


@app.get("/jobs/<job_id>")
@requires_bearer_token
def get_job(job_id):
    job = worker.get_job(job_id)
    if job is None:
        return jsonify(error="job not found"), 404
    return jsonify(job)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
