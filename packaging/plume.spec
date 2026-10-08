from pathlib import Path
root = Path(SPECPATH).parent
licenses = Path('/ucrt64/share/licenses')
a = Analysis([str(root/'packaging/windows-entry.py')], pathex=[str(root)],
    binaries=[(str(root/'vendor/librespot.exe'), 'vendor'), (str(root/'vendor/spotify_player.exe'), 'vendor')],
    datas=[(str(root/'assets'), 'assets'), (str(root/'vendor/*LICENSE*'), 'licenses'),
           (str(root/'THIRD_PARTY.md'), '.'), (str(root/'README.md'), '.')],
    hiddenimports=['psutil', 'gi.repository.Gtk', 'gi.repository.Gdk', 'gi.repository.GdkPixbuf'],
    hooksconfig={'gi': {'module-versions': {'Gtk':'4.0', 'Gdk':'4.0'}, 'icons':['Adwaita']}},
    excludes=['gi.repository.Secret'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Plume', debug=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='Plume')
