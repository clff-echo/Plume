#!/usr/bin/env python3
"""Measure real GTK cached navigation, with synthetic network/account data."""
import json,os,sys,tempfile,time,traceback
from pathlib import Path
from unittest.mock import Mock
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
runtime=tempfile.TemporaryDirectory(prefix='plume-speed-test-')
os.environ['PLUME_HOME']=runtime.name
os.environ['GSK_RENDERER']='cairo'
from plume.app import Plume,GLib
from plume.cache import LibraryCache
from plume.engine import memory_usage,memory_limit
app=Plume(demo=True)
app.engine.start=Mock()
app.poll=lambda:None
profile={'id':'fixture','display_name':'Compte de test'}
calls=[]
page={'items':[{'id':str(i),'name':'Playlist '+str(i),'uri':'spotify:playlist:'+str(i),'tracks':{'total':50}} for i in range(24)],'total':24}
report={'synthetic_network':True,'errors':[]}
phase=0
start=time.monotonic()

def request(path,**params):
    calls.append(path)
    if path!='/me/playlists':raise AssertionError('Unexpected API request '+path)
    time.sleep(.25)
    return page
app.api.request=request

def check():
    global phase
    try:
        if time.monotonic()-start>12:raise AssertionError('Cache test timed out')
        if phase==0:
            app.demo=False
            app.connected=True
            app.library_cache.activate(profile)
            app.navigate('library')
            app.navigate('library')
            phase=1
        elif phase==1 and not app.page_requests:
            assert len(calls)==1, calls
            timings=[]
            for _ in range(10):
                before=time.perf_counter()
                app.navigate('library')
                timings.append((time.perf_counter()-before)*1000)
            assert len(calls)==1
            report['warm_library_average_ms']=round(sum(timings)/len(timings),2)
            report['warm_library_max_ms']=round(max(timings),2)
            report['ten_reopens_network_requests']=0
            report['duplicate_loads_network_requests']=1
            app.library_cache=LibraryCache(app.settings)
            assert app.library_cache.remembered_profile()==profile
            app.oauth.token=Mock(return_value='synthetic')
            app.connected=False
            app.restore_session()
            phase=2
        elif phase==2 and app.connected:
            assert len(calls)==1, calls
            app.oauth.token.assert_called_once()
            report['warm_restart_profile_api_requests']=0
            report['warm_restart_library_api_requests']=0
            report['rss_bytes']=memory_usage()
            report['memory_limit_bytes']=memory_limit()
            app.quit()
            return False
    except Exception:
        report['errors'].append(traceback.format_exc())
        app.quit()
        return False
    return True

GLib.timeout_add(100,check)
app.run([])
(ROOT/'verification'/'speed-check.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
runtime.cleanup()
raise SystemExit(bool(report['errors']))
