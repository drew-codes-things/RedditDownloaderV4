import os
import secrets
import subprocess
import sys

import pytest

os.environ.setdefault("RELAY_API_TOKEN", "test-relay-token-long-enough-for-the-minimum")

import app

RELAY_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE_FILE = os.path.join(RELAY_DIR, ".env.example")
START_SECONDS = 60


def example_file_token() -> str:
    with open(EXAMPLE_FILE, encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("RELAY_API_TOKEN="):
                return line.split("=", 1)[1].strip()
    raise AssertionError(".env.example has no RELAY_API_TOKEN line")


@pytest.mark.parametrize("raw, problem", [
    (None, "not set"),
    ("", "not set"),
    ("   ", "not set"),
    ("short-token", "at least 32"),
    ("x" * 31, "at least 32"),
    ("change-me-to-a-long-random-string", ".env.example"),
    ("  CHANGE-ME-TO-A-LONG-RANDOM-STRING\n", ".env.example"),
])
def test_unusable_tokens_are_refused(raw, problem):
    with pytest.raises(RuntimeError, match=problem):
        app.checked_relay_token(raw)


def test_a_long_random_token_is_accepted_and_trimmed():
    token = secrets.token_urlsafe(32)
    assert app.checked_relay_token(f" {token}\n") == token
    assert app.checked_relay_token("x" * 32) == "x" * 32


def test_the_refused_placeholder_is_the_one_in_the_example_file():
    with pytest.raises(RuntimeError, match=".env.example"):
        app.checked_relay_token(example_file_token())


@pytest.mark.parametrize("token", ["", "short-token", "change-me-to-a-long-random-string"])
def test_the_relay_refuses_to_start_with_an_unusable_token(token):
    env = {**os.environ, "RELAY_API_TOKEN": token}
    started = subprocess.run([sys.executable, "-c", "import app"], cwd=RELAY_DIR, env=env,
                             capture_output=True, text=True, timeout=START_SECONDS)
    assert started.returncode != 0
    assert "RuntimeError: RELAY_API_TOKEN" in started.stderr
    if token:
        assert token not in started.stderr
