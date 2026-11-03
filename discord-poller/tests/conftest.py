import os
import sys

os.environ.setdefault("BOT_TOKEN", "test-bot-token")
os.environ.setdefault("CHANNEL_ID", "111")
os.environ.setdefault("RELAY_API_TOKEN", "test-relay-token")
os.environ["REDDIT_RELAY_ENV_PATH"] = os.devnull
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
