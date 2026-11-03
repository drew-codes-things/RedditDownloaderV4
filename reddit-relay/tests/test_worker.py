import os
import threading
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler

import pytest

import worker

IMAGE_BYTES = b"\x89PNG fake image body" * 1000


@pytest.fixture
def image_server(tmp_path):
    (tmp_path / "served").mkdir()
    (tmp_path / "served" / "abc123.png").write_bytes(IMAGE_BYTES)
    handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path / "served"))
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture
def download_dir(tmp_path, monkeypatch):
    target = tmp_path / "downloads"
    monkeypatch.setattr(worker, "DOWNLOAD_DIR", str(target))
    return target


def test_reddit_image_hosts_are_direct_downloads():
    assert worker._is_direct_image_url("https://preview.redd.it/p2b5skl6bjph1.jpeg?auto=webp&s=abc")
    assert worker._is_direct_image_url("https://i.redd.it/x.gif")
    assert not worker._is_direct_image_url("https://v.redd.it/abcdef")


def test_direct_image_path_uses_subreddit_post_and_file_name(download_dir):
    path = worker._direct_image_path("pics", "https://preview.redd.it/p2b5skl6bjph1.jpeg?auto=webp&s=abc", "t3_xyz")
    assert path == os.path.join(str(download_dir), "pics", "t3_xyz_p2b5skl6bjph1.jpeg")


def test_direct_image_is_downloaded_whole(image_server, download_dir):
    download_dir.mkdir()
    target = str(download_dir / "abc.png")
    assert worker._download_direct_image(f"{image_server}/abc123.png", target) == target
    assert open(target, "rb").read() == IMAGE_BYTES
    assert not os.path.exists(target + ".part")


def test_failed_image_download_leaves_nothing_behind(image_server, download_dir):
    download_dir.mkdir()
    target = str(download_dir / "missing.png")
    with pytest.raises(Exception):
        worker._download_direct_image(f"{image_server}/missing.png", target)
    assert os.listdir(download_dir) == []


def test_existing_image_is_not_downloaded_again(download_dir):
    download_dir.mkdir()
    target = download_dir / "kept.png"
    target.write_bytes(b"already here")
    assert worker._download_direct_image("http://127.0.0.1:9/never-called.png", str(target)) == str(target)
    assert target.read_bytes() == b"already here"


def test_image_jobs_skip_ytdlp(monkeypatch, download_dir):
    calls = []
    monkeypatch.setattr(worker, "_download_direct_image", lambda url, target: calls.append(("image", target)) or target)
    monkeypatch.setattr(worker, "_download_with_ytdlp", lambda job: calls.append(("ytdlp", job["url"])))
    worker._download({"url": "https://i.redd.it/a.jpg", "subreddit": "pics", "postId": "p1"})
    worker._download({"url": "https://v.redd.it/vid", "subreddit": "pics", "postId": "p2"})
    assert [kind for kind, _ in calls] == ["image", "ytdlp"]


def test_preview_links_try_the_original_first():
    url = "https://preview.redd.it/ri2z96do9jph1.gif?s=abc"
    assert worker._image_candidates(url) == ["https://i.redd.it/ri2z96do9jph1.gif", url]
    assert worker._image_candidates("https://i.redd.it/a.jpg") == ["https://i.redd.it/a.jpg"]


def test_falls_back_to_the_next_candidate(image_server, download_dir, monkeypatch):
    download_dir.mkdir()
    monkeypatch.setattr(worker, "_image_candidates",
                        lambda url: [f"{image_server}/gone.png", f"{image_server}/abc123.png"])
    target = str(download_dir / "fallback.png")
    worker._download_direct_image("https://preview.redd.it/abc123.png", target)
    assert open(target, "rb").read() == IMAGE_BYTES
