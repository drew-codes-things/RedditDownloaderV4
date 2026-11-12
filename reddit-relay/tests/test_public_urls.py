import socket

import pytest

import worker

PUBLIC_ADDRESS = "151.101.1.140"
PRIVATE_ADDRESS = "192.168.1.20"


def resolve_to(address):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))]

    return fake_getaddrinfo


@pytest.mark.parametrize("url", [
    "http://192.168.1.1/cgi-bin/reboot.jpg",
    "http://127.0.0.1:5000/jobs.mp4",
    "http://[::1]/a.png",
    "http://169.254.169.254/latest/meta-data.gif",
    "http://10.0.0.5/x.mp4",
    "file:///etc/passwd",
    "ytsearch:anything.mp4",
    "ftp://example.com/a.jpg",
    "https:///no-host.jpg",
])
def test_private_and_non_http_urls_are_not_public(url):
    assert not worker.is_public_http_url(url)


def test_host_name_resolving_to_a_private_address_is_not_public(monkeypatch):
    monkeypatch.setattr(worker.socket, "getaddrinfo", resolve_to(PRIVATE_ADDRESS))
    assert not worker.is_public_http_url("https://router.example.com/a.jpg")


def test_host_name_that_does_not_resolve_is_not_public(monkeypatch):
    def failing_getaddrinfo(host, port, *args, **kwargs):
        raise socket.gaierror("no such host")

    monkeypatch.setattr(worker.socket, "getaddrinfo", failing_getaddrinfo)
    assert not worker.is_public_http_url("https://nowhere.invalid/a.jpg")


def test_public_host_is_public(monkeypatch):
    monkeypatch.setattr(worker.socket, "getaddrinfo", resolve_to(PUBLIC_ADDRESS))
    assert worker.is_public_http_url("https://i.redd.it/a.jpg")


def test_job_for_a_private_address_fails_without_downloading(monkeypatch):
    downloads = []
    monkeypatch.setattr(worker, "_download", lambda job: downloads.append(job["url"]))
    job_id = "job-private"
    monkeypatch.setitem(worker._jobs, job_id, {
        "id": job_id,
        "url": "http://192.168.1.1/cgi-bin/reboot.jpg",
        "subreddit": "pics",
        "postId": "t3_a",
        "status": "queued",
        "queuedAt": 0,
    })
    worker._run_job(job_id)
    assert downloads == []
    assert worker._jobs[job_id]["status"] == "error"
    assert "public" in worker._jobs[job_id]["error"]
