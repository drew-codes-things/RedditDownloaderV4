import os

os.environ.setdefault("RELAY_API_TOKEN", "test-relay-token")

import app


def test_relay_answers_without_cross_origin_headers():
    client = app.app.test_client()
    reply = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in reply.headers
    preflight = client.open("/jobs", method="OPTIONS", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in preflight.headers
