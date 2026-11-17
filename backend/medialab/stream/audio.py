"""Ce que le son dit d'un épisode ou d'un film : où sont les génériques, et si les sous-titres sont décalés.

Pendant la lecture, mpv mesure le son dix fois par seconde : son volume et sa « couleur » (voir ``feature``). Ces mesures
servent à :

- **Passer l'intro.** Le générique d'une série a la même musique à chaque épisode : en comparant le début de deux épisodes,
  on trouve le passage commun (15 s à 3 min, dans les 10 premières minutes). Il est gardé pour la série ; aux épisodes
  suivants, dès que ce passage est reconnu (8 secondes après son début), le bouton « Passer l'intro » apparaît.
- **Générique de fin.** Pareil avec les 7 dernières minutes : la carte « Épisode suivant » arrive quand il commence.
- **Recaler les sous-titres.** Les sous-titres trouvés en ligne viennent parfois d'une autre version du film : ils
  arrivent trop tôt ou trop tard (ou dérivent, 25 contre 23,976 images par seconde). On cherche le décalage pour lequel
  les moments où l'on parle coïncident le mieux avec les répliques (volume seul).
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


# ------------------------------------------------------------------ empreinte du son
# Le volume seul ne suffit pas : une musique de générique, très compressée, a un volume presque constant, et deux scènes
# différentes peuvent avoir la même courbe. On mesure donc aussi la « couleur » du son (filtre aspectralstats) : son
# centre de gravité, sa largeur, sa coupure haute, son côté bruit ou note, son désordre. La même musique redonne la même
# trajectoire à chaque épisode ; une scène différente non.
FEATS = ("M", "centroid", "spread", "rolloff", "flatness", "entropy")
_LO = np.array([-60.0, 5.0, 5.0, 5.0, -6.0, 0.0], dtype=np.float32)    # bornes de chaque mesure (après log)
_HI = np.array([0.0, 14.5, 14.5, 14.5, 0.0, 1.0], dtype=np.float32)
D = len(FEATS)


def feature(md: dict) -> list[float] | None:
    """Métadonnées du filtre @vad → une mesure (volume dB, log2 Hz…, log10 platitude, entropie), ou None."""
    try:
        m = float(md["lavfi.r128.M"])
        g = lambda k: float(md[f"lavfi.aspectralstats.1.{k}"])  # noqa: E731
        return [m, float(np.log2(max(32.0, g("centroid")))), float(np.log2(max(32.0, g("spread")))),
                float(np.log2(max(32.0, g("rolloff")))), float(np.log10(max(1e-6, g("flatness")))), g("entropy")]
    except (KeyError, TypeError, ValueError):
        return None


def features(samples: dict[int, list[float]], start: int = 0, until: int | None = None) -> np.ndarray:
    """Mesures {dixième de seconde: vecteur} → tableau (n, D) en niveaux 0–255, de ``start`` à ``until`` (exclu).

    Les trous sont comblés par la mesure précédente ; s'il manque la fin, le tableau s'arrête à la dernière mesure."""
    keys = [k for k in samples if k >= start and (until is None or k < until)]
    if not keys:
        return np.zeros((0, D), dtype=np.float32)
    n = max(keys) + 1 - start
    out = np.full((n, D), np.nan, dtype=np.float32)
    for k in keys:
        out[k - start] = samples[k]
    last = out[~np.isnan(out[:, 0])][0].copy()
    for i in range(n):  # trous : la mesure précédente
        if np.isnan(out[i, 0]):
            out[i] = last
        else:
            last = out[i]
    if n >= 3:  # mesures prises au vol, à 0,1 s près : une moyenne glissante sur 0,3 s les rend comparables
        out[1:-1] = (out[:-2] + out[1:-1] + out[2:]) / 3
    return np.clip((out - _LO) / (_HI - _LO) * 255.0, 0, 255).astype(np.float32)


def measured(samples: dict, start: int, until: int) -> float:
    """Part des dixièmes de seconde réellement mesurés entre ``start`` et ``until``."""
    return sum(1 for k in samples if start <= k < until) / max(1, until - start)


def pack(env: np.ndarray) -> str:
    """Tableau (n, D) en niveaux, ou enveloppe en dB → texte compact (un octet par mesure) pour le garder en base."""
    if env.ndim == 2:
        q = np.clip(np.round(env), 0, 255).astype(np.uint8)
        return f"{q.shape[1]}:" + base64.b64encode(q.tobytes()).decode()
    return base64.b64encode(np.clip(np.round(env - FLOOR), 0, 255).astype(np.uint8).tobytes()).decode()


def unpack(s: str) -> np.ndarray:
    if ":" in s[:4]:
        d, s = s.split(":", 1)
        return np.frombuffer(base64.b64decode(s), dtype=np.uint8).astype(np.float32).reshape(-1, int(d))
    return np.frombuffer(base64.b64decode(s), dtype=np.uint8).astype(np.float32) + FLOOR


def _z(x: np.ndarray) -> np.ndarray:
    s = x.std()
    return (x - x.mean()) / s if s > 1e-6 else x * 0


def _ncc_all(win: np.ndarray, ref: np.ndarray, flat: float = 0.5) -> np.ndarray:
    """Corrélation normalisée de ``win`` contre chaque position de ``ref`` (−1 … 1) ; 0 là où ``ref`` est plat."""
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
    out[var < flat] = 0  # passages plats (silence) : rien à reconnaître
    return out.astype(np.float32)


def _ncc(win: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """Ressemblance de ``win`` (m, D) à chaque position de ``ref`` (n, D) : moyenne des corrélations des mesures.

    Une mesure qui ne bouge pas dans ``win`` (silence, note tenue) ne compte pas : elle ne dit rien."""
    if win.ndim == 1:
        return _ncc_all(win, ref)
    tot, cnt = None, 0
    for d in range(win.shape[1]):
        if win[:, d].std() < 1.5:
            continue
        r = _ncc_all(win[:, d], ref[:, d], flat=1.0)
        if not len(r):
            return r
        tot = r if tot is None else tot + r
        cnt += 1
    if tot is None:
        return np.zeros(max(0, len(ref) - len(win) + 1), dtype=np.float32)
    # moins de trois mesures qui bougent : peu fiable, on pénalise
    return (tot / cnt * min(1.0, cnt / 3)).astype(np.float32)


def _local(a: np.ndarray, b: np.ndarray, w: int) -> np.ndarray:
    """Ressemblance locale de deux passages alignés : corrélation sur des fenêtres de ``w`` mesures (moyenne des mesures)."""
    n = min(len(a), len(b)) - w + 1
    if n <= 0:
        return np.zeros(0, dtype=np.float32)
    a2, b2 = (a[:, None], b[:, None]) if a.ndim == 1 else (a, b)
    tot = np.zeros(n)
    cnt = np.zeros(n)

    def cs(v):
        return np.concatenate([[0.0], np.cumsum(v)])

    def f(c):
        return (c[w:] - c[:-w]) / w

    for d in range(a2.shape[1]):
        x, y = a2[: n + w - 1, d].astype(np.float64), b2[: n + w - 1, d].astype(np.float64)
        mx, my = f(cs(x)), f(cs(y))
        vx, vy = f(cs(x * x)) - mx * mx, f(cs(y * y)) - my * my
        ok = (vx > 2.0) & (vy > 2.0)
        r = np.where(ok, (f(cs(x * y)) - mx * my) / np.sqrt(np.maximum(vx * vy, 1e-9)), 0.0)
        tot += r
        cnt += ok
    return np.where(cnt >= 2, tot / np.maximum(cnt, 1), -1.0).astype(np.float32)


# ------------------------------------------------------------------ génériques d'ouverture et de fin
HEAD = 600           # secondes du début d'épisode gardées pour chercher le générique (les avant-génériques sont parfois longs)
TAIL = 420           # secondes de la fin gardées pour chercher le générique de fin
INTRO_MIN, INTRO_MAX = 15, 180
OUTRO_MIN, OUTRO_MAX = 15, 420
WIN = 8              # secondes comparées à la fois
THR = 0.7            # ressemblance d'une fenêtre « même son »
EDGE = 0.6           # ressemblance locale qui marque encore le passage commun (bords)
SAME = 0.75          # ressemblance du passage entier : vrai générique ≥ 0,8 ; deux scènes qui se ressemblent ≤ 0,7


def find_common(a: np.ndarray, b: np.ndarray, lo: float = INTRO_MIN, hi: float = INTRO_MAX,
                thr: float = THR) -> tuple[float, float, float] | None:
    """Le plus long passage commun à deux débuts (ou fins) d'épisodes : (début dans a, début dans b, durée) en secondes.

    Une musique tourne en boucle : une fenêtre de 8 s ressemble alors à plusieurs endroits de l'autre épisode (une mesure
    plus loin, au refrain suivant…). On garde donc tous les bons alignements de chaque fenêtre, puis l'alignement que
    suivent le plus longtemps des fenêtres consécutives : le bon est celui où tout le morceau coïncide."""
    m = WIN * RATE
    step = RATE  # une fenêtre par seconde
    hits: list[tuple[int, int]] = []  # (décalage b − a, position dans a) des fenêtres bien reconnues
    for i in range(0, len(a) - m, step):
        r = _ncc(a[i:i + m], b)
        if not len(r):
            break
        if float(r.max()) < thr:
            continue
        peak = (r >= thr) & (r >= np.concatenate([[-2.0], r[:-1]])) & (r >= np.concatenate([r[1:], [-2.0]]))
        js = np.nonzero(peak)[0]
        for j in js[np.argsort(r[js])[::-1][:12]]:
            hits.append((int(j) - i, i))
    hits.sort()
    runs: list[tuple[int, int, int, int]] = []  # (fenêtres, début, fin, décalage)
    k = 0
    while k < len(hits):  # décalages voisins (à 0,3 s près) regroupés, puis suites de fenêtres (trous de 6 s permis)
        g = k
        while g + 1 < len(hits) and hits[g + 1][0] - hits[k][0] <= 3:
            g += 1
        pos = sorted({i for _l, i in hits[k:g + 1]})
        lag = int(np.median([lg for lg, _i in hits[k:g + 1]]))
        s0 = 0
        for q in range(1, len(pos) + 1):
            if q == len(pos) or pos[q] - pos[q - 1] > 6 * step:
                if q - s0 >= 2:
                    runs.append((q - s0, pos[s0], pos[q - 1] + m, lag))
                s0 = q
        k = g + 1
    best = None
    for _n, st0, en0, lag in sorted(runs, reverse=True)[:12]:  # les plus longues suites seulement
        st, en = _refine(a, b, st0, en0, lag)
        dur = (en - st) / RATE
        if lo <= dur <= hi and (best is None or dur > best[2] + 0.5) and _whole(a, b, st, en, lag) >= SAME:
            best = (st / RATE, (st + lag) / RATE, dur)
    return best


def _whole(a: np.ndarray, b: np.ndarray, st: int, en: int, lag: int) -> float:
    """Ressemblance du passage entier, aligné (au mieux à ±0,2 s près)."""
    out = -1.0
    for dl in range(-2, 3):
        x0, x1 = max(st, -(lag + dl)), min(en, len(b) - (lag + dl))
        if x1 - x0 >= WIN * RATE:
            out = max(out, float(_ncc(a[x0:x1], b[x0 + lag + dl:x1 + lag + dl])[0]))
    return out


def _refine(a: np.ndarray, b: np.ndarray, start: int, end: int, lag: int) -> tuple[int, int]:
    """Bords exacts du passage commun : jusqu'où les deux sons, alignés, se ressemblent encore (fenêtres de 3 s)."""
    lo = max(0, start - 90 * RATE, -lag)
    hi = min(len(a), end + 90 * RATE, len(b) - lag)
    w = 3 * RATE
    if hi - lo < w + 2:
        return start, end
    # petit flottement d'alignement (mesures prises au vol) : on garde le meilleur de lag − 2 … lag + 2
    best = None
    for dl in range(-2, 3):
        x0, x1 = max(lo, -(lag + dl)), min(hi, len(b) - (lag + dl))
        if x1 - x0 < w + 2:
            continue
        r = np.full(hi - lo, -1.0, dtype=np.float32)
        loc = _local(a[x0:x1], b[x0 + lag + dl:x1 + lag + dl], w)
        r[x0 - lo:x0 - lo + len(loc)] = loc
        best = r if best is None else np.maximum(best, r)
    if best is None:
        return start, end
    ok = np.convolve((best >= EDGE).astype(float), np.ones(RATE) / RATE, "same") >= 0.5  # trous d'une seconde comblés
    c0, c1 = max(0, start - lo), max(0, min(len(ok), end - w - lo))
    if c1 <= c0 or ok[c0:c1].mean() < 0.5:
        return start, end
    s = c0 + int(np.argmax(ok[c0:c1]))
    while s > 0 and ok[s - 1]:
        s -= 1
    e = c1 - 1
    while e + 1 < len(ok) and ok[e + 1]:
        e += 1
    # centres des fenêtres ; une fenêtre à cheval sur le bord ressemble encore un peu : on resserre d'une seconde
    s = s + w // 2 + (RATE if s > 0 else 0)
    return lo + s, min(hi, lo + e + w // 2 - RATE)


def locate_start(live: np.ndarray, template: np.ndarray, thr: float = THR) -> float | None:
    """Où le générique a commencé dans ``live`` (secondes depuis le début de ``live``), ou None.

    On cherche le début du générique (ses 8 premières secondes) : une chanson se répète, son début se reconnaît presque
    sans ambiguïté. S'il y a plusieurs candidats (refrain qui revient), on garde celui pour lequel tout ce qu'on a entendu
    depuis ressemble le plus au générique."""
    m = WIN * RATE
    # on cherche à partir de 2 s après le début appris : s'il a été pris un peu tôt (fin de la scène d'avant), ça ne
    # gêne pas. Les candidats sont départagés sur tout ce qu'on a entendu du générique depuis.
    o = min(2 * RATE, max(0, len(template) - 2 * m))
    if len(live) < m or len(template) - o < m:
        return None
    r = _ncc(template[o:o + m], live)
    if not len(r) or float(r.max()) < thr:
        return None
    cands = [int(j) - o for j in np.nonzero(r >= max(thr - 0.04, float(r.max()) - 0.08))[0]]
    # chacun jugé sur la même longueur (une comparaison plus courte ressemble plus facilement par hasard)
    span = {st: (max(o, -st), min(len(live) - st - max(o, -st), len(template) - max(o, -st))) for st in cands}
    span = {st: v for st, v in span.items() if v[1] >= m + 3 * RATE}  # au moins 11 s entendues
    if not span:
        return None
    n = min(v[1] for v in span.values())
    best, score = None, -2.0
    for st, (t0, _n) in sorted(span.items()):
        s = float(_ncc(live[st + t0:st + t0 + n], template[t0:t0 + n])[0])
        if s > score + 0.01:  # à égalité, le plus tôt
            best, score = st, s
    return best / RATE if score >= thr - 0.08 else None


class IntroMemory:
    """Débuts et fins d'épisodes mesurés, et génériques trouvés, gardés par série."""

    KEEP = 8  # épisodes comparés au plus

    def __init__(self, store) -> None:
        self.store = store
        store.run("DROP TABLE IF EXISTS intros")  # anciennes mesures (volume seul) : trop pauvres pour être réutilisées
        store.run("DROP TABLE IF EXISTS intro_found")
        store.run("CREATE TABLE IF NOT EXISTS intro_parts (account TEXT, series TEXT, episode TEXT, part TEXT, data TEXT, "
                  "updated REAL, PRIMARY KEY (account, series, episode, part))")
        store.run("CREATE TABLE IF NOT EXISTS intro_tpl (account TEXT, series TEXT, part TEXT, template TEXT, duration REAL, "
                  "offset REAL, support INTEGER, updated REAL, PRIMARY KEY (account, series, part))")

    def template(self, account: str, series: str, part: str = "head") -> tuple[np.ndarray, float, float] | None:
        """(empreinte, durée, position) du générique : secondes depuis le début (head) ou avant la fin (tail)."""
        row = self.store.one("SELECT template, duration, offset FROM intro_tpl WHERE account=? AND series=? AND part=?",
                             (account, series, part))
        return (unpack(row["template"]), float(row["duration"]), float(row["offset"])) if row else None

    def learn(self, account: str, series: str, episode: str, part: str, data: np.ndarray,
              origin: float = 0.0, dur: float = 0.0) -> tuple[np.ndarray, float, float] | None:
        """Garde ce début (ou cette fin) d'épisode et le compare aux autres de la série ; renvoie le générique trouvé.

        ``data`` : mesures (n, D) depuis l'instant ``origin`` de l'épisode (0 pour le début) ; ``dur`` : durée de
        l'épisode (pour placer le générique de fin avant la fin). Le générique retenu est celui qu'on retrouve dans le plus
        d'épisodes (à égalité, le plus long)."""
        if data.ndim != 2 or len(data) < 4 * WIN * RATE:
            return None
        self.store.run("INSERT OR REPLACE INTO intro_parts VALUES (?,?,?,?,?,?)",
                       (account, series, episode, part, pack(data), time.time()))
        others = self.store.q("SELECT data FROM intro_parts WHERE account=? AND series=? AND part=? AND episode<>? "
                              "ORDER BY updated DESC LIMIT ?", (account, series, part, episode, self.KEEP))
        lo, hi = (INTRO_MIN, INTRO_MAX) if part == "head" else (OUTRO_MIN, OUTRO_MAX)
        found: list[tuple[float, float]] = []
        for o in others:
            other = unpack(o["data"])
            if other.ndim != 2 or other.shape[1] != data.shape[1]:
                continue
            f = find_common(data, other, lo, hi)
            if f:
                found.append((f[0], f[2]))
        if not found:
            return None
        # regroupe les passages qui commencent au même endroit (à 3 s près) : le plus souvent retrouvé gagne
        groups: list[list[tuple[float, float]]] = []
        for st, du in sorted(found):
            if groups and st - groups[-1][0][0] <= 3:
                groups[-1].append((st, du))
            else:
                groups.append([(st, du)])
        g = max(groups, key=lambda g: (len(g), float(np.median([d for _s, d in g]))))
        start = float(np.median([s for s, _d in g]))
        duration = float(np.median([s + d for s, d in g])) - start
        old = self.store.one("SELECT support FROM intro_tpl WHERE account=? AND series=? AND part=?", (account, series, part))
        if old and int(old["support"]) > len(g):
            return self.template(account, series, part)  # celui qu'on a est mieux confirmé
        tpl = data[int(start * RATE): int((start + duration) * RATE)]
        offset = origin + start if part == "head" else max(0.0, dur - (origin + start))
        self.store.run("INSERT OR REPLACE INTO intro_tpl VALUES (?,?,?,?,?,?,?,?)",
                       (account, series, part, pack(tpl), duration, offset, len(g), time.time()))
        return tpl, duration, offset


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
