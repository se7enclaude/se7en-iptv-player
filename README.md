# se7en IPTV Player

**[Télécharger se7en IPTV Player](https://github.com/se7enclaude/se7en-iptv-player/releases/latest/download/se7en-IPTV-Player-Setup.exe)**
(ou depuis Seventh OS)

Lecteur IPTV pour Windows : ton abonnement **Xtream Codes** (serveur, utilisateur, mot de passe) ou ta liste
**M3U / M3U8** avec un guide **XMLTV**. se7en IPTV Player ne fournit aucune chaîne, aucune liste ni aucun abonnement.

- TV en direct : catégories, favoris, zapping au clavier, guide des programmes en grille sur 7 jours, rappels.
- Films et séries : fiches, reprise, épisode suivant, « Continuer à regarder ».
- Replay (catch-up) et enregistrements FFmpeg en copie de flux, sans jamais dépasser `max_connections`.
- Plusieurs abonnements, profils, contrôle parental par code, sauvegarde chiffrée.
- Identifiants chiffrés par Windows (DPAPI), adresses de flux masquées dans les journaux.

## Code

Base : la même que se7en Clip / se7en Censored (`backend/medialab` en Python + FastAPI, interface React dans
`frontend`, fenêtre WebView2, installateur Inno Setup, mises à jour par `latest.json`).

- `backend/medialab/stream/xtream.py`, `m3u.py`, `xmltv.py` : clients et analyseurs tolérants.
- `backend/medialab/stream/store.py`, `sync.py`, `catalog.py` : cache SQLite, mise à jour en arrière-plan, listes.
- `backend/medialab/stream/player.py` : libmpv dans une fenêtre enfant de l'appli, bandeau dessiné par mpv (ASS).
- `backend/medialab/stream/recorder.py` : enregistrements et compteur de connexions.
- `backend/medialab/stream/api.py` : routes HTTP.

```
backend\.venv\Scripts\python -m pytest backend\tests\test_stream_core.py backend\tests\test_stream_api.py
powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Repo se7enclaude/se7en-iptv-player
powershell -ExecutionPolicy Bypass -File packaging\publish.ps1
```

## Licence

AGPL-3.0. Le lecteur [mpv](https://mpv.io) (libmpv, build GPL-3.0 de
[shinchiro](https://github.com/shinchiro/mpv-winbuild-cmake)) est fourni dans `bin\mpv`.
