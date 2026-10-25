"""Lecture d'un guide XMLTV en flux (fichiers de plusieurs centaines de Mo, compressés ou non).

    <tv><channel id="tf1.fr"><display-name>TF1</display-name><icon src="…"/></channel>
        <programme start="20261025200000 +0100" stop="20261025204500 +0100" channel="tf1.fr">
          <title>Journal</title><desc>…</desc><category>Info</category></programme></tv>
"""
from __future__ import annotations

import datetime as dt
import gzip
import io
import re
import xml.etree.ElementTree as ET
from typing import IO, Iterator

from ..errors import MediaLabError

_TIME = re.compile(r"^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})?\s*([+-]\d{2}):?(\d{2})?")


def parse_time(s: str | None) -> int | None:
    """« 20261025200000 +0100 » → horodatage UTC. Sans fuseau : UTC. Date impossible : None."""
    try:
        return _parse_time(s)
    except (ValueError, OverflowError):
        return None


def _parse_time(s: str | None) -> int | None:
    m = _TIME.match((s or "").strip())
    if not m:
        m2 = re.match(r"^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})?", (s or "").strip())
        if not m2:
            return None
        y, mo, d, h, mi, se = (int(x or 0) for x in m2.groups())
        return int(dt.datetime(y, mo, d, h, mi, se, tzinfo=dt.timezone.utc).timestamp())
    y, mo, d, h, mi = (int(x) for x in m.groups()[:5])
    se = int(m.group(6) or 0)
    off_h = int(m.group(7))
    off_m = int(m.group(8) or 0) * (1 if off_h >= 0 and not m.group(7).startswith("-") else -1)
    tz = dt.timezone(dt.timedelta(hours=off_h, minutes=off_m))
    return int(dt.datetime(y, mo, d, h, mi, se, tzinfo=tz).timestamp())


def open_stream(raw: IO[bytes]) -> IO[bytes]:
    """Décompresse à la volée si le fichier est en gzip (même si le serveur ne le dit pas)."""
    head = raw.read(2)
    rest = io.BufferedReader(_Prepend(head, raw))  # type: ignore[arg-type]
    return gzip.GzipFile(fileobj=rest) if head == b"\x1f\x8b" else rest


class _Prepend(io.RawIOBase):
    def __init__(self, head: bytes, raw: IO[bytes]) -> None:
        self.head, self.raw = head, raw

    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:  # type: ignore[override]
        if self.head:
            n = min(len(b), len(self.head))
            b[:n] = self.head[:n]
            self.head = self.head[n:]
            return n
        data = self.raw.read(len(b))
        n = len(data)
        b[:n] = data
        return n


_BARE_AMP = re.compile(rb"&(?!(?:[A-Za-z][A-Za-z0-9]{0,31}|#[0-9]{1,7}|#x[0-9A-Fa-f]{1,6});)")
_BAD_CHARS = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class _Repair(io.RawIOBase):
    """Corrige au vol les deux fautes les plus courantes des guides IPTV : « & » non échappé
    (« Tom & Jerry ») et caractères de contrôle interdits en XML."""

    def __init__(self, raw: IO[bytes]) -> None:
        self.raw, self.tail, self.out = raw, b"", b""

    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:  # type: ignore[override]
        while not self.out:
            chunk = self.raw.read(1 << 16)
            data = self.tail + chunk
            if chunk:
                # on garde la fin si un « & » n'a pas encore assez de suite pour être jugé
                amp = data.rfind(b"&", max(0, len(data) - 40))
                cut = amp if amp >= 0 and b";" not in data[amp:] and len(data) - amp < 40 else len(data)
                data, self.tail = data[:cut], data[cut:]
            else:
                self.tail = b""
            self.out = _BAD_CHARS.sub(b"", _BARE_AMP.sub(b"&amp;", data))
            if not chunk:
                break
        n = min(len(b), len(self.out))
        b[:n] = self.out[:n]
        self.out = self.out[n:]
        return n


class Broken(MediaLabError):
    """Le guide est illisible à partir d'un endroit : ce qui a été lu avant reste valable."""


def _text(el: ET.Element | None) -> str:
    return (el.text or "").strip() if el is not None else ""


def parse(raw: IO[bytes], since: int | None = None, until: int | None = None) -> Iterator[tuple[str, dict]]:
    """Donne ("channel", {...}) et ("programme", {...}) au fil de la lecture. ``since`` / ``until`` : fenêtre gardée."""
    try:
        yield from _parse(io.BufferedReader(_Repair(open_stream(raw))), since, until)
    except ET.ParseError as e:
        raise Broken("epg_invalid", "Le guide des programmes de ce fournisseur est mal formé.",
                     "Les programmes lus avant l'erreur sont gardés.", detail=str(e)) from e


def _parse(stream: IO[bytes], since: int | None, until: int | None) -> Iterator[tuple[str, dict]]:
    for _event, el in ET.iterparse(stream, events=("end",)):
        tag = el.tag
        if tag == "channel":
            icon = el.find("icon")
            yield "channel", {"id": el.get("id", ""), "names": [_text(n) for n in el.findall("display-name") if _text(n)],
                              "icon": icon.get("src", "") if icon is not None else ""}
            el.clear()
        elif tag == "programme":
            start, stop = parse_time(el.get("start")), parse_time(el.get("stop"))
            if start and (since is None or (stop or start) >= since) and (until is None or start <= until):
                yield "programme", {"channel": el.get("channel", ""), "start": start, "stop": stop or start + 1800,
                                    "title": _text(el.find("title")), "desc": _text(el.find("desc")),
                                    "category": _text(el.find("category"))}
            el.clear()
