"""Lecture des listes M3U / M3U8 (« #EXTM3U »), ligne par ligne : une liste de 100 000 entrées ne charge pas tout en mémoire.

    #EXTINF:-1 tvg-id="tf1.fr" tvg-name="TF1" tvg-logo="http://…/tf1.png" group-title="France" catchup="default",TF1 HD
    #EXTVLCOPT:http-user-agent=…
    http://serveur/live/user/pass/1.ts
"""
from __future__ import annotations

import hashlib
import re
from typing import Iterable, Iterator

_ATTR = re.compile(r'([A-Za-z0-9_-]+)="([^"]*)"')
_HEAD = re.compile(r"^#EXTINF:\s*(-?\d+(?:\.\d+)?)?(.*)$", re.I)


def _split_title(rest: str) -> tuple[str, str]:
    """Sépare « attributs,titre » ; la virgule peut apparaître dans les attributs entre guillemets."""
    in_quote = False
    for i, ch in enumerate(rest):
        if ch == '"':
            in_quote = not in_quote
        elif ch == "," and not in_quote:
            return rest[:i], rest[i + 1:].strip()
    return rest, ""


def kind_of(url: str) -> str:
    u = url.lower()
    if "/movie/" in u:
        return "movie"
    if "/series/" in u:
        return "episode"
    if re.search(r"\.(mp4|mkv|avi|mov|m4v|wmv|flv|webm)(\?|$)", u):
        return "movie"
    return "live"


def entry_id(url: str) -> str:
    """Identifiant stable d'une entrée (l'URL complète, hachée : pas d'identifiants en clair dans la base)."""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def parse(lines: Iterable[str | bytes]) -> Iterator[dict]:
    pending: dict | None = None
    opts: dict[str, str] = {}
    group_override = ""
    for raw in lines:
        line = (raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw).strip().lstrip("﻿")
        if not line:
            continue
        if line.upper().startswith("#EXTINF"):
            m = _HEAD.match(line)
            attrs_part, title = _split_title(m.group(2) if m else line[8:])
            attrs = {k.lower(): v.strip() for k, v in _ATTR.findall(attrs_part)}
            pending = {"name": title or attrs.get("tvg-name") or "Sans nom", "tvg_id": attrs.get("tvg-id", ""),
                       "tvg_name": attrs.get("tvg-name", ""), "logo": attrs.get("tvg-logo", ""),
                       "group": attrs.get("group-title", ""), "chno": attrs.get("tvg-chno", ""),
                       "catchup": attrs.get("catchup", attrs.get("catchup-type", "")),
                       "catchup_days": attrs.get("catchup-days", attrs.get("timeshift", "")),
                       "catchup_source": attrs.get("catchup-source", "")}
            opts = {}
            group_override = ""
        elif line.upper().startswith("#EXTGRP:"):
            group_override = line[8:].strip()
        elif line.upper().startswith("#EXTVLCOPT:"):
            k, _, v = line[11:].partition("=")
            opts[k.strip().lower()] = v.strip()
        elif line.startswith("#"):
            continue
        elif pending is not None:
            if group_override and not pending["group"]:
                pending["group"] = group_override
            pending["url"] = line
            pending["kind"] = kind_of(line)
            pending["id"] = entry_id(line)
            if opts:
                pending["opts"] = dict(opts)
            yield pending
            pending = None
