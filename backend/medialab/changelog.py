"""What changed in each version, shown once after an update ("se7en has been updated")."""
from __future__ import annotations

CHANGES: dict[str, dict[str, list[str]]] = {
    "1.0.0": {
        "fr": ["Première version : ton abonnement Xtream Codes ou ta liste M3U, la TV en direct avec le guide des "
               "programmes, les films et séries avec reprise, le replay et les enregistrements, plusieurs profils "
               "avec contrôle parental."],
        "en": ["First version: your Xtream Codes subscription or M3U playlist, live TV with the programme guide, "
               "movies and series with resume, catch-up and recordings, several profiles with parental control."],
    },
}


def since(previous: str, current: str) -> dict[str, list[str]]:
    """Everything that changed after ``previous`` up to ``current``, newest first ("older": since before 1.2)."""
    from .updater import is_newer, parse_version
    out: dict[str, list[str]] = {}
    for v in sorted(CHANGES, key=parse_version, reverse=True):
        if is_newer(v, current):
            continue
        if previous != "older" and not is_newer(v, previous):
            continue
        for lang, items in CHANGES[v].items():
            out.setdefault(lang, []).extend(items)
    return out
