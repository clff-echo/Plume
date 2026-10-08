# Plume 0.6.0 · Ubuntu et Windows

Un client natif indépendant pour écouter ton compte Spotify Premium, retrouver tes playlists, changer d’appareil et afficher les paroles disponibles.

## Se connecter

1. Ouvre **Plume**.
2. Clique sur **Se connecter à Spotify**.
3. Connecte-toi et valide dans ton navigateur. Plume ouvre automatiquement ta bibliothèque une fois le compte confirmé.

La connexion est le premier écran. Les playlists et le lecteur apparaissent seulement après validation ; aux prochains lancements, Plume essaie de retrouver ta session. Si elle a expiré, le bouton de connexion réapparaît.

L’ouverture passe par le service de bureau Linux. Si rien ne s’affiche, les boutons **Ouvrir le navigateur**, **Choisir un autre navigateur** et **Copier le lien** restent disponibles pendant la connexion.

**Aucun compte développeur, aucun Client ID, aucun code à copier.**

Si Spotify confirme la connexion mais limite ensuite le chargement du compte, Plume conserve la session et affiche un compte à rebours. Le chargement du compte est limité à trois essais et 30 secondes au total. Si Spotify reste indisponible, Plume arrête l’attente et affiche **Réessayer le chargement**, sans effacer la session. Une réponse tardive ne peut pas relancer une attente annulée. L’accès au trousseau est lui aussi limité à huit secondes. La nouvelle tentative respecte le délai de Spotify sans ajouter systématiquement 15 à 120 secondes. Les premières nouvelles tentatives suivent ce délai, puis ralentissent si les refus se répètent. Un délai long demandé par Spotify reste respecté intégralement. **Mettre en pause** garde la session ; **Reprendre la connexion** retente le chargement sans rouvrir les autorisations. Une session réellement expirée demande une nouvelle connexion.

La première fois, Spotify peut demander deux validations. Utilise le même compte pour les deux. L’autorisation de bibliothèque peut afficher **ncspot** : Plume utilise la connexion standard du composant libre spotify-player, qui s’appuie sur cette application. Le composant est fourni avec Plume et s’arrête une fois la connexion obtenue.

Clique ensuite sur une playlist. Dans **Appareils**, choisis **Plume · Linux** pour écouter sur le PC ou ton téléphone pour y transférer la lecture. Ouvre Spotify sur le téléphone pour qu’il apparaisse dans la liste.

## Chargements plus rapides

- Les pages de playlists et leurs titres restent en cache cinq minutes ; les titres aimés quinze minutes. Revenir sur une page déjà chargée ne déclenche pas de nouvelle requête pendant cette durée.
- Un cache plus ancien reste affiché pendant son actualisation. Le bouton **Actualiser** permet de demander les dernières données ; les limitations Spotify restent respectées.
- Le compte déjà vérifié est réutilisé au prochain lancement avec la même session et les mêmes identifiants audio. Le renouvellement de l’autorisation est toujours vérifié, mais le profil n’est pas redemandé inutilement.
- Les chargements identiques simultanés sont regroupés. Le jeton fraîchement obtenu reste utilisé jusqu’à son expiration prévue au lieu d’être renouvelé après trente secondes.
- Le cache de métadonnées est limité à 1 Mo en mémoire et 8 Mo sur disque. Il est propre à la session et au compte, et supprimé à la déconnexion. Aucun audio n’est mis en cache.

Approches étudiées dans [Spotifast](https://github.com/crmne/spotifast) : cache par compte, affichage conservé pendant les actualisations et attente du délai Spotify. Plume garde son interface GTK et son moteur existants ; il ne reprend pas le code de Spotifast. Le quota de l’application publique reste partagé : ces optimisations n’annulent pas un blocage imposé par Spotify lors du tout premier accès.

Test sur 24 playlists fictives : dix retours sur la bibliothèque, 40,7 ms en moyenne, aucune requête réseau supplémentaire, 186,2 Mo RSS sous le plafond de 300 Mo. Ce résultat mesure la réouverture depuis le cache, pas la vitesse réelle du service Spotify. Rapport dans `verification/speed-check.json`.

## Interface

Disposition proche de Spotify : barre globale avec accueil et recherche, bibliothèque latérale avec pochettes et favoris, raccourcis de playlists, grille de grandes pochettes et lecteur centré. Les playlists ont une grande couverture et des colonnes titre, album et durée. Couleurs noir, gris et vert ; l’identité reste Plume, client indépendant.

## Au quotidien

- **Accueil** : tes playlists, leurs pochettes et un accès direct dans la bibliothèque à gauche.
- **Titres aimés** : tes favoris.
- **Rechercher** : utilise la barre en haut pour un titre ou un artiste, puis appuie sur Entrée.
- **Appareils** : l’icône de haut-parleur dans le lecteur ouvre les sorties audio. Le transfert conserve la lecture ou la pause.
- **Paroles** : active cette option dans Réglages, puis utilise l’icône de micro dans le lecteur. LRCLIB fournit les paroles quand elles existent.
- **Réglages** : qualité audio, paroles et déconnexion.

Plume n’offre pas toutes les fonctions du client officiel : pas de téléchargement hors ligne, de vidéos ni de garantie de disponibilité de chaque fonction Spotify. Son fonctionnement dépend de Spotify et des composants libres employés. Le quota de connexion est partagé avec d’autres utilisateurs ; Plume respecte les délais imposés par Spotify lorsqu’il limite les requêtes.

## Installer

Le paquet contient l’interface et les deux composants nécessaires pour Linux x86-64. Sur Ubuntu / Debian, si GTK manque :

```sh
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-secret-1
```

Dans le dossier extrait :

```sh
./install.sh
```

Plume apparaît dans le menu des applications. L’installation se fait dans ton dossier personnel, sans service permanent. Tu peux aussi lancer directement `./launch.sh`.

Sur Linux ARM64, lance d’abord `python3 scripts/fetch-engine.py` pour récupérer les deux binaires adaptés, vérifiés par leur empreinte.

## Mémoire

Le lanceur place l’interface, le moteur audio et le composant temporaire de connexion dans un seul groupe limité à **300 000 000 octets**, sans swap. Il nécessite une session systemd utilisateur et cgroups v2 avec contrôleur mémoire. Le navigateur de connexion est lancé en dehors de ce budget.

**Si le plafond est atteint, Linux peut arrêter l’application.** La barre latérale indique la mémoire mesurée et l’état du plafond. Le lanceur refuse de démarrer si la limite ne peut pas être appliquée. Pour un diagnostic volontairement sans plafond : `./launch.sh --no-cap`.

L’interface est native GTK, sans Electron ni navigateur intégré. Les listes sont paginées ; aucun cache audio n’est activé. Les pochettes sont réduites à 192 pixels et le cache est limité à 24 images, avec deux téléchargements simultanés au maximum. Le compteur RSS additionne les processus et peut compter plusieurs fois des bibliothèques partagées, contrairement à la mémoire imputée au groupe Linux.

## Session et confidentialité

Le mot de passe est saisi uniquement chez Spotify. Plume ne lit pas les cookies du navigateur, ni les sessions d’autres applications.

Une autorisation personnelle est obtenue par le composant intégré spotify-player. Les données temporaires de connexion sont supprimées ensuite. Le renouvellement de la bibliothèque est enregistré dans le trousseau Linux lorsqu’il est disponible ; sinon, dans un fichier privé accessible uniquement à ton utilisateur. Les identifiants audio sont eux aussi conservés dans un dossier privé. Ces fichiers de secours ne sont pas chiffrés par le trousseau.

**Se déconnecter** arrête le moteur et supprime les identifiants de Plume. Pour révoquer les autorisations côté Spotify, utilise les paramètres de ton compte Spotify.

Les paroles sont optionnelles. Les activer envoie à LRCLIB le titre, l’artiste, l’album et la durée du morceau quand tu ouvres leur page ; aucun jeton Spotify ne lui est transmis. Aucune télémétrie ou publicité n’est ajoutée par Plume.

## Vérification et limites

- 28 tests automatisés : sessions, renouvellement, séparation des comptes, suppression des données temporaires, permissions des fichiers, réponses Spotify, paroles et arrêt du moteur.
- Le véritable bouton et son composant de connexion ont généré une autorisation Spotify sous le plafond de 300 Mo ; le test a été annulé avant toute connexion à un compte. Pic mesuré : 164,9 Mo RSS.
- L’ouverture du navigateur par le portail Linux a été confirmée à l’écran par l’utilisateur.
- Huit vérifications du parcours GTK : pages cachées avant connexion, erreur du navigateur sans abandon de session, ouverture de la bibliothèque, déconnexion, annulation, session expirée et reprise automatique après une limitation Spotify sans nouvelle autorisation.
- Les sept vues GTK sont vérifiées avec des données fictives. Les captures sont dans `verification/`.
- Le test de navigation, 50 titres fictifs et moteur au repos, a mesuré un pic de 207,3 Mo RSS sous plafond Linux actif. Les mesures exactes et leur scénario sont dans `verification/`.
- **La validation complète des deux autorisations, la lecture réelle et le transfert vers un téléphone nécessitent encore ton compte.** L’ouverture de la page de connexion ne suffit pas à prouver leur fonctionnement. Aucun accès à « tout Spotify » n’est garanti.

## Composants

- [spotify-player 0.25.1](https://github.com/aome510/spotify-player/tree/v0.25.1) : connexion par navigateur avec sa configuration standard.
- [Spotifyd 0.4.2](https://github.com/Spotifyd/spotifyd/releases/tag/v0.4.2) : moteur audio basé sur librespot.
- [LRCLIB](https://lrclib.net) : paroles optionnelles.

Plume n’est ni affilié, ni sponsorisé, ni approuvé par Spotify. Les licences sont dans `THIRD_PARTY.md` et `vendor/`.

## Tests de développement

```sh
python3 -m unittest discover -s tests -v
python3 scripts/smoke-ui.py
python3 scripts/check-login-flow.py
systemd-run --user --scope -p MemoryMax=300000000 -p MemorySwapMax=0 python3 scripts/check-memory.py
```

Les tests graphiques nécessitent une fenêtre visible ou un écran virtuel Xvfb. Ils utilisent un dossier temporaire et des données fictives, sans lire ta session Spotify.

## Releases Windows et Ubuntu

Téléchargements : https://github.com/clff-echo/Plume/releases

Windows 10/11 x64 : installer `Plume-0.6.0-windows-x64.exe`. GTK et Python sont inclus, aucun environnement de développement nécessaire. Le moteur audio Windows est librespot 0.8.0 ; les données de session sont dans `%LOCALAPPDATA%/Plume`. Elles héritent des permissions du profil Windows ; le fichier de secours n’est pas chiffré par un trousseau. Le plafond strict de 300 Mo décrit plus haut est réservé à Linux. La première version Windows est expérimentale : son démarrage est testé, mais une écoute réelle reste à valider.

Ubuntu 24.04 x64 : `sudo apt install ./plume_0.6.0_amd64.deb`, puis ouvrir Plume dans le menu. Les dépendances système sont installées par APT.

Le ZIP source contient le code Plume, les tests et les scripts de construction. Les binaires tiers sont récupérés depuis leurs versions officielles et vérifiés. Pour Linux : `python3 scripts/fetch-engine.py`, puis `python3 scripts/build-deb.py`. Pour Windows, le workflow `.github/workflows/release.yml` construit librespot, empaquette GTK avec PyInstaller et crée un installateur Inno Setup.

Les téléchargements ne sont publiés qu’après réussite des deux constructions et des tests. Le workflow prépare une release brouillon pour vérification.
