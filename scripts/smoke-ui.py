#!/usr/bin/env python3
"""Render the real GTK interface with isolated, clearly marked sample data."""
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
runtime = tempfile.TemporaryDirectory(prefix='plume-ui-')
os.environ['PLUME_HOME'] = runtime.name
from plume.app import Plume, GLib, Gtk
from plume.engine import memory_usage

app = Plume(demo=True)
output = ROOT/'verification'
output.mkdir(exist_ok=True)
results = {'screens': [], 'rss_bytes': [], 'errors': []}


def snapshot(name):
    # Render only our own widget tree, independent of the desktop compositor.
    paintable = Gtk.WidgetPaintable.new(app.window)
    node = None
    for _ in range(12):
        context = GLib.MainContext.default()
        while context.pending():
            context.iteration(False)
        shot = Gtk.Snapshot.new()
        paintable.snapshot(shot, app.window.get_width(), app.window.get_height())
        node = shot.to_node()
        if node is not None:
            break
        time.sleep(0.05)
    if node is None:
        shot = Gtk.Snapshot.new()
        app.window.snapshot_child(app.window.get_child(), shot)
        node = shot.to_node()
    if node is None:
        raise RuntimeError('No GTK frame available for screenshot')
    texture = app.window.get_renderer().render_texture(node, None)
    texture.save_to_png(str(output/(name+'.png')))
    results['screens'].append(name)
    results['rss_bytes'].append(memory_usage())


def step1():
    try:
        snapshot('bibliotheque')
        app.show_welcome()
        app.login_btn.set_sensitive(True)
        GLib.timeout_add(500, step2)
    except Exception:
        fail()
    return False


def step2():
    try:
        snapshot('connexion')
        app.logging_in = True
        app.login_url = 'https://accounts.spotify.com/authorize?sample=not-a-real-session'
        app.refresh_login()
        GLib.timeout_add(500, step_login)
    except Exception:
        fail()
    return False


def step_login():
    try:
        snapshot('connexion-en-cours')
        app.logging_in = False
        app.login_url = None
        app.connected = True
        app.update_state({'item': {'id':'test','name':'Morceau de test','artists':[{'name':'Artiste de test'}], 'duration_ms':200000},
                          'is_playing':True,'progress_ms':15000,'device':{'volume_percent':50, 'supports_volume':True}})
        app.settings.data['lyrics'] = True
        app.lyric_data = {'lines':[(0,'Exemple de paroles de test'),(15000,'La deuxième ligne'),(30000,'La troisième ligne')], 'plain':'', 'instrumental':False}
        app.last_lyric_key = 'test'
        app.navigate('lyrics')
        GLib.timeout_add(500, step3)
    except Exception:
        fail()
    return False


def step3():
    try:
        snapshot('paroles')
        app.api.request = lambda path, *args, **kwargs: {
            '/me/player/devices': {'devices': [
                {'id':'local', 'name':'Plume · Linux', 'type':'Computer', 'is_active':True},
                {'id':'phone', 'name':'Téléphone de test', 'type':'Smartphone', 'is_active':False}]},
            '/search': {'tracks': {'items':[{'id':'test','uri':'spotify:track:test','name':'Résultat de test',
                                           'artists':[{'name':'Artiste de test'}],'duration_ms':180000}]}}
        }.get(path)
        app.navigate('devices')
        GLib.timeout_add(500, step4)
    except Exception:
        fail()
    return False


def step4():
    try:
        snapshot('appareils')
        app.navigate('search')
        app.search('test')
        GLib.timeout_add(500, step5)
    except Exception:
        fail()
    return False


def step5():
    try:
        snapshot('recherche')
        app.open_playlist({'id':'0','name':'Les heures bleues','tracks':{'total':5}})
        GLib.timeout_add(500, step6)
    except Exception:
        fail()
    return False


def step6():
    try:
        snapshot('playlist')
        app.connected = False
        app.quit()
    except Exception:
        fail()
    return False


def fail():
    results['errors'].append(traceback.format_exc())
    app.quit()


GLib.timeout_add(1200, step1)
app.run([])
(output/'ui-check.json').write_text(json.dumps(results, indent=2))
print(json.dumps(results, indent=2))
runtime.cleanup()
raise SystemExit(bool(results['errors']))
