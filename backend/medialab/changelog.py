"""What changed in each version, shown once after an update ("se7en has been updated")."""
from __future__ import annotations

CHANGES: dict[str, dict[str, list[str]]] = {
    "1.0.3": {
        "fr": ["Plein écran réparé : la vidéo remplit tout l'écran (elle restait sur la moitié du haut), et la fenêtre "
               "passe bien par-dessus la barre des tâches même quand elle était agrandie."],
        "en": ["Full screen fixed: the video fills the whole screen (it stayed on the top half), and the window goes "
               "over the taskbar even when it was maximized."],
    },
    "1.0.2": {
        "fr": ["Abonnements refusés alors que les codes sont bons : certains fournisseurs n'acceptent que les lecteurs "
               "qu'ils connaissent. se7en IPTV Player réessaie maintenant tout seul en se présentant comme VLC, puis "
               "comme un navigateur, et garde ce qui marche pour la lecture et les enregistrements."],
        "en": ["Subscriptions refused despite good codes: some providers only accept the players they know. se7en IPTV "
               "Player now retries on its own as VLC, then as a browser, and keeps what works for playback and "
               "recordings."],
    },
    "1.0.1": {
        "fr": ["Nouveau logo : une télé rouge avec le 7 sur l'écran, pour ne plus confondre avec se7en Censored."],
        "en": ["New logo: a red TV with the 7 on its screen, so it no longer looks like se7en Censored."],
    },
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
