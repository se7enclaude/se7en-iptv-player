"""What changed in each version, shown once after an update ("se7en has been updated")."""
from __future__ import annotations

CHANGES: dict[str, dict[str, list[str]]] = {
    "1.1.4": {
        "fr": ["Beaucoup plus rapide sur les gros catalogues (100 000 films) : l'accueil s'affiche en moins d'un dixième "
               "de seconde après le choix du profil, les pages Films, Séries, TV en direct et Guide s'ouvrent presque "
               "tout de suite.",
               "Revenir sur une page est instantané, et les premières pages de Films, Séries et TV en direct sont "
               "préparées pendant que tu es sur l'accueil.",
               "La première recherche ne fait plus attendre : l'index est prêt dès le lancement."],
        "en": ["Much faster on big catalogues (100,000 films): the home screen shows in under a tenth of a second after "
               "picking the profile, Films, Series, Live TV and Guide open almost at once.",
               "Going back to a page is instant, and the first pages of Films, Series and Live TV are prepared while "
               "you are on the home screen.",
               "The first search no longer waits: the index is ready at launch."],
    },
    "1.1.3": {
        "fr": ["Avancer et reculer dans un film marche enfin : un clic sur la barre de progression va au bon endroit, "
               "même chez les fournisseurs qui n'acceptent qu'une connexion à la fois.",
               "Après une coupure, un film repart là où il en était, plus au début."],
        "en": ["Seeking in films finally works: clicking the progress bar jumps to the right place, even with providers "
               "that only allow one connection at a time.",
               "After a drop, a film resumes where it was instead of from the start."],
    },
    "1.1.2": {
        "fr": ["« Qui regarde ? » au lancement : choisis ton profil (avec son code s'il est protégé), puis l'appli s'ouvre "
               "sur ton abonnement.",
               "Barre de progression comme sur YouTube pour les films et séries : sur toute la largeur, le temps visé "
               "s'affiche au survol, clique où tu veux pour avancer ou reculer, et l'heure de fin s'affiche à droite. "
               "Elle marche aussi sur les films dont le fichier ne donne pas sa durée.",
               "Bulles plus petites et plus discrètes, sur un léger voile sombre pour rester lisibles.",
               "Sous-titres : la liste dit clairement quand le fichier du fournisseur n'en contient aucun.",
               "Accueil plus rempli : programme en cours sur tes chaînes, films et séries ajoutés récemment, les mieux "
               "notés, et des catégories à explorer.",
               "Le menu du profil ne passe plus sous le tri des films."],
        "en": ["« Who's watching? » at launch: pick your profile (with its code if protected), then the app opens on "
               "your subscription.",
               "YouTube-like progress bar for films and series: full width, target time on hover, click anywhere to "
               "seek, end time on the right; also works when the file has no duration.",
               "Smaller, quieter bubbles over a light dark shade.",
               "Subtitles: the list says when the provider's file has none.",
               "Fuller home: what's on your channels now, recently added films and series, top rated, categories.",
               "The profile menu no longer goes under the films sort selector."],
    },
    "1.1.1": {
        "fr": ["Plein écran : la souris et le clavier marchent enfin. Bouge la souris : des bulles en verre apparaissent en "
               "bas au milieu (reculer / avancer de 10 s, pause, langue audio, sous-titres, chaîne − / +, son, quitter le "
               "plein écran). Double-clic ou Échap pour sortir.",
               "Le catalogue n'est plus rechargé à chaque lancement : seulement s'il a plus de 12 h ; le guide se recharge "
               "tout seul avant d'arriver à sa fin.",
               "Les infobulles « Marquer comme vu » des épisodes ne sont plus coupées."],
        "en": ["Full screen: mouse and keyboard finally work. Move the mouse: glass bubbles appear at the bottom (back / "
               "forward 10 s, pause, audio language, subtitles, channel − / +, sound, exit full screen). Double-click or "
               "Esc to leave.",
               "The catalogue is no longer reloaded at every launch: only when older than 12 h; the guide reloads before "
               "it runs out.",
               "Episode « Mark as watched » tooltips are no longer cut."],
    },
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
