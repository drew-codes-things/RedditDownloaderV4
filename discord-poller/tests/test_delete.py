import json

import pytest
import responses

import poller

RELAY_ITEMS = [{"url": "https://i.redd.it/a.jpg", "subreddit": "pics", "postId": "t3_a"}]
DELETE_URL = f"{poller.DISCORD_API_BASE}/channels/{poller.CHANNEL_ID}/messages/42"


def relay_message(content=None, webhook_id="hook"):
    return {"id": "42", "content": content or "RELAY:" + json.dumps(RELAY_ITEMS), "webhook_id": webhook_id}


@pytest.fixture(autouse=True)
def reset_warning(monkeypatch):
    monkeypatch.setattr(poller, "_warned_missing_permission", False)
    monkeypatch.setattr(poller, "DELETE_RELAY_MESSAGES", True)


@responses.activate
def test_message_is_deleted_after_the_relay_accepts_it():
    responses.post(poller.RELAY_JOBS_URL, json={"queued": ["x"], "skipped": []})
    responses.delete(DELETE_URL, status=204)
    poller.process_message(relay_message())
    assert [call.request.method for call in responses.calls] == ["POST", "DELETE"]


@responses.activate
def test_message_is_kept_when_the_relay_rejects_it():
    responses.post(poller.RELAY_JOBS_URL, status=400, json={"error": "bad"})
    poller.process_message(relay_message())
    assert [call.request.method for call in responses.calls] == ["POST"]


@responses.activate
def test_non_relay_and_foreign_messages_are_never_deleted():
    poller.process_message({"id": "42", "content": "hello", "webhook_id": "hook"})
    poller.process_message(relay_message(webhook_id=None))
    assert len(responses.calls) == 0


@responses.activate
def test_deleting_can_be_turned_off(monkeypatch):
    monkeypatch.setattr(poller, "DELETE_RELAY_MESSAGES", False)
    responses.post(poller.RELAY_JOBS_URL, json={"queued": ["x"], "skipped": []})
    poller.process_message(relay_message())
    assert [call.request.method for call in responses.calls] == ["POST"]


@responses.activate
def test_missing_permission_warns_once(caplog):
    responses.delete(DELETE_URL, status=403)
    assert poller.delete_relay_message("42") is False
    assert poller.delete_relay_message("42") is False
    assert caplog.text.count("Manage Messages") == 1


@responses.activate
def test_already_deleted_counts_as_done():
    responses.delete(DELETE_URL, status=404)
    assert poller.delete_relay_message("42") is True


@responses.activate
def test_rate_limit_is_retried_once(monkeypatch):
    monkeypatch.setattr(poller.time, "sleep", lambda seconds: None)
    responses.delete(DELETE_URL, status=429, json={"retry_after": 0.1})
    responses.delete(DELETE_URL, status=204)
    assert poller.delete_relay_message("42") is True
