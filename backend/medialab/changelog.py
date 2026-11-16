"""What changed in each version, shown once after an update ("se7en has been updated")."""
from __future__ import annotations

CHANGES: dict[str, dict[str, list[str]]] = {
    "1.5.0": {
        "fr": ["Interpolation d'images : de nouvelles images sont calculées entre celles de la vidéo, comme le « 100 Hz » des téléviseurs. 25 i/s → 50 et 50 i/s → 100 pour le foot et les chaînes, jamais plus que la fréquence de ton écran. Jusqu'à la HD ; la 4K passe telle quelle. À activer dans Paramètres › Lecture : la première fois, deux modules libres (VapourSynth et MVTools, ≈ 25 Mo) sont téléchargés et vérifiés."],
        "en": ["Frame interpolation: new frames are computed between the video's own, like a TV's \"100 Hz\" mode. 25 fps → 50 and 50 fps → 100 for football and channels, never above your screen's refresh rate. Up to HD; 4K plays as is. Turn it on in Settings › Playback: the first time, two free modules (VapourSynth and MVTools, about 25 MB) are downloaded and checked."],
    },
    "1.4.0": {
        "fr": ["Passer l'intro : l'appli reconnaît le générique d'ouverture d'une série à sa musique (elle l'apprend dès que tu as vu deux épisodes depuis le début) et affiche « Passer l'intro » pendant qu'il passe. Aussi quand le fichier a un chapitre « Intro ».",
               "Sous-titres recalés automatiquement : les sous-titres trouvés en ligne ou ajoutés à la main qui arrivent trop tôt, trop tard ou qui dérivent sont recalés sur les voix du film après quelques minutes (réglable dans Paramètres › Lecture). Si tu règles le décalage toi-même, l'appli n'y touche plus.",
               "Télécommande iPhone améliorée : cherche un film, une série ou une chaîne et lance-le sur le PC, reprends ce que tu regardais, choisis l'épisode d'une série, avance dans le film avec la barre de lecture, change de langue ou de sous-titres, « Passer l'intro » et « Épisode suivant » depuis le téléphone."],
        "en": ["Skip intro: the app recognises a series' opening credits by their music (it learns them once you have watched two episodes from the start) and shows \"Skip intro\" while they play. Also when the file has an \"Intro\" chapter.",
               "Subtitles resynced automatically: subtitles found online or added by hand that come too early, too late or drift are realigned on the film's voices after a few minutes (Settings › Playback). If you set the delay yourself, the app leaves it alone.",
               "Better iPhone remote: search a film, series or channel and start it on the PC, resume what you were watching, pick a series episode, move through the film with the progress bar, change language or subtitles, \"Skip intro\" and \"Next episode\" from the phone."],
    },
    "1.3.0": {
        "fr": ["Épisode suivant automatique : pendant le générique de fin, une carte propose l'épisode suivant et le lance après 10 s. « Rester » pour regarder le générique (réglable dans Paramètres › Lecture).",
               "Sous-titres décalés ou trop petits : dans la liste des sous-titres, règle le décalage (par demi-seconde) et la taille. La taille est gardée pour les films suivants.",
               "En plein écran, le bouton ⚙ donne la vitesse de lecture et le format d'image (Auto, 16:9, 4:3, 2,35, Remplir).",
               "Image fluide : les images sont calées sur la fréquence de ton écran et fondues entre elles, pour des panoramiques et du foot sans saccades (activable ou non dans Paramètres › Lecture).",
               "4K HDR mieux rendue : vraie image HDR si Windows est en mode HDR, sinon couleurs et luminosité adaptées scène par scène à ton écran.",
               "Fiches plus complètes, même sans clé TMDB : note IMDb et nombre de votes, distribution avec photos (les têtes d'affiche d'abord), réalisation et bande-annonce. La bande-annonce se lit maintenant dans l'appli."],
        "en": ["Next episode, automatically: during the end credits a card offers the next episode and starts it after 10 s. \"Stay\" to watch the credits (can be turned off in Settings › Playback).",
               "Subtitles out of sync or too small: in the subtitle list, adjust the delay (by half a second) and the size. The size is kept for the next films.",
               "In full screen, the ⚙ button gives playback speed and picture format (Auto, 16:9, 4:3, 2.35, Fill).",
               "Smooth picture: frames are timed to your screen's refresh rate and blended, for judder-free pans and football (on or off in Settings › Playback).",
               "Better 4K HDR: real HDR if Windows is in HDR mode, otherwise colours and brightness adapted scene by scene to your screen.",
               "Richer film and series pages, even without a TMDB key: IMDb rating and vote count, cast with photos (stars first), director and trailer. Trailers now play inside the app."],
    },
    "1.2.1": {
        "fr": ["Nouveau look du lecteur en plein écran : plus de bulles, des icônes nettes comme sur les grands services de streaming, une barre de lecture fine, le titre propre (« MobLand · S1 E2 · Épisode 2 » au lieu de « |FR| MobLand 01x02 (UHD) ») avec un badge 4K, et des listes de pistes plus lisibles.", "En fenêtre, plus de double barre : les boutons sont sous la vidéo, et sur l'image ne s'affichent que les messages.", "Les boutons Capture d'écran, Statistiques et Arrêter ont été retirés de la barre du lecteur."],
        "en": ["New full-screen player look: no more bubbles, crisp icons like the big streaming services, a thin progress bar, a clean title (\"MobLand · S1 E2 · Episode 2\" instead of \"|FR| MobLand 01x02 (UHD)\") with a 4K badge, and clearer track lists.", "In a window, no more double bar: the buttons sit under the video, and only messages show on the picture.", "The Screenshot, Statistics and Stop buttons were removed from the player bar."],
    },
    "1.2.0": {
        "fr": ["Toutes les versions : sur la fiche d'un film ou d'une série, le bouton « Toutes les versions » montre le même titre dans toutes les langues du catalogue (Français, Anglais, Espagnol, Arabe, Turc…) et toutes ses qualités (4K UHD, 1080p, Blu-ray, HEVC…). Un clic ouvre la version, ▶ la lance.", "Sous-titres pour tous les films et toutes les séries : quand la vidéo n'en a pas dans ta langue, l'appli les trouve sur OpenSubtitles et les ajoute toute seule (deux par langue, l'anglais en secours). Si le film est dans une autre langue que la tienne, le premier sous-titre s'affiche directement.", "Corrigé : un enregistrement pouvait finir en des dizaines de morceaux identiques quand le fournisseur renvoyait sa courte vidéo d'attente. Les copies ne sont plus gardées, les essais s'espacent, l'appli te dit pourquoi si ça dure, et les morceaux sont réunis en un seul fichier à la fin."],
        "en": ["All versions: on a film or series page, the \"All versions\" button shows the same title in every language of the catalogue (French, English, Spanish, Arabic, Turkish…) and every quality (4K UHD, 1080p, Blu-ray, HEVC…). Click to open a version, ▶ to play it.", "Subtitles for every film and series: when the video has none in your language, the app finds them on OpenSubtitles and adds them by itself (two per language, English as a backup). If the film is in another language than yours, the first subtitle shows right away.", "Fixed: a recording could end up as dozens of identical pieces when the provider kept sending its short waiting video. Copies are no longer kept, retries slow down, the app tells you why if it goes on, and the pieces are joined into one file at the end."],
    },
    "1.1.9": {
        "fr": ["Plus fluide : les affiches et les logos sont téléchargés une seule fois, réduits à la taille affichée et gardés sur ton PC. Le défilement des films ne saccade plus, et tout s'affiche tout de suite au lancement suivant.", "Les affiches apparaissent en fondu au lieu de surgir.", "Changer d'onglet est plus rapide : Accueil, Films, Séries et Guide restent en mémoire, et tu retrouves ta liste à l'endroit où tu l'avais laissée.", "Corrigé : sur un gros catalogue, la liste des films pouvait mettre 0,3 s par page (après la première mise à jour du catalogue) ; elle répond maintenant en quelques millisecondes. Le tri par nom est aussi instantané."],
        "en": ["Smoother: posters and logos are downloaded once, resized to the size shown and kept on your PC. Scrolling through films no longer stutters, and everything shows up at once next time.", "Posters fade in instead of popping up.", "Switching tabs is faster: Home, Films, Series and Guide stay in memory, and your list is where you left it.", "Fixed: on a large catalogue the film list could take 0.3 s per page (after the first catalogue update); it now answers in a few milliseconds. Sorting by name is instant too."],
    },
    "1.1.8": {
        "fr": ["Corrigé : une chaîne qui se coupe repart toute seule au lieu de rester figée, et un replay coupé reprend là où il en était.", "Corrigé : les replays ne sont plus décalés d'une heure.", "Corrigé : si la mise à jour du guide des programmes échoue, l'ancien guide est gardé (il ne se vide plus).", "Zapping plus rapide, et l'appli démarre plus vite.", "Quand tu fermes l'appli, le lecteur, les enregistrements et FFmpeg s'arrêtent proprement."],
        "en": ["Fixed: a channel that cuts out restarts on its own instead of freezing, and an interrupted replay resumes where it was.", "Fixed: replays are no longer one hour off.", "Fixed: if the programme guide update fails, the old guide is kept (it no longer goes empty).", "Faster channel switching, and the app starts faster.", "When you close the app, the player, recordings and FFmpeg stop cleanly."],
    },
    "1.1.7": {
        "fr": ["Composants : chacun affiche « À jour (version X, vérifié le …) » ou « Mise à jour disponible » (le lecteur vidéo (libmpv) et FFmpeg). Un composant à jour n'est plus jamais retéléchargé, et l'appli te prévient quand une nouvelle version sort (vérification toutes les 12 h).", "Corrigé : une mise à jour de FFmpeg ou de Deno était téléchargée mais pas utilisée (le bouton retéléchargeait en boucle).", "Corrigé : un ancien yt-dlp téléchargé pouvait masquer le yt-dlp plus récent fourni avec l'appli."],
        "en": ["Components: each one shows \"Up to date (version X, checked on …)\" or \"Update available\". A component that is up to date is never downloaded again, and the app tells you when a new version is out (checked every 12 h).", "Fixed: an FFmpeg or Deno update was downloaded but not used (the button downloaded it again and again).", "Fixed: an old downloaded yt-dlp could hide the newer one shipped with the app."],
    },
    "1.1.6": {
        "fr": ["Toutes les pistes accessibles : avec beaucoup de sous-titres (30 et plus), le haut de la liste sortait de "
               "l'écran ; elle s'affiche maintenant en colonnes.",
               "Noms de pistes lisibles : « Grec », « Coréen », « Version originale », « Français · VFQ · 5.1 », "
               "« Français · Forcés », « Français · SDH » au lieu de codes ou de « Français » en double.",
               "Plus de sous-titres français affichés d'office sur un film en français : seuls les sous-titres forcés "
               "(passages en langue étrangère) s'affichent tout seuls.",
               "Ta langue est retenue : passe un film en anglais ou coupe les sous-titres, le film suivant s'ouvre pareil. "
               "Et une piste choisie à la main ne passe plus au film suivant (mauvaise langue, voire pas de son).",
               "Les sauts dans la barre tombent juste : ils s'arrêtaient jusqu'à 10 s avant l'endroit cliqué."],
        "en": ["All tracks reachable: with many subtitles (30+), the top of the list went off screen; it now uses columns.",
               "Readable track names (Greek, Korean, Original version, French · 5.1, Forced, SDH) instead of codes.",
               "No more French subtitles shown by default over French audio: only forced subtitles show by themselves.",
               "Your language is remembered for the next film, and a hand-picked track number no longer carries over.",
               "Seeking lands exactly where you click (it stopped up to 10 s earlier)."],
    },
    "1.1.5": {
        "fr": ["Le minuteur et l'heure de fin avancent enfin pendant le film, et la barre de progression suit.",
               "Langues audio et sous-titres : la liste montre enfin les pistes du film (elle disait toujours « aucun "
               "sous-titre ») et tu peux en changer.",
               "Nouveau réglage du volume : une réglette en verre à côté du bouton du son (clic ou glisser), plus de gros "
               "« Volume » au milieu de l'image ; barres plus jolies dans la fenêtre.",
               "Fini les « connexion perdue · nouvelle tentative » au zapping : beaucoup de serveurs mettent 1 à 3 s à "
               "libérer la chaîne précédente et refusaient la nouvelle ; l'appli patiente maintenant. Si le serveur "
               "refuse vraiment (abonnement utilisé ailleurs), le message le dit."],
        "en": ["The timer and end time finally move during the film, and the progress bar follows.",
               "Audio languages and subtitles: the list finally shows the film's tracks and you can switch.",
               "New volume control: a glass slider next to the sound button (click or drag); nicer bars in the window.",
               "No more « connection lost · retry » when zapping: many servers take 1-3 s to free the previous channel "
               "and refused the new one; the app now waits. If the server really refuses, the message says so."],
    },
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
