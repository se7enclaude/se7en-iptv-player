"""Relais local : le fournisseur ne voit jamais deux connexions à la fois, même quand le lecteur en ouvre deux."""
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from medialab.stream.relay import Relay

DATA = bytes(range(256)) * 4096  # 1 Mio


def test_relay_keeps_a_single_provider_connection():
    state = {"open": 0, "max": 0, "refused": 0}
    lock = threading.Lock()

    class Strict(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            with lock:
                if state["open"]:
                    state["refused"] += 1
                    self.send_response(403)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                state["open"] += 1
                state["max"] = max(state["max"], state["open"])
            try:
                start = int((self.headers.get("Range") or "bytes=0-")[6:].split("-")[0] or 0)
                self.send_response(206)
                self.send_header("Content-Length", str(len(DATA) - start))
                self.send_header("Content-Range", f"bytes {start}-{len(DATA) - 1}/{len(DATA)}")
                self.send_header("Connection", "close")
                self.end_headers()
                for i in range(start, len(DATA), 4096):
                    self.wfile.write(DATA[i:i + 4096])
                    time.sleep(0.005)
            except OSError:
                pass
            finally:
                with lock:
                    state["open"] -= 1

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Strict)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    r = Relay()
    try:
        url = r.url_for(f"http://127.0.0.1:{srv.server_address[1]}/movie/u/p/7.mkv")
        assert url.startswith("http://127.0.0.1:") and url.endswith("/7.mkv") and "/u/p/" not in url
        first = urllib.request.urlopen(url, timeout=10)
        assert first.read(8192) == DATA[:8192]  # lecture en cours…
        # … le lecteur saute ailleurs sans fermer la première connexion (comme ffmpeg)
        second = urllib.request.urlopen(urllib.request.Request(url, headers={"Range": "bytes=500000-"}), timeout=10)
        assert second.status == 206 and second.headers["Content-Range"].startswith("bytes 500000-")
        assert second.read(4096) == DATA[500000:504096]
        assert state["max"] == 1  # jamais deux connexions chez le fournisseur
        second.close()
        first.close()
        # une clé inconnue ne mène nulle part
        try:
            urllib.request.urlopen(url.rsplit("/", 2)[0] + "/nope/x.mkv", timeout=5)
            raise AssertionError("404 attendu")
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        r.stop()
        srv.shutdown()
