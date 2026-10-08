#!/usr/bin/env python3
"""Build an amd64 Debian package without root privileges."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plume import __version__
ROOT = Path(__file__).resolve().parents[1]
out = ROOT/'dist'
out.mkdir(exist_ok=True)
for name in ('spotifyd', 'spotify_player'):
    if not (ROOT/'vendor'/name).is_file():
        raise SystemExit('Run python3 scripts/fetch-engine.py first')
with tempfile.TemporaryDirectory(prefix='plume-deb-') as temp:
    stage = Path(temp)
    app = stage/'opt/plume'
    app.mkdir(parents=True)
    for name in ('plume','assets','vendor'):
        shutil.copytree(ROOT/name, app/name, ignore=shutil.ignore_patterns('__pycache__','*.pyc','*.exe'))
    for name in ('launch.sh','README.md','THIRD_PARTY.md'):
        shutil.copy2(ROOT/name, app/name)
    (app/'launch.sh').chmod(0o755)
    for binary in ('spotifyd','spotify_player'):
        (app/'vendor'/binary).chmod(0o755)
    bindir = stage/'usr/bin'; bindir.mkdir(parents=True)
    (bindir/'plume').write_text('#!/bin/sh\nexec /opt/plume/launch.sh "$@"\n')
    (bindir/'plume').chmod(0o755)
    desktop = stage/'usr/share/applications'; desktop.mkdir(parents=True)
    (desktop/'io.plume.Client.desktop').write_text('[Desktop Entry]\nType=Application\nName=Plume\nComment=Client musical indépendant\nExec=plume\nIcon=io.plume.Client\nTerminal=false\nCategories=AudioVideo;Audio;Player;\nStartupWMClass=io.plume.Client\n')
    icons = stage/'usr/share/icons/hicolor/scalable/apps'; icons.mkdir(parents=True)
    shutil.copy2(ROOT/'assets/plume.svg', icons/'io.plume.Client.svg')
    control = stage/'DEBIAN'; control.mkdir()
    size = sum(p.stat().st_size for p in stage.rglob('*') if p.is_file())//1024
    (control/'control').write_text(f'''Package: plume
Version: {__version__}
Section: sound
Priority: optional
Architecture: amd64
Maintainer: clff-echo <216766912+clff-echo@users.noreply.github.com>
Installed-Size: {size}
Depends: python3 (>= 3.10), python3-gi, python3-gi-cairo, gir1.2-gtk-4.0 (>= 4.6), gir1.2-gdkpixbuf-2.0, gir1.2-secret-1, libasound2 | libasound2t64, libdbus-1-3, libpulse0, libssl3 | libssl3t64, libc6 (>= 2.35), libgcc-s1, systemd, dbus-user-session, xdg-utils
Recommends: gnome-keyring, xdg-desktop-portal, xdg-desktop-portal-gtk
Homepage: https://github.com/clff-echo/Plume
Description: Client musical natif indépendant pour Spotify Premium
 Interface GTK avec playlists, recherche, lecture et paroles optionnelles.
 Session utilisateur systemd et cgroups v2 requis pour le plafond mémoire.
''')
    target = out/f'plume_{__version__}_amd64.deb'
    subprocess.run(['dpkg-deb','--root-owner-group','--build',str(stage),str(target)],check=True)
    print(target)
