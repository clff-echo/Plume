#!/usr/bin/env python3
"""Exercise GTK pages and the actual idle audio engine in a capped cgroup."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ['GSK_RENDERER'] = 'cairo'
runtime = tempfile.TemporaryDirectory(prefix='plume-memory-')
os.environ['PLUME_HOME'] = runtime.name
from plume.app import Plume, GLib, box, clear
from plume.engine import memory_limit, memory_usage

app = Plume(demo=True)
samples = []
errors = []
started = time.monotonic()
ticks = 0
engine_pid = None
rows = [{'item': {'id':str(i), 'uri':'spotify:track:'+str(i), 'name':f'Titre de vérification {i}',
                  'artists':[{'name':'Artiste de test'}], 'duration_ms':180000}} for i in range(50)]


def cgroup_current():
    for line in Path('/proc/self/cgroup').read_text().splitlines():
        if line.startswith('0::'):
            return int((Path('/sys/fs/cgroup')/line[3:].lstrip('/')/'memory.current').read_text())


def exercise():
    global ticks, engine_pid
    try:
        if not engine_pid:
            app.engine.start()
            engine_pid = app.engine.process.pid
        ticks += 1
        if ticks % 3 == 0:
            app.navigate('settings')
        elif ticks % 3 == 1:
            app.navigate('library')
        else:
            clear(app.content)
            container = box()
            app.content.append(container)
            app.render_tracks({'items':rows}, container)
        samples.append({'second':round(time.monotonic()-started, 2), 'rss_bytes':memory_usage(),
                        'cgroup_bytes':cgroup_current(), 'engine_running':app.engine.running})
        if ticks >= 24:
            app.quit()
            return False
    except Exception:
        errors.append(traceback.format_exc())
        app.quit()
        return False
    return True


cap = memory_limit()
if cap is None or not 299_990_000 <= cap <= 300_000_000:
    raise SystemExit('Run in a 300000000-byte cgroup to validate the budget.')
GLib.timeout_add_seconds(1, exercise)
app.run([])
report = {'scenario':'GTK navigation + 50 synthetic tracks + real Spotifyd idle; no account or audio playback',
          'memory_max_bytes':cap, 'peak_rss_bytes':max((s['rss_bytes'] for s in samples), default=0),
          'peak_cgroup_bytes':max((s['cgroup_bytes'] for s in samples), default=0),
          'samples':samples, 'errors':errors,
          'engine_reaped':app.engine.process is not None and app.engine.process.poll() is not None}
output=ROOT/'verification'
output.mkdir(exist_ok=True)
(output/'memory-check.json').write_text(json.dumps(report,indent=2))
print(json.dumps({k:v for k,v in report.items() if k!='samples'},indent=2))
runtime.cleanup()
raise SystemExit(bool(errors) or not samples or not all(s['engine_running'] for s in samples))
