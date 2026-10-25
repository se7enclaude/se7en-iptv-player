"""What changed in each version, shown once after an update ("se7en has been updated")."""
from __future__ import annotations

CHANGES: dict[str, dict[str, list[str]]] = {
    "1.1.0": {
        "fr": ["Nouveau plein écran : la vidéo prend tout l'écran, avec un bouton « Quitter le plein écran » (ou Échap), "
               "Chaîne − / Chaîne + et le bandeau du programme. Image plus fluide (chaînes entrelacées désentrelacées, "
               "images calées sur l'écran).",
               "Zapping instantané : la chaîne suivante est ouverte d'avance (abonnements avec au moins 2 connexions).",
               "SD / HD / FHD / 4K regroupés : une seule ligne par chaîne ou par film, la version de ton choix d'office.",
               "Recherche intelligente : tolère les fautes, cherche aussi par acteur, réalisateur, genre et catégorie.",
               "Fiches films et séries enrichies : genres, distribution, versions, titres similaires ; résumés en français, "
               "photos et bandes-annonces avec ta clé TMDB (optionnelle).",
               "Télécommande sur le téléphone : scanne le QR code dans Réglages → Télécommande."],
        "en": ["New full screen: the video fills the screen, with an « Exit full screen » button (or Esc), channel up / down "
               "and the programme banner. Smoother picture (interlaced channels deinterlaced, frames synced to the screen).",
               "Instant zapping: the next channel is opened in advance (subscriptions with at least 2 connections).",
               "SD / HD / FHD / 4K grouped: one line per channel or film, your preferred version by default.",
               "Smart search: typo tolerant, also by actor, director, genre and category.",
               "Richer film and series sheets: genres, cast, versions, similar titles; French summaries, photos and "
               "trailers with your (optional) TMDB key.",
               "Phone remote: scan the QR code in Settings → Phone remote."],
    },
    "1.0.4": {
        "fr": ["Plus fluide : le défilement ne saccade plus (accueil, films, chaînes, guide), et les pages s'affichent "
               "en entier trois fois plus vite.",
               "Les infobulles des boutons du lecteur ne sont plus coupées par la vidéo."],
        "en": ["Smoother: scrolling no longer stutters (home, movies, channels, guide), and pages show in full three "
               "times faster.",
               "Player button tooltips are no longer cut by the video."],
    },
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
