"""Small OS boundary shared by the desktop and supervised subprocesses."""
import os
import subprocess
import sys
from pathlib import Path

WINDOWS = sys.platform == "win32"
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))

def process_options():
    if WINDOWS:
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {"umask": 0o077}

def config_home():
    if WINDOWS:
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "Plume"
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "plume"
