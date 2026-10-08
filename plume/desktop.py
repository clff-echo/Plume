"""Desktop portal launches the browser outside the audio/UI memory cgroup.

Observe the Request response rather than equating a spawned process with success.
The login callback stays alive even when the browser launch fails or is cancelled.
"""
import uuid

from gi.repository import Gio, GLib

from .engine import validate_browser_url, open_browser
from .platform_support import WINDOWS


class BrowserLauncher:
    def __init__(self):
        self.bus = None
        self.subscription = None
        self.path = None
        self.timer = None
        self.exported = None
        self.generation = 0

    def close(self):
        self.generation += 1
        if self.bus and self.subscription:
            self.bus.signal_unsubscribe(self.subscription)
        if self.bus and self.path:
            self.bus.call('org.freedesktop.portal.Desktop', self.path,
                'org.freedesktop.portal.Request', 'Close', None, None,
                Gio.DBusCallFlags.NONE, 2000, None, None)
        if self.timer:
            GLib.source_remove(self.timer)
        if self.exported:
            self.exported.unexport_handle()
        self.subscription = self.path = self.timer = self.exported = None

    def open(self, url, window, done, choose=False):
        validate_browser_url(url)
        self.close()
        generation = self.generation
        if WINDOWS:
            try:
                open_browser(url)
                done(None)
            except Exception:
                done("Impossible d’ouvrir le navigateur. Utilise Copier le lien.")
            return

        def finish(message):
            if generation != self.generation:
                return False
            self.close()
            done(message)
            return False

        def call_portal(parent):
            if generation != self.generation:
                return
            try:
                token = 'plume_' + uuid.uuid4().hex
                sender = self.bus.get_unique_name()[1:].replace('.', '_')
                self.path = '/org/freedesktop/portal/desktop/request/' + sender + '/' + token

                def response(_bus, _sender, _path, _iface, _signal, params, *_):
                    code, _ = params.unpack()
                    finish(None if code == 0 else 'L’ouverture a été annulée. Réessaie ou copie le lien dans ton navigateur.'
                           if code == 1 else 'Le navigateur n’a pas pu être lancé. Choisis un navigateur ou copie le lien.')

                self.subscription = self.bus.signal_subscribe('org.freedesktop.portal.Desktop',
                    'org.freedesktop.portal.Request', 'Response', self.path, None,
                    Gio.DBusSignalFlags.NONE, response, None)
                options = {'handle_token': GLib.Variant('s', token), 'ask': GLib.Variant('b', choose)}
                context = window.get_display().get_app_launch_context()
                context.set_timestamp(0)
                info = Gio.AppInfo.get_default_for_uri_scheme('https')
                activation = context.get_startup_notify_id(info, [Gio.File.new_for_uri(url)]) if info else None
                if activation:
                    options['activation_token'] = GLib.Variant('s', activation)

                def sent(bus, result, *_):
                    if generation != self.generation:
                        return
                    try:
                        bus.call_finish(result)
                    except GLib.Error:
                        finish('Linux n’a pas pu ouvrir le navigateur. Utilise « Copier le lien » pour continuer.')

                self.timer = GLib.timeout_add_seconds(45, lambda: finish(
                    'Toujours rien ? Choisis un navigateur ou copie le lien pour continuer.'))
                self.bus.call('org.freedesktop.portal.Desktop', '/org/freedesktop/portal/desktop',
                    'org.freedesktop.portal.OpenURI', 'OpenURI',
                    GLib.Variant('(ssa{sv})', (parent, url, options)),
                    GLib.VariantType.new('(o)'), Gio.DBusCallFlags.NONE, 10000, None, sent, None)
            except Exception:
                finish('L’ouverture automatique est indisponible. Copie le lien dans ton navigateur pour continuer.')

        def ready(_source, result, *_):
            if generation != self.generation:
                return
            try:
                self.bus = Gio.bus_get_finish(result)
                surface = window.get_surface()
                # Export the actual parent so desktop dialogs appear over Plume.
                if hasattr(surface, 'export_handle'):
                    def exported(surface, handle, *_):
                        if generation != self.generation:
                            surface.unexport_handle()
                            return
                        self.exported = surface
                        call_portal('wayland:' + handle)
                    if surface.export_handle(exported, None):
                        return
                call_portal('x11:' + format(surface.get_xid(), 'x') if hasattr(surface, 'get_xid') else '')
            except Exception:
                finish('Le service d’ouverture Linux est indisponible. Copie le lien dans ton navigateur.')

        Gio.bus_get(Gio.BusType.SESSION, None, ready, None)
