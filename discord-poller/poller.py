import json
import logging
import os
import time

import requests
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))
REDDIT_RELAY_ENV_PATH = os.path.expanduser(
    os.getenv("REDDIT_RELAY_ENV_PATH", "~/Downloads/reddit-relay/.env")
)
load_dotenv(REDDIT_RELAY_ENV_PATH)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("discord_poller")

BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
CHANNEL_ID = (os.getenv("CHANNEL_ID") or "").strip()
RELAY_API_TOKEN = (os.getenv("RELAY_API_TOKEN") or "").strip()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set - refusing to start")
if not CHANNEL_ID:
    raise RuntimeError("CHANNEL_ID is not set - refusing to start")
if not RELAY_API_TOKEN:
    raise RuntimeError("RELAY_API_TOKEN is not set - refusing to start")

DISCORD_API_BASE = "https://discord.com/api/v10"
RELAY_JOBS_URL = os.getenv("RELAY_JOBS_URL", "http://localhost:5000/jobs")
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "10"))
RELAY_MESSAGE_PREFIX = "RELAY:"
STATE_FILE = os.path.join(BASE_DIR, "last_message_id.txt")

DISCORD_HEADERS = {"Authorization": f"Bot {BOT_TOKEN}"}
RELAY_HEADERS = {
    "Authorization": f"Bearer {RELAY_API_TOKEN}",
    "Content-Type": "application/json",
}


def load_last_message_id() -> str | None:
    if not os.path.exists(STATE_FILE):
        return None
    with open(STATE_FILE, encoding="utf-8") as state_file:
        return state_file.read().strip() or None


def save_last_message_id(message_id: str) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as state_file:
        state_file.write(message_id)


def fetch_new_messages(after_id: str | None) -> list[dict]:
    params = {"limit": 100}
    if after_id is not None:
        params["after"] = after_id

    response = requests.get(
        f"{DISCORD_API_BASE}/channels/{CHANNEL_ID}/messages",
        headers=DISCORD_HEADERS,
        params=params,
        timeout=15,
    )
    if response.status_code == 429:
        retry_after = response.json().get("retry_after", 1)
        logger.warning("rate limited by Discord; sleeping %.1fs", retry_after)
        time.sleep(retry_after)
        return []
    response.raise_for_status()

    return sorted(response.json(), key=lambda msg: int(msg["id"]))


def forward_to_relay(items: list[dict]) -> None:
    response = requests.post(
        RELAY_JOBS_URL, headers=RELAY_HEADERS, json={"items": items}, timeout=15
    )
    if not response.ok:
        logger.error(
            "reddit-relay rejected items: HTTP %d %s", response.status_code, response.text
        )
        return
    body = response.json()
    logger.info(
        "forwarded %d item(s): queued=%d skipped=%d",
        len(items),
        len(body.get("queued", [])),
        len(body.get("skipped", [])),
    )


def process_message(message: dict) -> None:
    content = message.get("content", "")
    if not content.startswith(RELAY_MESSAGE_PREFIX):
        return

    try:
        items = json.loads(content[len(RELAY_MESSAGE_PREFIX) :])
    except json.JSONDecodeError as err:
        logger.error("message %s: invalid RELAY payload: %s", message["id"], err)
        return

    if not isinstance(items, list) or not items:
        logger.error("message %s: RELAY payload is not a non-empty array", message["id"])
        return

    forward_to_relay(items)


def poll_once() -> None:
    last_message_id = load_last_message_id()
    messages = fetch_new_messages(last_message_id)

    for message in messages:
        process_message(message)
        save_last_message_id(message["id"])


def main() -> None:
    logger.info("discord-poller starting; channel=%s", CHANNEL_ID)
    while True:
        try:
            poll_once()
        except requests.RequestException as err:
            logger.error("poll failed: %s", err)
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
