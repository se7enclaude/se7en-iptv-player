"""Ce que le son dit d'un épisode ou d'un film : où est le générique d'ouverture, et si les sous-titres sont décalés.

Pendant la lecture, mpv mesure le volume du son (bande des voix, 200–3500 Hz) dix fois par seconde. Ces mesures servent à :

- **Passer l'intro.** Le générique d'une série a la même musique à chaque épisode : en comparant le début de deux épisodes,
  on trouve le passage commun (20 s à 2 min 30, dans les 6 premières minutes). Il est gardé pour la série ; aux épisodes
  suivants, dès que ce passage est reconnu (quelques secondes après son début), le bouton « Passer l'intro » apparaît.
- **Recaler les sous-titres.** Les sous-titres trouvés en ligne viennent parfois d'une autre version du film : ils
  arrivent trop tôt ou trop tard (ou dérivent, 25 contre 23,976 images par seconde). On cherche le décalage pour lequel
  les moments où l'on parle coïncident le mieux avec les répliques.
Rien n'est envoyé nulle part ; aucune connexion de plus au fournisseur.
"""
from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path

import numpy as np

RATE = 10            # mesures par seconde
FLOOR = -60.0        # silence (dB)
HEAD = 360           # secondes du début d'épisode gardées pour chercher le générique
INTRO_MIN, INTRO_MAX = 20, 150
WIN = 8              # secondes comparées à la fois


# ------------------------------------------------------------------ enveloppe
def envelope(samples: dict[int, float], until: float | None = None) -> np.ndarray:
    """Mesures {dixième de seconde: dB} → tableau régulier (trous comblés par la mesure précédente)."""
    if not samples:
        return np.zeros(0, dtype=np.float32)
    n = int(max(samples) + 1) if until is None else int(until * RATE)
    out = np.full(n, np.nan, dtype=np.float32)
    for k, v in samples.items():
        if 0 <= k < n:
            out[k] = max(FLOOR, v)
    # trous : la valeur précédente (ou le silence au tout début)
    last = FLOOR
    for i in range(n):
        if np.isnan(out[i]):
            out[i] = last
        else:
            last = out[i]
    return out


def pack(env: np.ndarray) -> str:
    """Enveloppe → texte compact (un octet par mesure) pour la garder en base."""
    q = np.clip(np.round(env - FLOOR), 0, 255).astype(np.uint8)
    return base64.b64encode(q.tobytes()).decode()


def unpack(s: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(s), dtype=np.uint8).astype(np.float32) + FLOOR


def _z(x: np.ndarray) -> np.ndarray:
    s = x.std()
    return (x - x.mean()) / s if s > 1e-6 else x * 0


def _ncc_all(win: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """Corrélation normalisée de ``win`` contre chaque position de ``ref`` (−1 … 1)."""
    m = len(win)
    if len(ref) < m or m < 4:
        return np.zeros(0, dtype=np.float32)
    w = _z(win)
    if not w.any():
        return np.zeros(len(ref) - m + 1, dtype=np.float32)
    c = np.cumsum(np.concatenate([[0.0], ref]))
    c2 = np.cumsum(np.concatenate([[0.0], ref * ref]))
    sums = c[m:] - c[:-m]
    sq = c2[m:] - c2[:-m]
    var = np.maximum(sq / m - (sums / m) ** 2, 1e-9)
    nfft = 1 << int(np.ceil(np.log2(len(ref) + m)))
    corr = np.fft.irfft(np.fft.rfft(ref, nfft) * np.conj(np.fft.rfft(w, nfft)), nfft)[: len(ref) - m + 1]
    out = corr / (m * np.sqrt(var))
    out[var < 0.5] = 0  # passages plats (silence) : rien à reconnaître
    return out.astype(np.float32)


# ------------------------------------------------------------------ générique d'ouverture
def find_common(a: np.ndarray, b: np.ndarray, thr: float = 0.8) -> tuple[float, float, float] | None:
    """Le plus long passage commun aux débuts de deux épisodes : (début dans a, début dans b, durée) en secondes."""
    a, b = a[: HEAD * RATE], b[: HEAD * RATE]
    m = WIN * RATE
    step = RATE  # une fenêtre par seconde
    hits: list[tuple[int, int]] = []  # (position dans a, décalage b − a) des fenêtres bien reconnues
    for i in range(0, len(a) - m, step):
        r = _ncc_all(a[i:i + m], b)
        if not len(r):
            break
        j = int(np.argmax(r))
        if r[j] >= thr:
            hits.append((i, j - i))
    best = None
    k = 0
    while k < len(hits):  # suites de fenêtres consécutives au même décalage (à 0,3 s près)
        s = k
        while k + 1 < len(hits) and hits[k + 1][0] - hits[k][0] <= 6 * step and abs(hits[k + 1][1] - hits[s][1]) <= 3:
            k += 1
        start, lag = hits[s][0], hits[s][1]
        st, en = _refine(a, b, start, hits[k][0] + m, lag)
        dur = (en - st) / RATE
        if INTRO_MIN <= dur <= INTRO_MAX and (best is None or dur > best[2]):
            best = (st / RATE, (st + lag) / RATE, dur)
        k += 1
    return best


def _refine(a: np.ndarray, b: np.ndarray, start: int, end: int, lag: int) -> tuple[int, int]:
    """Bords exacts du passage commun : là où les deux sons restent presque identiques (écart lissé < 2,5 dB)."""
    lo = max(0, start - 60 * RATE, -lag)
    hi = min(len(a), end + 60 * RATE, len(b) - lag)
    if hi - lo < 4 * RATE:
        return start, end
    d = a[lo:hi] - b[lo + lag:hi + lag]
    core = d[max(0, start - lo):max(1, end - lo)]
    d = np.abs(d - (np.median(core) if len(core) else 0))  # même passage un peu plus fort dans un épisode : compensé
    k = int(1.5 * RATE)
    sm = np.convolve(d, np.ones(k) / k, mode="same")
    ok = sm < 2.5
    best, cur = (start, end), None
    for i, v in enumerate(ok):
        if v and cur is None:
            cur = i
        if (not v or i == len(ok) - 1) and cur is not None:
            j = i if not v else i + 1
            if j - cur > best[1] - best[0] or best == (start, end):
                best = (lo + cur, lo + j) if j - cur >= INTRO_MIN * RATE else best
            cur = None
    return best


def locate(live: np.ndarray, template: np.ndarray, thr: float = 0.86) -> float | None:
    """Où en est le générique (secondes depuis son début), d'après les dernières secondes entendues ; None si absent."""
    m = 6 * RATE
    if len(live) < m or len(template) < m:
        return None
    r = _ncc_all(live[-m:], template)
    if not len(r):
        return None
    j = int(np.argmax(r))
    return (j + m) / RATE if r[j] >= thr else None


def locate_start(live: np.ndarray, template: np.ndarray, thr: float = 0.86) -> float | None:
    """Où le générique a commencé dans ``live`` (secondes depuis le début de ``live``), ou None.

    On cherche le début du générique (ses 8 premières secondes) plutôt qu'un moment quelconque : une chanson se répète,
    son début se reconnaît sans ambiguïté (juste avant, ce n'est pas le générique). Le plus tôt des meilleurs passages."""
    m = WIN * RATE
    if len(live) < m or len(template) < m:
        return None
    r = _ncc_all(template[:m], live)
    if not len(r) or float(r.max()) < thr:
        return None
    j = int(np.nonzero(r >= max(thr, float(r.max()) - 0.03))[0][0])
    return j / RATE


class IntroMemory:
    """Débuts d'épisodes et génériques trouvés, gardés par série (table ``intros``)."""

    def __init__(self, store) -> None:
        self.store = store
        store.run("CREATE TABLE IF NOT EXISTS intros (account TEXT, series TEXT, episode TEXT, head TEXT, updated REAL, "
                  "PRIMARY KEY (account, series, episode))")
        store.run("CREATE TABLE IF NOT EXISTS intro_found (account TEXT, series TEXT, template TEXT, duration REAL, "
                  "updated REAL, PRIMARY KEY (account, series))")

    def template(self, account: str, series: str) -> tuple[np.ndarray, float] | None:
        row = self.store.one("SELECT template, duration FROM intro_found WHERE account=? AND series=?", (account, series))
        return (unpack(row["template"]), float(row["duration"])) if row else None

    def learn(self, account: str, series: str, episode: str, head: np.ndarray) -> tuple[np.ndarray, float] | None:
        """Garde le début de cet épisode et le compare aux autres de la série ; renvoie le générique s'il est trouvé."""
        if len(head) < min(120, HEAD) * RATE * 0.9:
            return None
        self.store.run("INSERT OR REPLACE INTO intros VALUES (?,?,?,?,?)", (account, series, episode, pack(head), time.time()))
        others = self.store.q("SELECT head FROM intros WHERE account=? AND series=? AND episode<>? ORDER BY updated DESC LIMIT 3",
                              (account, series, episode))
        for o in others:
            found = find_common(head, unpack(o["head"]))
            if found:
                start, _sb, dur = found
                tpl = head[int(start * RATE): int((start + dur) * RATE)]
                self.store.run("INSERT OR REPLACE INTO intro_found VALUES (?,?,?,?,?)",
                               (account, series, pack(tpl), dur, time.time()))
                return tpl, dur
        return None


# ------------------------------------------------------------------ sous-titres
_TC = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{1,3})")
_ASS = re.compile(r"^Dialogue:\s*[^,]*,(\d+):(\d{2}):(\d{2})\.(\d{2}),(\d+):(\d{2}):(\d{2})\.(\d{2}),", re.M)


def cues(path: str | Path) -> list[tuple[float, float]]:
    """Moments des répliques (début, fin) d'un fichier .srt / .vtt / .ass."""
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return []
    from .subs_online import decode
    text = decode(raw)
    out = []
    for m in _TC.finditer(text):
        g = [int(x) for x in m.groups()]
        a = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / (10 ** len(m.group(4)))
        b = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / (10 ** len(m.group(8)))
        if b > a:
            out.append((a, b))
    if not out:
        for m in _ASS.finditer(text):
            g = [int(x) for x in m.groups()]
            out.append((g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 100, g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 100))
    return sorted(out)


SPEEDS = (1.0, 25 / 23.976, 23.976 / 25)


def best_offset(env: np.ndarray, known: np.ndarray, subs: list[tuple[float, float]], max_shift: float = 30.0) -> dict | None:
    """Décalage (et vitesse) des sous-titres qui fait coïncider répliques et paroles.

    ``env`` : volume du son (dB, 10 par seconde, depuis 0) ; ``known`` : où il a vraiment été mesuré (booléens).
    Renvoie {"delay": s, "speed": x, "score": …} si le résultat est net, sinon None (on ne touche à rien)."""
    if known.sum() < 180 * RATE or not subs:
        return None
    n = len(env)
    vals = env[known]
    sd = float(vals.std()) or 1.0
    voice = np.zeros(n, dtype=np.float32)  # paroles plus fortes que d'habitude → positif ; passages non mesurés → 0
    voice[known] = np.clip((vals - vals.mean()) / sd, -2.5, 2.5)
    shifts = np.arange(-int(max_shift * RATE), int(max_shift * RATE) + 1)
    best = None
    for speed in SPEEDS:
        s = np.zeros(n + 2 * len(shifts), dtype=np.float32)
        base = len(shifts)
        for a, b in subs:
            ia, ib = int(a * speed * RATE) + base, int(b * speed * RATE) + base
            if ib > 0 and ia < len(s):
                s[max(0, ia):min(len(s), ib)] = 1.0
        s -= s[base:base + n].mean()
        # score de chaque décalage : somme(voix × répliques décalées), sur les passages mesurés
        nfft = 1 << int(np.ceil(np.log2(len(s) + n)))
        corr = np.fft.irfft(np.fft.rfft(s, nfft) * np.conj(np.fft.rfft(voice, nfft)), nfft)
        # corr[k] = sum_t voice[t] * s[t + k] ; décalage d (répliques déplacées de d) ↔ k = base − d
        ks = base - shifts
        sc = corr[ks] / max(1.0, float(known.sum()))
        i = int(np.argmax(sc))
        # netteté : le meilleur pic contre le meilleur ailleurs (à plus de 1,5 s)
        far = np.abs(shifts - shifts[i]) > int(1.5 * RATE)
        second = float(sc[far].max()) if far.any() else 0.0
        cand = {"delay": round(float(shifts[i]) / RATE, 1), "speed": round(speed, 5), "score": float(sc[i]),
                "sharp": float(sc[i]) / max(1e-6, second) if second > 0 else 9.0}
        if best is None or cand["score"] > best["score"] * (1.04 if speed != 1.0 else 1.0):
            best = cand
    if best and best["score"] > 0.05 and best["sharp"] >= 1.15:
        return best
    return None


def to_json(env: np.ndarray) -> str:
    return json.dumps({"rate": RATE, "env": pack(env)})
