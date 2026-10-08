"""Smoke test the packaged Windows app, with isolated fake data and no login."""
import os
from pathlib import Path
import subprocess
import tempfile
import time
import ctypes
exe = Path('dist/Plume/Plume.exe').resolve()
with tempfile.TemporaryDirectory(prefix='plume-smoke-') as temp:
    proc = subprocess.Popen([str(exe), '--demo'], env={**os.environ, 'PLUME_HOME':temp})
    try:
        deadline = time.monotonic()+45
        visible = []
        while time.monotonic()<deadline:
            if proc.poll() is not None: raise SystemExit('Packaged Plume exited before showing its window')
            def visit(hwnd, _):
                pid = ctypes.c_ulong()
                ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value == proc.pid and ctypes.windll.user32.IsWindowVisible(hwnd): visible.append(hwnd)
                return True
            callback = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(visit)
            ctypes.windll.user32.EnumWindows(callback, 0)
            if visible: break
            time.sleep(.5)
        if not visible: raise SystemExit('No visible Plume window after 45 seconds')
        print('Packaged Windows app: visible demo window confirmed')
        ctypes.windll.user32.PostMessageW(visible[0], 0x10, 0, 0)
        proc.wait(timeout=15)
        if proc.returncode: raise SystemExit(f'Unexpected application exit: {proc.returncode}')
    finally:
        if proc.poll() is None: proc.terminate(); proc.wait(timeout=10)
