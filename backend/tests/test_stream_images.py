"""Affiches et logos réduits et gardés sur le disque (stream/images.py) : une seule fois téléchargés, jamais agrandis."""
import io
import threading
import time

import pytest
from PIL import Image

from medialab.stream import images


def _jpeg(w, h):
    b = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 40)).save(b, "JPEG")
    return b.getvalue()


def _png_alpha(w, h):
    b = io.BytesIO()
    Image.new("RGBA", (w, h), (0, 0, 0, 0)).save(b, "PNG")
    return b.getvalue()


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(images, "cache_dir", lambda: tmp_path)
    images._failed.clear()
    yield


def test_bucket_rounds_up_to_a_served_width():
    assert images.bucket(10) == 64
    assert images.bucket(216) == 256
    assert images.bucket(384) == 384
    assert images.bucket(5000) == 1280


def test_shrink_reduces_big_posters_and_never_enlarges():
    out = Image.open(io.BytesIO(images.shrink(_jpeg(1000, 1500), 256)))
    assert out.format == "WEBP" and out.size == (256, 384)
    small = Image.open(io.BytesIO(images.shrink(_jpeg(100, 150), 256)))
    assert small.size == (100, 150)


def test_shrink_keeps_logo_transparency():
    out = Image.open(io.BytesIO(images.shrink(_png_alpha(512, 512), 128)))
    assert out.mode == "RGBA" and out.size == (128, 128)


def test_downloaded_once_then_served_from_disk(monkeypatch):
    calls = []
    monkeypatch.setattr(images, "_download", lambda url, ua: calls.append(url) or _jpeg(1000, 1500))
    p1 = images.get("http://img.example/poster.jpg", 200)
    p2 = images.get("http://img.example/poster.jpg", 210)  # même largeur servie (256)
    assert p1 == p2 and p1.exists() and len(calls) == 1
    images.get("http://img.example/poster.jpg", 1000)  # une autre taille : une autre variante
    assert len(calls) == 2


def test_parallel_requests_for_one_image_download_it_once(monkeypatch):
    calls = []

    def slow(url, ua):
        calls.append(url)
        time.sleep(0.2)
        return _jpeg(600, 900)

    monkeypatch.setattr(images, "_download", slow)
    ts = [threading.Thread(target=images.get, args=("http://img.example/a.jpg", 256)) for _ in range(5)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(calls) == 1


def test_broken_image_is_not_asked_again_right_away(monkeypatch):
    calls = []

    def bad(url, ua):
        calls.append(url)
        return b"pas une image"

    monkeypatch.setattr(images, "_download", bad)
    for _ in range(3):
        with pytest.raises(images.ImageError):
            images.get("http://img.example/broken.jpg", 256)
    assert len(calls) == 1


def test_only_web_addresses_are_fetched():
    for url in ("file:///C:/Windows/win.ini", "C:/x.png", "ftp://x/y.png"):
        with pytest.raises(images.ImageError):
            images.get(url, 256)


def test_prune_removes_the_oldest_images_beyond_the_limit(tmp_path):
    for i in range(5):
        p = tmp_path / "ab" / f"{i}.webp"
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(b"x" * 100)
        t = time.time() - 100 + i
        import os
        os.utime(p, (t, t))
    assert images.prune(limit=250) == 3
    assert sorted(p.name for p in tmp_path.rglob("*.webp")) == ["3.webp", "4.webp"]


def test_image_route_serves_small_webp_and_404_for_missing(monkeypatch):
    from fastapi.testclient import TestClient
    from medialab.app import Services, create_app

    monkeypatch.setattr(images, "_download", lambda url, ua: _jpeg(1000, 1500) if "ok" in url else b"")
    with TestClient(create_app(Services(token="t"))) as c:
        r = c.get("/api/stream/img", params={"u": "http://img.example/ok.jpg", "w": 200, "token": "t"})
        assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
        assert "immutable" in r.headers["cache-control"]
        assert Image.open(io.BytesIO(r.content)).size == (256, 384)
        again = c.get("/api/stream/img", params={"u": "http://img.example/ok.jpg", "w": 200, "token": "t"})
        assert again.content == r.content
        assert c.get("/api/stream/img", params={"u": "http://img.example/ko.jpg", "token": "t"}).status_code == 404
        assert c.get("/api/stream/img", params={"u": "http://img.example/ok.jpg"}).status_code == 401  # jeton exigé
