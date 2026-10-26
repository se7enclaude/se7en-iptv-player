"""Recherche intelligente : tolère les fautes de frappe et les accents, cherche aussi par acteur, réalisateur, genre
et catégorie, et classe les résultats (titre exact, début du titre, tous les mots, mot approché…).

« spidrman » trouve « Spider-Man », « jean reno » trouve ses films, « comedie » les comédies. Un index de trigrammes par
abonnement et par type est gardé en mémoire et refait après chaque mise à jour du catalogue.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

from . import catalog
from .store import Store, norm

KINDS = ("live", "movie", "series")
_lock = threading.Lock()
_cache: dict[tuple[str, str], "_Index"] = {}


@dataclass
class _Index:
    stamp: float
    ids: list[str] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    words: list[list[str]] = field(default_factory=list)
    grams: dict[str, list[int]] = field(default_factory=dict)


def _trigrams(s: str) -> set[str]:
    s = f"  {s} "
    return {s[i:i + 3] for i in range(len(s) - 2)}


def _clean(s: str) -> str:
    """norm() puis sans ponctuation : « spider-man » et « spiderman » se rapprochent."""
    return " ".join("".join(ch if ch.isalnum() or ch == "+" else " " for ch in norm(s)).split())


def _index(store: Store, aid: str, typ: str) -> _Index:
    acc = store.one("SELECT synced FROM accounts WHERE id=?", (aid,)) or {}
    stamp = acc.get("synced") or 0
    with _lock:
        idx = _cache.get((aid, typ))
        if idx and idx.stamp == stamp:
            return idx
    idx = _Index(stamp)
    for k, r in enumerate(store.q(f"SELECT id, name FROM {catalog.TABLE[typ]} WHERE account=?", (aid,))):
        n = _clean(r["name"])
        idx.ids.append(r["id"])
        idx.names.append(n)
        idx.words.append(n.split())
        for g in _trigrams(n.replace(" ", "")) | _trigrams(n):
            idx.grams.setdefault(g, []).append(k)
    with _lock:
        _cache[(aid, typ)] = idx
    return idx


def warm(store: Store, aid: str) -> None:
    """Prépare l'index de recherche d'un compte (au lancement et après une mise à jour du catalogue) : la première
    recherche ne prend plus 2 à 3 s sur un gros catalogue."""
    for typ in catalog.TABLE:
        _index(store, aid, typ)


def _close(a: str, b: str, limit: int) -> bool:
    """Distance d'édition ≤ limit (avec arrêt rapide)."""
    if abs(len(a) - len(b)) > limit:
        return False
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        if min(cur) > limit:
            return False
        prev = cur
    return prev[-1] <= limit


def _word_hit(q: str, words: list[str], joined: str) -> float:
    """1 : le mot est là (ou en début de mot) ; 0.7 : à une ou deux fautes près ; 0 : absent."""
    if any(w.startswith(q) for w in words) or (len(q) >= 3 and q in joined):
        return 1.0
    if len(q) >= 4:
        lim = 1 if len(q) < 7 else 2
        if any(_close(q, w, lim) or (len(w) > len(q) and _close(q, w[:len(q)], lim)) for w in words):
            return 0.7
        # mots collés ou coupés : « spidrman » ↔ « spider man »
        glued = joined.replace(" ", "")
        if _close(q, glued[:len(q) + 1], lim) or _close(q, glued[:len(q)], lim):
            return 0.7
    return 0.0


def rank(store: Store, aid: str, typ: str, query: str, limit: int = 60) -> list[tuple[str, float, str]]:
    """[(id, score, raison)] du plus pertinent au moins pertinent."""
    q = _clean(query)
    if not q:
        return []
    qwords = q.split()
    idx = _index(store, aid, typ)
    qgrams = _trigrams(q.replace(" ", "")) | _trigrams(q)
    counts: dict[int, int] = {}
    for g in qgrams:
        for k in idx.grams.get(g, ()):
            counts[k] = counts.get(k, 0) + 1
    need = max(1, int(len(qgrams) * (0.3 if len(q) < 5 else 0.45)))
    # les noms qui partagent le plus de trigrammes d'abord ; au-delà de 4000 candidats le reste ne serait pas montré
    cands = sorted((kc for kc in counts.items() if kc[1] >= need), key=lambda kc: -kc[1])[:4000]
    scored: dict[str, tuple[float, str]] = {}
    for k, c in cands:
        name, words = idx.names[k], idx.words[k]
        if name == q:
            s = 100.0
        elif name.startswith(q):
            s = 92.0
        else:
            hits = [_word_hit(w, words, name) for w in qwords]
            if not all(hits):
                continue
            s = 60.0 + 25.0 * (sum(hits) / len(hits)) + (5.0 if words and words[0].startswith(qwords[0]) else 0.0)
        s += min(5.0, c / max(1, len(qgrams)) * 5)  # départage : plus de trigrammes en commun
        scored[idx.ids[k]] = (s, "")
    # acteurs, réalisateur, genre (fiches déjà vues et listes de séries)
    if typ != "live" and len(q) >= 3:
        like = f"%{q}%"
        for r in store.q("SELECT id, genre, actors, director FROM people WHERE account=? AND type=? AND "
                         "(actors LIKE ? OR director LIKE ? OR genre LIKE ?) LIMIT 400", (aid, typ, like, like, like)):
            why = ("Avec " + query.strip().title()) if q in (r["actors"] or "") else (
                "Réalisé par " + query.strip().title()) if q in (r["director"] or "") else "Genre : " + query.strip().lower()
            base = 70.0 if not why.startswith("Genre") else 55.0
            if scored.get(r["id"], (0, ""))[0] < base:
                scored[r["id"]] = (base, why)
    # catégorie (« sport », « action », « jeunesse »…)
    if len(q) >= 4:
        cats = store.q("SELECT id, name FROM categories WHERE account=? AND type=?", (aid, typ))
        hit = [c for c in cats if q in _clean(c["name"])]
        if hit:
            for r in store.q(f"SELECT id, category FROM {catalog.TABLE[typ]} WHERE account=? AND category IN "
                             f"({','.join('?' * len(hit))}) ORDER BY position LIMIT 300", [aid, *[c["id"] for c in hit]]):
                if r["id"] not in scored:
                    name = next(c["name"] for c in hit if c["id"] == r["category"])
                    scored[r["id"]] = (40.0, f"Catégorie {name}")
    best = sorted(scored.items(), key=lambda kv: -kv[1][0])[: limit * 3]
    return [(i, s, why) for i, (s, why) in best]


def search(store: Store, aid: str, profile: str, show_adult: bool, query: str, quality: str, limit: int = 30) -> dict:
    out: dict = {}
    for typ in KINDS:
        ranked = rank(store, aid, typ, query, limit)
        if not ranked:
            out[typ] = []
            continue
        order = {i: n for n, (i, _s, _w) in enumerate(ranked)}
        why = {i: w for i, _s, w in ranked}
        ids = list(order)
        if quality:  # le meilleur rang d'un groupe SD / HD / 4K vaut pour tout le groupe
            for i in list(ids):
                for v in catalog.group_of(store, aid, typ, i):
                    if v not in order:
                        order[v], why[v] = order[i], why[i]
                        ids.append(v)
        rows = catalog.items(store, aid, typ, profile, show_adult, "__all", "", 0, len(ids), quality=quality, only=ids)["items"]
        rows.sort(key=lambda r: min([order.get(r["id"], 1e9)] + [order.get(v["id"], 1e9) for v in r.get("variants") or []]))
        for r in rows[:limit]:
            r["why"] = why.get(r["id"]) or next((why[v["id"]] for v in r.get("variants") or [] if why.get(v["id"])), "")
        out[typ] = rows[:limit]
    out["programmes"] = catalog.search_all(store, aid, profile, show_adult, query, limit, programmes_only=True)["programmes"]
    return out

