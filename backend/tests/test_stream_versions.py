"""Toutes les versions d'un titre (langues, qualités) et les sous-titres trouvés en ligne."""
import gzip
import json
import os
import subprocess
import time

import pytest

from medialab.stream import player as P
from medialab.stream import subs_online as SO
from medialab.stream import versions as V
from medialab.stream.store import Store


# ------------------------------------------------------------------ lecture des noms du catalogue
@pytest.mark.parametrize("name, cat, lang, key, rank, tags", [
    ("|FR| Le Parrain", "|FR| FILMS ANCIENS", "fr", "le parrain", 0, []),
    ("|FR| Le Parrain (4K)", "|FR| FILMS 4K LIGHT", "fr", "le parrain", 4, ["Light"]),
    ("|IT| Il padrino (BLURAY)", "|IT| FILM BLURAY", "it", "il padrino", 0, ["Blu-ray"]),
    ("|IT| Il padrino (HEVC)", "|IT| FILM HEVC", "it", "il padrino", 0, ["HEVC"]),
    ("|IT| Scary Movie | 2026 (UHD)", "|IT| FILM", "it", "scary movie", 4, []),
    ("EN: The Godfather 1080p", "Movies", "en", "the godfather", 3, []),
    ("[TR] Baba", "Filmler", "tr", "baba", 0, []),
    ("Le Parrain", "|AR| أفلام", "ar", "le parrain", 0, []),
    ("|FR| Notre Parrain (VOST)", "|FR| DOCUMENTAIRE", "fr", "notre parrain", 0, ["VOST"]),
])
def test_names_give_language_title_quality_and_tags(name, cat, lang, key, rank, tags):
    d = V.describe(name, cat)
    assert (d["lang"], V.title_key(name), d["rank"], d["tags"]) == (lang, key, rank, tags)


def test_a_title_that_only_looks_like_a_language_is_kept():
    assert V.lang_of("Le Parrain") == "" and V.title_key("Le Parrain") == "le parrain"
    assert V.year_of("|IT| L'ultimo padrino | 2008 (SD)", None) == 2008


# ------------------------------------------------------------------ toutes les versions
@pytest.fixture
def cat(tmp_path):
    st = Store(tmp_path / "s.db")
    aid = "acc"
    cats = [("1", "|FR| FILMS ANCIENS"), ("2", "|FR| FILMS 4K LIGHT"), ("3", "|IT| FILM BLURAY"), ("4", "|ES| ANTIGUAS"),
            ("5", "|FR| COMÉDIE"), ("6", "|AR| أفلام")]
    st.many("INSERT INTO categories VALUES (?,?,?,?,?,?)", [(aid, "movie", c, n, i, 0) for i, (c, n) in enumerate(cats)])
    films = [("10", "|FR| Le Parrain", "1"), ("11", "|FR| Le Parrain (4K)", "2"), ("12", "|IT| Il padrino (BLURAY)", "3"),
             ("13", "|ES| El padrino", "4"), ("14", "|FR| Les Parrains", "5"), ("15", "|IT| Il padrino", "3"),
             ("16", "العراب", "6"), ("17", "|ES| El padrino", "4")]
    st.many("INSERT INTO vod (account, id, name, norm, logo, category, position) VALUES (?,?,?,?,?,?,?)",
            [(aid, i, n, n.lower(), "", c, k) for k, (i, n, c) in enumerate(films)])
    V.index(st, aid, "movie", {"10": "238"})
    return st, aid


def _wikidata(_q):
    labels = ["Le Parrain", "The Godfather", "Il padrino", "El padrino", "العراب", "Der Pate", "Pi"]
    return {"results": {"bindings": [{"l": {"value": l}, "imdb": {"value": "tt0068646"}} for l in labels]}}


def test_all_languages_and_qualities_are_found_and_checked_by_their_own_sheet(cat):
    st, aid = cat
    sheets = {"11": "238", "12": "238", "13": "238", "15": "238", "16": "238", "17": "999"}  # 17 : un remake
    res = V.versions(st, aid, "movie", "10", tmdb_of=sheets.get, fetch=_wikidata, pref_langs=["fr", "en"])
    got = [(v["id"], v["lang_label"], v["quality"], v["tags"]) for v in res["versions"]]
    assert res["imdb"] == "tt0068646"
    assert got[:2] == [("11", "Français", "4K UHD", ["Light"]), ("10", "Français", "", [])]  # ta langue d'abord
    ids = {g[0] for g in got}
    assert ids == {"10", "11", "12", "13", "15", "16"}  # ni « Les Parrains », ni le remake
    assert {v["lang_label"] for v in res["versions"]} == {"Français", "Italien", "Espagnol", "Arabe"}
    assert next(v for v in res["versions"] if v["id"] == "10")["current"]
    # numéros appris : la prochaine fois, plus besoin des fiches
    assert st.one("SELECT tmdb FROM works WHERE account=? AND id='12'", (aid,))["tmdb"] == "238"


def test_without_provider_sheets_same_title_versions_are_kept_unverified(cat):
    st, aid = cat
    res = V.versions(st, aid, "movie", "10", tmdb_of=None, fetch=_wikidata)
    v13 = next(v for v in res["versions"] if v["id"] == "13")
    assert not v13["verified"] and "14" not in {v["id"] for v in res["versions"]}


def test_titles_are_cached_and_offline_keeps_the_last_answer(cat):
    st, _aid = cat
    calls = []
    V.work_info(st, "movie", "238", lambda q: calls.append(q) or _wikidata(q))
    V.work_info(st, "movie", "238", lambda q: calls.append(q) or _wikidata(q))
    assert len(calls) == 1
    st.run("UPDATE wd_titles SET fetched=0")
    assert V.work_info(st, "movie", "238", lambda q: (_ for _ in ()).throw(OSError("hors ligne")))["imdb"] == "tt0068646"


def test_real_resolution_from_the_provider_sheet():
    assert V._video({"video": {"width": 3840, "height": 2160, "codec_name": "hevc"}, "bitrate": 18000}) == \
        {"label": "3840×2160 · HEVC · 18.0 Mb/s", "rank": 4}
    assert V._video({}) == {}


# ------------------------------------------------------------------ sous-titres en ligne
def test_wanted_languages_follow_the_settings_with_english_as_backup():
    assert SO.wanted_langs("fr,fre,fra") == ["fre", "eng"]
    assert SO.wanted_langs("ar,en") == ["ara", "eng"]


def test_a_language_already_in_the_video_is_not_searched():
    tracks = [{"type": "sub", "lang": "fre", "title": ""}, {"type": "audio", "lang": "eng"}]
    assert SO.has_track(tracks, "fre") and not SO.has_track(tracks, "eng")
    assert SO.has_track([{"type": "sub", "lang": None, "title": "French (forced)"}], "fre")


def test_search_keeps_single_files_hearing_impaired_last():
    rows = [{"IDSubtitleFile": "1", "SubSumCD": "2", "SubFormat": "srt", "SubDownloadsCnt": "99999", "SubDownloadLink": "x"},
            {"IDSubtitleFile": "2", "SubSumCD": "1", "SubFormat": "srt", "SubDownloadsCnt": "500", "SubHearingImpaired": "1", "SubDownloadLink": "x"},
            {"IDSubtitleFile": "3", "SubSumCD": "1", "SubFormat": "srt", "SubDownloadsCnt": "100", "SubDownloadLink": "x"}]
    urls = []
    got = SO.search("tt0068646", "fre", fetch=lambda u: urls.append(u) or rows)
    assert [s["id"] for s in got] == ["3", "2"] and urls == [f"{SO.API}/imdbid-68646/sublanguageid-fre"]
    SO.search("tt0141842", "fre", season=1, episode=3, fetch=lambda u: urls.append(u) or [])
    assert urls[-1] == f"{SO.API}/episode-3/imdbid-141842/season-1/sublanguageid-fre"


def test_download_unzips_and_converts_old_encodings_to_utf8(tmp_path, monkeypatch):
    monkeypatch.setattr(SO, "cache_dir", lambda: tmp_path)
    srt = "1\n00:00:01,000 --> 00:00:02,000\nÇa marche, déjà vu.\n\n".encode("cp1252")
    p = SO.download({"id": "7", "link": "http://x", "encoding": "CP1252"}, fetch_bytes=lambda u: gzip.compress(srt))
    assert p.read_text(encoding="utf-8").count("Ça marche, déjà vu.") == 1


@pytest.mark.skipif(not os.environ.get("SE7EN_ONLINE_TESTS"), reason="réseau : SE7EN_ONLINE_TESTS=1")
def test_real_opensubtitles_has_french_subtitles_for_the_godfather(tmp_path, monkeypatch):
    monkeypatch.setattr(SO, "cache_dir", lambda: tmp_path)
    found = SO.search("tt0068646", "fre")
    assert found and SO.download(found[0]).stat().st_size > 1000


def test_found_subtitles_become_normal_tracks_in_real_mpv(tmp_path, ffmpeg_bin):
    if P.mpv_dir() is None:
        pytest.skip("libmpv absent")
    clip = tmp_path / "film.mkv"
    subprocess.run([ffmpeg_bin, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=25", "-f", "lavfi",
                    "-i", "sine=frequency=440", "-t", "10", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    "-metadata:s:a:0", "language=ara", str(clip)], check=True)
    srt = tmp_path / "os-1.srt"
    srt.write_text("1\n00:00:00,500 --> 00:00:05,000\nBonjour\n\n", encoding="utf-8")
    mpv = P._mpv()
    p = P.Player("t", lambda: {})
    p.mpv = mpv.MPV(vo="null", ao="null", config="no", load_scripts="no")
    p.item = {"kind": "movie", "id": "42", "title": "Film"}
    try:
        p.mpv.play(str(clip))
        for _ in range(100):
            if (p.mpv.time_pos or 0) > 0.5:
                break
            time.sleep(0.05)
        assert not p.add_external_sub("autre", str(srt), "Français · OpenSubtitles 1", "fre", True)  # plus le même film
        assert p.add_external_sub("42", str(srt), "Français · OpenSubtitles 1", "fre", True)
        time.sleep(0.4)
        subs = [t for t in p.snapshot()["tracks"] if t["type"] == "sub"]
        assert len(subs) == 1 and subs[0]["selected"] and subs[0]["external"] and subs[0]["title"] == "Français · OpenSubtitles 1"
    finally:
        p.mpv.terminate()
        p.mpv = None


def test_a_version_without_its_own_sheet_borrows_the_number_of_the_same_title(cat):
    st, aid = cat
    res = V.versions(st, aid, "movie", "11", tmdb_of=lambda i: None, fetch=_wikidata)  # « Le Parrain (4K) »
    assert {"10", "12", "13", "15", "16"} <= {v["id"] for v in res["versions"]} and res["tmdb"] == "238"


def test_a_slow_provider_does_not_block_the_list(cat, monkeypatch):
    st, aid = cat
    monkeypatch.setattr(V, "VERIFY_SECONDS", 0.3)
    t0 = time.time()
    res = V.versions(st, aid, "movie", "10", tmdb_of=lambda i: time.sleep(3) or "238", fetch=_wikidata)
    assert time.time() - t0 < 2 and {"12", "13"} <= {v["id"] for v in res["versions"]}


def test_advert_blocks_are_removed_and_cues_renumbered():
    srt = ("1\n00:00:06,000 --> 00:00:12,074\nDo you want subtitles for any video?\n-=[ ai.OpenSubtitles.com ]=-\n\n"
           "2\n00:01:16,780 --> 00:01:19,240\nJ'ai confiance en l'Amérique.\n\n"
           "3\n00:01:19,700 --> 00:01:22,240\nL'Amérique a fait ma fortune.\n")
    out = SO.strip_ads(srt)
    assert "OpenSubtitles" not in out and out.startswith("1\n00:01:16,780") and "\n2\n00:01:19,700" in out
