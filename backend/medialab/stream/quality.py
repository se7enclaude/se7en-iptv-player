"""Versions d'une même chaîne ou d'un même film : « TF1 SD », « TF1 HD », « TF1 FHD », « TF1 4K » → un seul groupe.

Le nom, sans ses marques de qualité, donne la clé du groupe ; la marque la plus haute donne la qualité (4K > FHD > HD > SD,
0 = non précisée). Calculé après chaque mise à jour du catalogue dans la table ``variants`` ; les listes n'affichent
qu'une version par groupe (celle de la qualité préférée) et proposent les autres.
"""
from __future__ import annotations

import re

from .store import Store, norm

# (libellé, rang, jetons) — un jeton est un mot entier du nom, une fois mis en forme par norm()
LEVELS = (
    ("4K", 4, {"4k", "uhd", "2160", "2160p", "8k"}),
    ("FHD", 3, {"fhd", "1080", "1080p", "1080i", "fullhd"}),
    ("HD", 2, {"hd", "720", "720p", "hdtv", "hd+"}),
    ("SD", 1, {"sd", "480", "480p", "576", "576p", "lq"}),
)
# jetons techniques retirés de la clé sans changer la qualité
NOISE = {"hevc", "h265", "h.265", "x265", "h264", "x264", "hdr", "hdr10", "50fps", "60fps", "25fps", "fps", "raw", "backup",
         "(backup)", "vip", "ᴴᴰ"}
# « Full HD », « Ultra HD » écrits en deux mots
PAIRS = {("full", "hd"): "fullhd", ("ultra", "hd"): "uhd"}
SPLIT = re.compile(r"[\s\[\]\(\)\{\}:|/·•\-_,.]+")
TABLE = {"live": "channels", "movie": "vod", "series": "series"}
TARGET = {"best": None, "4k": 4, "fhd": 3, "hd": 2, "sd": 1}


def parse(name: str) -> tuple[str, str, int]:
    """« FR | TF1 FHD (HEVC) » → (« fr tf1 », « FHD », 3)."""
    words = [w for w in SPLIT.split(norm(name).lower()) if w]  # lower() again: « ᴴᴰ » only becomes « HD » in norm()
    merged: list[str] = []
    for w in words:
        if merged and (merged[-1], w) in PAIRS:
            merged[-1] = PAIRS[(merged[-1], w)]
        else:
            merged.append(w)
    label, rank, keep = "", 0, []
    for w in merged:
        hit = next(((lab, r) for lab, r, toks in LEVELS if w in toks), None)
        if hit:
            if hit[1] > rank:
                label, rank = hit
            continue
        if w in NOISE:
            continue
        keep.append(w)
    key = " ".join(keep) or norm(name)
    return key, label, rank


def _order(pref: str):
    """Clé de tri : la version préférée d'un groupe d'abord (« best » : la plus haute qualité ; « hd » : la HD, sinon
    la plus proche, en dessous d'abord ; qualité non précisée en dernier)."""
    target = TARGET.get(pref)

    def key(r: dict) -> tuple:
        rank = r["rank"]
        near = 0 if target is None else (9 if rank == 0 else abs(rank - target) * 2 + (rank > target))
        return near, -rank, r["position"] or 0
    return key


def index(store: Store, aid: str, typ: str | None = None, pref: str | None = None) -> int:
    """(Re)calcule les groupes d'un compte et la version à montrer de chaque groupe : ``rep_all`` dans les listes
    complètes et la recherche, ``rep_cat`` dans une catégorie (où les versions sont parfois rangées à part :
    « FR HD », « FR 4K »). Renvoie le nombre d'éléments qui ont au moins une autre version."""
    pref = pref or store.get_meta("quality_pref_index", "best") or "best"
    order = _order(pref)
    grouped = 0
    for t in ([typ] if typ else list(TABLE)):
        rows = store.q(f"SELECT id, name, category, position FROM {TABLE[t]} WHERE account=?", (aid,))
        by_key: dict[str, list[dict]] = {}
        by_cat: dict[tuple, list[dict]] = {}
        for r in rows:
            r["key"], r["label"], r["rank"] = parse(r["name"])
            by_key.setdefault(r["key"], []).append(r)
            by_cat.setdefault((r["key"], r["category"]), []).append(r)
        rep_all = {min(g, key=order)["id"] for g in by_key.values()}
        rep_cat = {min(g, key=order)["id"] for g in by_cat.values()}
        # un groupe garde la place de sa première version dans la liste du fournisseur (pas celle de la 4K, souvent à la fin)
        pos_all = {k: min(r["position"] or 0 for r in g) for k, g in by_key.items()}
        pos_cat = {k: min(r["position"] or 0 for r in g) for k, g in by_cat.items()}
        out = [(aid, t, r["id"], r["key"], r["label"], r["rank"], int(r["id"] in rep_all), int(r["id"] in rep_cat),
                pos_all[r["key"]], pos_cat[(r["key"], r["category"])]) for r in rows]
        with store.write_lock:
            con = store.conn()
            con.execute("BEGIN")
            con.execute("DELETE FROM variants WHERE account=? AND type=?", (aid, t))
            con.executemany("INSERT OR REPLACE INTO variants VALUES (?,?,?,?,?,?,?,?,?,?)", out)
            con.commit()
        grouped += sum(len(g) for g in by_key.values() if len(g) > 1)
    return grouped


def ensure(store: Store, aid: str, pref: str) -> None:
    """Groupes calculés au premier affichage (catalogue synchronisé avant cette version) et refaits quand la qualité
    préférée change."""
    if (store.get_meta("quality_pref_index", "best") or "best") != pref:
        store.set_meta("quality_pref_index", pref)
        for a in {aid, *(r["id"] for r in store.q("SELECT id FROM accounts"))}:
            index(store, a, pref=pref)
        return
    for t, table in TABLE.items():
        have = store.one("SELECT 1 AS x FROM variants WHERE account=? AND type=? LIMIT 1", (aid, t))
        if not have and store.one(f"SELECT 1 AS x FROM {table} WHERE account=? LIMIT 1", (aid,)):
            index(store, aid, t, pref)
