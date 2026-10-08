#!/usr/bin/env python3
"""Real bundled helper + GTK, cancelled before any account authorization."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import urllib.parse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
os.environ['GSK_RENDERER']='cairo'
runtime=tempfile.TemporaryDirectory(prefix='plume-helper-test-')
os.environ['PLUME_HOME']=runtime.name
from plume.app import Plume,GLib
from plume.engine import memory_usage,memory_limit
app=Plume()
observed=[]
samples=[]
started=time.monotonic()

def opened(url, window, done, choose=False):
    parts=urllib.parse.urlsplit(url)
    observed.append(parts.scheme=='https' and parts.hostname=='accounts.spotify.com' and parts.path=='/authorize')
    app.cancel_login()

app.browser.open=opened

def sample():
    samples.append(memory_usage())
    if observed and not app.logging_in:
        app.quit()
        return False
    if time.monotonic()-started>35:
        app.quit()
        return False
    return True

GLib.timeout_add(500,lambda:(app.login(),False)[1])
GLib.timeout_add(100,sample)
app.run([])
report={'spotify_authorization_generated':bool(observed) and all(observed),
        'real_account_used':False,'cancelled_and_reaped':app.engine.auth_process is None,
        'memory_max_bytes':memory_limit(),'peak_rss_bytes':max(samples,default=0)}
(ROOT/'verification'/'helper-check.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
runtime.cleanup()
raise SystemExit(not report['spotify_authorization_generated'] or not report['cancelled_and_reaped'])
