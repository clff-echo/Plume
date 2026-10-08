#!/bin/sh
set -eu
PLUME_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PLUME_DEST="${XDG_DATA_HOME:-$HOME/.local/share}/plume"
python3 -c 'import gi; gi.require_version("Gtk", "4.0"); from gi.repository import Gtk' || {
    printf '%s\n' 'Installe Python 3, PyGObject et GTK 4. Sur Ubuntu/Debian :' 'sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-secret-1' >&2
    exit 1
}
mkdir -p "$PLUME_DEST" "${XDG_DATA_HOME:-$HOME/.local/share}/applications" "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
if [ "$PLUME_DIR" != "$PLUME_DEST" ]; then
    cp -R "$PLUME_DIR/plume" "$PLUME_DIR/assets" "$PLUME_DIR/vendor" "$PLUME_DIR/scripts" "$PLUME_DIR/launch.sh" "$PLUME_DIR/README.md" "$PLUME_DIR/THIRD_PARTY.md" "$PLUME_DEST/"
fi
chmod +x "$PLUME_DEST/launch.sh"
cp "$PLUME_DIR/assets/plume.svg" "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps/io.plume.Client.svg"
python3 - "$PLUME_DEST" "${XDG_DATA_HOME:-$HOME/.local/share}/applications/io.plume.Client.desktop" <<'PY'
from pathlib import Path
import sys
launcher = str(Path(sys.argv[1])/'launch.sh')
launcher = launcher.replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%')
Path(sys.argv[2]).write_text('[Desktop Entry]\nType=Application\nName=Plume\nComment=Un lecteur musical léger et indépendant\nExec="'+launcher+'"\nIcon=io.plume.Client\nTerminal=false\nCategories=AudioVideo;Audio;Player;\nStartupNotify=true\nStartupWMClass=io.plume.Client\n')
PY
printf '%s\n' 'Plume est disponible dans le menu des applications.' 'Clique sur Se connecter à Spotify, puis valide dans ton navigateur.'
