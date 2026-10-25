"""se7en IPTV Player : lecteur IPTV (Xtream Codes, M3U, EPG XMLTV).

- ``xtream``  : client de l'API Xtream Codes (player_api.php), adresses de lecture, masquage des identifiants ;
- ``m3u``     : lecture des listes M3U / M3U8 (en flux) ;
- ``xmltv``   : lecture des guides XMLTV (en flux, gzip) ;
- ``store``   : base SQLite locale (catalogue, guide, profils, favoris, reprise, enregistrements) ;
- ``sync``    : mise à jour du catalogue et du guide en arrière-plan ;
- ``player``  : lecteur libmpv dans la fenêtre de l'appli, contrôles dessinés par mpv ;
- ``recorder``: enregistrements (FFmpeg en copie de flux) et programmation ;
- ``api``     : routes HTTP pour l'interface.

L'appli ne contient ni ne propose aucune source : l'utilisateur ajoute son propre abonnement.
"""
