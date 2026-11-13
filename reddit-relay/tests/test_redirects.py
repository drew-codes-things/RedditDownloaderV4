import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import worker

IMAGE_BYTES = b"\x89PNG fake image body" * 1000


def start_server(routes):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            status, headers, body = routes.get(self.path, (404, {}, b""))
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            return None

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


@pytest.fixture
def server():
    routes = {}
    httpd, base = start_server(routes)
    yield routes, base
    httpd.shutdown()


@pytest.fixture
def download_dir(tmp_path, monkeypatch):
    target = tmp_path / "downloads"
    target.mkdir()
    monkeypatch.setattr(worker, "DOWNLOAD_DIR", str(target))
    return target


def test_a_redirect_into_the_local_network_is_refused(server, download_dir):
    routes, base = server
    routes["/x.jpg"] = (302, {"Location": "http://192.0.2.20/router.jpg"}, b"")
    target = str(download_dir / "x.jpg")
    with pytest.raises(OSError, match="non-public"):
        worker._fetch_to_file(f"{base}/x.jpg", target)
    assert os.listdir(download_dir) == []


def test_every_hop_is_checked(server, download_dir, monkeypatch):
    routes, base = server
    routes["/one.jpg"] = (302, {"Location": f"{base}/two.jpg"}, b"")
    routes["/two.jpg"] = (302, {"Location": "http://192.0.2.5/three.jpg"}, b"")
    monkeypatch.setattr(worker, "is_public_http_url", lambda url: "192.0.2.5" not in url)
    with pytest.raises(OSError, match="non-public"):
        worker._fetch_to_file(f"{base}/one.jpg", str(download_dir / "one.jpg"))


def test_a_redirect_to_a_public_address_is_followed(server, download_dir, monkeypatch):
    routes, base = server
    routes["/old.png"] = (301, {"Location": f"{base}/new.png"}, b"")
    routes["/new.png"] = (200, {"Content-Type": "image/png"}, IMAGE_BYTES)
    monkeypatch.setattr(worker, "is_public_http_url", lambda url: True)
    target = str(download_dir / "new.png")
    assert worker._fetch_to_file(f"{base}/old.png", target) == target
    assert open(target, "rb").read() == IMAGE_BYTES


def test_a_direct_download_over_the_size_cap_is_dropped(server, download_dir, monkeypatch):
    routes, base = server
    routes["/big.png"] = (200, {}, IMAGE_BYTES)
    monkeypatch.setattr(worker, "MAX_DOWNLOAD_BYTES", 1024)
    with pytest.raises(OSError, match="larger than"):
        worker._fetch_to_file(f"{base}/big.png", str(download_dir / "big.png"))
    assert os.listdir(download_dir) == []


def routed(monkeypatch, url):
    calls = []
    monkeypatch.setattr(worker, "_download_direct_image", lambda link, target: calls.append("direct") or target)
    monkeypatch.setattr(worker, "_download_with_ytdlp", lambda job: calls.append("ytdlp"))
    worker._download({"url": url, "subreddit": "pics", "postId": "p1"})
    return calls


@pytest.mark.parametrize("url, route", [
    ("https://i.redd.it/a.jpg", "direct"),
    ("https://v.redd.it/abc/HLSPlaylist.m3u8", "ytdlp"),
    ("https://v.redd.it/abc/DASH_720.mp4?source=fallback", "ytdlp"),
    ("https://i.imgur.com/abc.gifv", "ytdlp"),
    ("https://www.reddit.com/r/pics/comments/abc/title/", "ytdlp"),
    ("https://media.example.net/clip.mp4", "direct"),
    ("https://media.example.net/photo.JPG", "direct"),
])
def test_links_go_to_the_right_downloader(monkeypatch, download_dir, url, route):
    assert routed(monkeypatch, url) == [route]


def test_other_hosts_never_reach_ytdlp(monkeypatch, download_dir):
    with pytest.raises(ValueError, match="not a supported media host"):
        routed(monkeypatch, "https://attacker.example/page")


def test_ytdlp_downloads_are_capped_in_size(download_dir):
    assert worker._make_ydl_opts("pics", "p1")["max_filesize"] == worker.MAX_DOWNLOAD_BYTES
