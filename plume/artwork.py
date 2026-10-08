"""Small, bounded artwork cache; no cookies or Spotify tokens sent to CDNs."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import urllib.parse
import urllib.request
import weakref

import gi
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk


HOSTS = {'i.scdn.co', 'mosaic.scdn.co', 'image-cdn-ak.spotifycdn.com', 'image-cdn-fa.spotifycdn.com'}


def allowed(url):
    parsed = urllib.parse.urlsplit(url)
    return parsed.scheme == 'https' and parsed.hostname in HOSTS and not parsed.username and not parsed.password


class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed(newurl):
            raise ValueError('Unexpected artwork host')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Artwork:
    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='covers')
        self.cache = OrderedDict()
        self.pending = {}
        self.closed = False

    def picture(self, images, size=160):
        picture = Gtk.Picture()
        picture.set_size_request(size, size)
        picture.set_can_shrink(True)
        picture.set_content_fit(Gtk.ContentFit.COVER)
        picture.add_css_class('artwork')
        options = [i for i in images or [] if allowed(i.get('url', ''))]
        if not options:
            return picture
        chosen = min(options, key=lambda i: abs((i.get('width') or 300) - 200))['url']
        if chosen in self.cache:
            self.cache.move_to_end(chosen)
            picture.set_paintable(self.cache[chosen])
        elif chosen in self.pending:
            self.pending[chosen].append(weakref.ref(picture))
        elif len(self.pending) < 24 and not self.closed:
            self.pending[chosen] = [weakref.ref(picture)]
            future = self.pool.submit(self.fetch, chosen)
            future.add_done_callback(lambda result: GLib.idle_add(self.deliver, chosen, result))
        return picture

    @staticmethod
    def fetch(url):
        with urllib.request.build_opener(Redirect()).open(url, timeout=10) as response:
            raw = response.read(1_500_001)
            if len(raw) > 1_500_000:
                raise ValueError('Artwork too large')
        stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(raw))
        return GdkPixbuf.Pixbuf.new_from_stream_at_scale(stream, 192, 192, True, None)

    def deliver(self, url, result):
        waiting = self.pending.pop(url, [])
        if self.closed:
            return False
        try:
            texture = Gdk.Texture.new_for_pixbuf(result.result())
        except Exception:
            return False
        self.cache[url] = texture
        while len(self.cache) > 24:
            self.cache.popitem(last=False)
        for ref in waiting:
            picture = ref()
            if picture is not None:
                picture.set_paintable(texture)
        return False

    def close(self):
        self.closed = True
        self.pending.clear()
        self.cache.clear()
        self.pool.shutdown(wait=False, cancel_futures=True)
