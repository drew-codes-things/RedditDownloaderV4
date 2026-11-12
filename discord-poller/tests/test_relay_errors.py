import json

import pytest
import requests
import responses

import poller

MESSAGES_URL = f"{poller.DISCORD_API_BASE}/channels/{poller.CHANNEL_ID}/messages"
ITEMS = [{"url": "https://i.redd.it/a.jpg", "subreddit": "pics", "postId": "t3_a"}]


def relay_message(message_id):
    return {"id": message_id, "content": "RELAY:" + json.dumps(ITEMS), "webhook_id": "hook"}


@pytest.fixture
def state(tmp_path, monkeypatch):
    state_file = tmp_path / "last_message_id.txt"
    state_file.write_text("40")
    monkeypatch.setattr(poller, "STATE_FILE", str(state_file))
    monkeypatch.setattr(poller, "DELETE_RELAY_MESSAGES", False)
    return state_file


@responses.activate
@pytest.mark.parametrize("status", [401, 403, 429])
def test_auth_or_rate_limit_errors_keep_the_position(state, status):
    responses.get(MESSAGES_URL, json=[relay_message("41"), relay_message("42")])
    responses.post(poller.RELAY_JOBS_URL, status=status, json={"error": "unauthorized"})
    with pytest.raises(requests.HTTPError):
        poller.poll_once()
    assert state.read_text() == "40"
    assert len([call for call in responses.calls if call.request.method == "POST"]) == 1


@responses.activate
def test_the_batch_is_retried_once_the_relay_accepts_again(state):
    responses.get(MESSAGES_URL, json=[relay_message("41")])
    responses.post(poller.RELAY_JOBS_URL, status=401, json={"error": "unauthorized"})
    with pytest.raises(requests.HTTPError):
        poller.poll_once()
    responses.replace(responses.POST, poller.RELAY_JOBS_URL, json={"queued": ["x"], "skipped": []})
    poller.poll_once()
    assert state.read_text() == "41"
    gets = [call for call in responses.calls if call.request.method == "GET"]
    assert [call.request.params.get("after") for call in gets] == ["40", "40"]


@responses.activate
def test_a_bad_request_is_skipped_so_one_broken_batch_cannot_block_the_rest(state):
    responses.get(MESSAGES_URL, json=[relay_message("41"), relay_message("42")])
    responses.post(poller.RELAY_JOBS_URL, status=400, json={"error": "bad"})
    poller.poll_once()
    assert state.read_text() == "42"
