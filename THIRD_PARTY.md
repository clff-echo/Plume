# Composants tiers

Plume est un projet indépendant, non affilié, non approuvé et non sponsorisé par Spotify. Spotify est une marque de ses propriétaires. L’accès au service reste soumis au compte Premium, aux autorisations et aux conditions de Spotify. Le moteur non officiel peut cesser de fonctionner si le protocole change.

- **Spotifyd 0.4.2** : moteur audio séparé, fondé sur librespot. Licence GPL-3.0, texte intégral dans `vendor/SPOTIFYD-LICENSE`. Sources correspondant au binaire : https://github.com/Spotifyd/spotifyd/tree/v0.4.2 ; archive source https://github.com/Spotifyd/spotifyd/archive/refs/tags/v0.4.2.tar.gz ; instructions et scripts de construction dans ce dépôt. Binaire upstream non modifié : https://github.com/Spotifyd/spotifyd/releases/tag/v0.4.2. La release contient `Spotifyd-0.4.2-corresponding-source.tar.gz` : source du composant, Cargo.lock, dépendances Rust et instructions de construction. Ce téléchargement est proposé à côté du paquet Linux.
- **spotify-player 0.25.1** : composant de connexion temporaire, binaire upstream non modifié, licence MIT dans `vendor/SPOTIFY-PLAYER-LICENSE`. Sources : https://github.com/aome510/spotify-player/tree/v0.25.1 ; version : https://github.com/aome510/spotify-player/releases/tag/v0.25.1. Son flux de connexion standard utilise l’application ncspot pour la bibliothèque et une autre autorisation pour librespot. Aucun identifiant développeur personnalisé n’est demandé à l’utilisateur.
- **librespot** : https://github.com/librespot-org/librespot, moteur inclus par Spotifyd.
- **GTK / PyGObject / libsecret** : bibliothèques système sous leurs licences respectives ; non incluses dans l’archive.
- **Spotify Web API** : connexion PKCE et commandes de bibliothèque/lecture ; aucun secret développeur embarqué.
- **LRCLIB** : https://lrclib.net, paroles recherchées uniquement après activation de l’option. Les droits sur les paroles restent à leurs ayants droit. Les paroles ne sont pas livrées avec Plume.

Les illustrations abstraites et l’icône Plume sont créées pour cette interface. Aucun logo Spotify n’est utilisé comme identité de l’application.

Référence de conception pour les optimisations de chargement : [Spotifast](https://github.com/crmne/spotifast), révision consultée `12deee4`, sous licence MIT. Principes étudiés : caches limités par compte, réutilisation des données affichées, regroupement des demandes et temporisation. Aucun code ni binaire Spotifast n’est inclus.

## Distribution Windows

- librespot 0.8.0, moteur audio construit depuis https://github.com/librespot-org/librespot/tree/v0.8.0 ; licence MIT incluse dans `vendor/LIBRESPOT-LICENSE` lors de la construction.
- spotify-player 0.25.1, binaire Windows officiel non modifié, licence MIT.
- Python, GTK, PyGObject, Cairo, GLib, GdkPixbuf, psutil et leurs dépendances sont inclus via MSYS2 et PyInstaller. Les textes de licence sont conservés dans le dossier `third-party-licenses` de l’installation ; les bibliothèques restent des fichiers séparés et remplaçables. Sources et recettes des paquets : https://github.com/msys2/MINGW-packages et https://packages.msys2.org. Sources GTK : https://download.gnome.org/sources/gtk/ ; PyGObject : https://download.gnome.org/sources/pygobject/.
