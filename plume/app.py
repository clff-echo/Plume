from __future__ import annotations

import argparse
import bisect
import json
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import threading
import time

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Gdk, GLib, Gio, Pango, GdkPixbuf

from .core import Settings, OAuth, SpotifyAPI, Lyrics, UserError, AuthRequired, RateLimited, REDIRECT, track_from_row, format_time
from .engine import Engine, validate_browser_url, memory_usage, memory_limit
from .desktop import BrowserLauncher
from .artwork import Artwork
from .cache import LibraryCache

from .platform_support import ROOT, WINDOWS


def box(vertical=True, spacing=12, css=None):
    widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL, spacing=spacing)
    if css:
        for name in css.split():
            widget.add_css_class(name)
    return widget


def label(text="", css=None, wrap=False):
    widget = Gtk.Label(label=text, xalign=0)
    widget.set_wrap(wrap)
    if css:
        for name in css.split():
            widget.add_css_class(name)
    return widget


def button(text, action, css=None, icon=None):
    widget = Gtk.Button(label=text)
    if icon:
        widget.set_child(Gtk.Image.new_from_icon_name(icon))
        widget.set_tooltip_text(text)
        widget.update_property([Gtk.AccessibleProperty.LABEL], [text])
    if css:
        for name in css.split():
            widget.add_css_class(name)
    widget.connect("clicked", lambda _: action())
    return widget


def clear(widget):
    while child := widget.get_first_child():
        widget.remove(child)


class Plume(Gtk.Application):
    def __init__(self, demo=False):
        super().__init__(application_id="io.plume.Client", flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.demo = demo
        self.settings = Settings()
        self.library_cache = LibraryCache(self.settings)
        self.page_requests = {}
        self.page_retry_sources = set()
        self.oauth = OAuth(self.settings)
        self.api = SpotifyAPI(self.oauth)
        self.lyrics = Lyrics()
        self.pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="plume")
        self.slots = threading.BoundedSemaphore(8)
        self.closed = False
        self.connected = False
        self.logging_in = False
        self.audio_login = False
        self.login_url = None
        self.login_serial = 0
        self.restoring = False
        self.session_pending = False
        self.verifying = False
        self.session_retry_source = None
        self.session_retry_at = 0
        self.session_attempt = 0
        self.session_wait_reason = ""
        self.session_watchdog = None
        self.session_deadline = 0
        self.session_error = False
        self.browser = BrowserLauncher()
        self.artwork = Artwork()
        self.library_items = []
        self.engine = Engine(self.settings, lambda msg: GLib.idle_add(self.status, msg))
        self.page = "library"
        self.generation = 0
        self.state = {}
        self.state_time = time.monotonic()
        self.polling = False
        self.last_poll = 0
        self.track_key = None
        self.lyric_data = None
        self.lyric_index = -2
        self.lyric_pending = None
        self.last_lyric_key = None
        self.controls = []
        self.connect("activate", self.activate)
        self.connect("shutdown", self.shutdown)

    def run_task(self, work, done=lambda _: None, quiet=False):
        if self.closed:
            return
        if not self.slots.acquire(blocking=False):
            if not quiet:
                self.status("Une opération est déjà en cours. Réessaie dans un instant.")
            return False
        future = self.pool.submit(work)

        def finished(future):
            self.slots.release()
            def deliver():
                if self.closed:
                    return False
                try:
                    result = future.result()
                    done(result)
                except Exception as exc:
                    if not quiet:
                        message = str(exc) if isinstance(exc, UserError) else "Cette opération a échoué. Vérifie ta connexion et réessaie."
                        self.status(message)
                return False
            GLib.idle_add(deliver)
        future.add_done_callback(finished)
        return True

    def activate(self, *_):
        if getattr(self, "window", None):
            self.window.present()
            return
        provider = Gtk.CssProvider()
        provider.load_from_path(str(ROOT/"assets"/"style.css"))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", True)
        self.window = Gtk.ApplicationWindow(application=self, title="Plume — ta musique, simplement")
        self.window.set_default_size(1200, 800)
        self.window.set_size_request(880, 620)
        header = Gtk.HeaderBar()
        header.add_css_class("titlebar")
        header.set_title_widget(label("PLUME", "wordmark"))
        self.window.set_titlebar(header)

        self.stack = Gtk.Stack()
        self.stack.set_hhomogeneous(False)
        self.stack.set_vhomogeneous(False)
        root = box(spacing=0, css="app-shell")
        layout = box(False, 8)
        layout.set_vexpand(True)
        sidebar = box(spacing=12, css="sidebar")
        sidebar.set_size_request(250, -1)
        library_head = box(False, 10)
        library_head.append(Gtk.Image.new_from_icon_name("view-list-symbolic"))
        library_head.append(label("Ta bibliothèque", "section-title"))
        sidebar.append(library_head)
        self.nav = {}
        filters = box(False, 8)
        for key, text in [("library", "Playlists"), ("liked", "Titres aimés")]:
            nav = button(text, lambda k=key: self.navigate(k), "chip")
            self.nav[key] = nav
            filters.append(nav)
        sidebar.append(filters)
        library_scroll = Gtk.ScrolledWindow()
        library_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        library_scroll.set_vexpand(True)
        self.sidebar_library = box(spacing=2)
        library_scroll.set_child(self.sidebar_library)
        sidebar.append(library_scroll)
        sidebar.append(button("⚙   Réglages", lambda: self.navigate("settings"), "nav"))
        self.ram = label("Mémoire · mesure…", "tiny")
        sidebar.append(self.ram)
        self.cap_label = label("", "tiny", True)
        sidebar.append(self.cap_label)
        layout.append(sidebar)
        main = box(spacing=10, css="main")
        main.set_hexpand(True)
        top = box(False, 14, "topbar")
        logo = Gtk.Image.new_from_file(str(ROOT/"assets"/"plume.svg"))
        logo.set_pixel_size(30)
        top.append(logo)
        brand = label("plume", "brand")
        brand.set_size_request(173, -1)
        top.append(brand)
        home = button("Accueil", lambda: self.navigate("library"), "home-button", "go-home-symbolic")
        top.append(home)
        self.global_search = Gtk.SearchEntry(placeholder_text="Que veux-tu écouter ?")
        self.global_search.set_hexpand(True)
        self.global_search.add_css_class("global-search")
        self.global_search.connect("activate", lambda _: self.global_find())
        top.append(self.global_search)
        self.profile = label("Mon compte", "profile-name")
        self.profile.set_ellipsize(Pango.EllipsizeMode.END)
        self.profile.set_max_width_chars(16)
        account = button("Mon compte", lambda: self.navigate("settings"), "account")
        account.set_child(self.profile)
        top.append(account)
        root.append(top)
        self.heading = label("Ta bibliothèque", "heading")
        main.append(self.heading)
        self.notice = label("", "notice", True)
        self.notice.set_visible(False)
        main.append(self.notice)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        self.content = box(spacing=12)
        scroll.set_child(self.content)
        main.append(scroll)
        layout.append(main)
        root.append(layout)
        root.append(self.build_player())
        self.stack.add_named(root, "music")
        welcome_scroll = Gtk.ScrolledWindow()
        welcome_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        welcome_scroll.set_child(self.build_welcome())
        self.stack.add_named(welcome_scroll, "welcome")
        self.window.set_child(self.stack)
        self.window.present()
        if self.demo:
            self.stack.set_visible_child_name("music")
            self.navigate("library")
        else:
            self.show_welcome()
            if self.settings.data["client_id"]:
                self.restore_session()
        GLib.timeout_add_seconds(1, self.tick)
        self.tick()

    def build_welcome(self):
        page = box(spacing=18, css="welcome")
        page.set_valign(Gtk.Align.FILL)
        brand = box(False, 10)
        logo = Gtk.Image.new_from_file(str(ROOT/"assets"/"plume.svg"))
        logo.set_pixel_size(34)
        brand.append(logo)
        brand.append(label("plume", "brand"))
        page.append(brand)
        center = box(spacing=20, css="login-card")
        center.set_valign(Gtk.Align.CENTER)
        center.set_halign(Gtk.Align.CENTER)
        center.set_vexpand(True)
        center.set_size_request(480, -1)
        logo = Gtk.Image.new_from_file(str(ROOT/"assets"/"plume.svg"))
        logo.set_pixel_size(76)
        logo.set_halign(Gtk.Align.START)
        self.welcome_logo = logo
        center.append(logo)
        center.append(label("TA MUSIQUE, SIMPLEMENT", "eyebrow accent"))
        self.welcome_title = label("Tout commence\npar ta musique.", "login-title")
        center.append(self.welcome_title)
        center.append(label("Tes playlists, tes titres aimés, tes appareils.\nConnecte ton compte Spotify pour les retrouver.", "subtitle", True))
        self.login_btn = button("Se connecter à Spotify", self.login, "primary login-button")
        center.append(self.login_btn)
        self.login_status = label("Ton mot de passe reste dans ton navigateur.", "muted", True)
        self.login_status.set_max_width_chars(58)
        center.append(self.login_status)
        self.login_fallback = box(spacing=10)
        self.login_fallback.append(label("Le navigateur ne s’affiche pas ?", "muted"))
        actions = box(False, 8)
        actions.append(button("Ouvrir le navigateur", self.reopen_login, "secondary"))
        actions.append(button("Copier le lien", self.copy_login, "secondary"))
        self.login_fallback.append(actions)
        chooser = button("Choisir un autre navigateur", lambda: self.reopen_login(True), "link-action")
        chooser.set_halign(Gtk.Align.START)
        self.login_fallback.append(chooser)
        center.append(self.login_fallback)
        self.cancel_login_btn = button("Annuler la connexion", self.cancel_login, "link-action")
        self.cancel_login_btn.set_halign(Gtk.Align.START)
        center.append(self.cancel_login_btn)
        page.append(center)
        foot = label("Plume est un client indépendant, non affilié à Spotify.  ·  Spotify Premium requis", "tiny", True)
        foot.set_halign(Gtk.Align.CENTER)
        page.append(foot)
        return page

    def show_welcome(self):
        self.stack.set_visible_child_name("welcome")
        self.refresh_login()

    def refresh_login(self):
        busy = self.logging_in or self.restoring
        self.welcome_logo.set_visible(not busy)
        self.welcome_title.set_text("Valide dans Spotify." if self.logging_in else "Connexion reçue." if self.restoring and self.oauth.access else "On retrouve ta musique." if self.restoring else "Chargement indisponible" if self.session_error and self.session_pending else "Tout commence\npar ta musique.")
        (self.welcome_title.add_css_class if busy else self.welcome_title.remove_css_class)("login-title-busy")
        self.login_btn.set_label("Connexion en cours…" if self.logging_in else "Chargement du compte…" if self.restoring else "Réessayer le chargement" if self.session_pending else "Se connecter à Spotify")
        self.login_btn.set_sensitive(not busy and not self.verifying and not self.demo)
        self.login_fallback.set_visible(bool(self.login_url) and self.logging_in)
        self.cancel_login_btn.set_visible(self.logging_in or self.restoring)
        self.cancel_login_btn.set_label("Mettre en pause" if self.restoring else "Annuler la connexion")

    def end_session_wait(self, message, invalidate=False):
        if invalidate:
            self.login_serial += 1
        for attr in ("session_retry_source", "session_watchdog"):
            source = getattr(self, attr)
            if source:
                GLib.source_remove(source)
                setattr(self, attr, None)
        self.restoring = self.verifying = False
        self.session_retry_at = 0
        self.session_error = True
        self.refresh_login()
        self.status(message)
        return False

    def restore_session(self):
        if self.closed or self.verifying:
            return
        if not self.restoring:
            self.session_attempt = 0
            self.session_deadline = time.monotonic() + 30
            self.session_error = False
            def expired():
                self.session_watchdog = None
                return self.end_session_wait("Le chargement du compte n’a pas abouti en 30 secondes. Ta session est conservée. Réessaie plus tard.", True)
            self.session_watchdog = GLib.timeout_add_seconds(30, expired)
        self.restoring = True
        self.session_pending = True
        self.verifying = True
        self.session_retry_at = 0
        self.refresh_login()
        serial = self.login_serial
        self.status("Connexion reçue. Chargement de ton compte…" if self.oauth.access else "On retrouve ton compte…")
        def work():
            try:
                profile = self.library_cache.remembered_profile()
                if profile:
                    # Validate the saved grant, without spending shared /me quota
                    # again for the same account and exact authorization.
                    self.oauth.token()
                else:
                    profile = self.api.request("/me")
                credentials_path = self.engine.cache / "credentials.json"
                credentials = json.loads(credentials_path.read_text()) if credentials_path.exists() else {}
                if credentials.get("username") and profile.get("id") and credentials["username"] != profile["id"]:
                    self.engine.logout()
                    self.oauth.logout()
                    raise AuthRequired("Les deux validations utilisent des comptes différents. Reconnecte-toi avec le même compte Spotify.")
                return profile, None
            except Exception as exc:
                return None, exc
        def done(result):
            if serial != self.login_serial:
                return
            self.verifying = False
            profile, error = result
            if isinstance(error, AuthRequired):
                self.session_pending = False
                self.end_session_wait(str(error))
            elif error or not profile:
                self.session_attempt += 1
                # Short server-requested waits should not become a two-minute
                # artificial delay. Slow down only after repeated failures.
                minimum = min(30, 2 ** min(max(0, self.session_attempt - 3), 5))
                delay = max(minimum, int(error.retry_at - time.monotonic()) + 1) if isinstance(error, RateLimited) else min(30, 2 ** min(self.session_attempt, 5))
                if self.session_attempt >= 3 or time.monotonic() + delay >= self.session_deadline:
                    detail = f"Spotify limite l’accès au compte et demande encore {delay} s d’attente." if isinstance(error, RateLimited) else str(error) if isinstance(error, UserError) else "Spotify ne renvoie pas le compte."
                    self.end_session_wait(detail + " Ta session est conservée. Tu peux réessayer plus tard.")
                    return
                self.session_retry_at = time.monotonic() + delay
                self.session_wait_reason = "Spotify limite temporairement le chargement du compte." if isinstance(error, RateLimited) else str(error) if isinstance(error, UserError) else "Le compte est momentanément indisponible."
                self.refresh_login()
                self.status(f"Spotify fait patienter le chargement du compte. Ta connexion est conservée. Nouvel essai automatique dans {delay} s.")
                def retry():
                    self.session_retry_source = None
                    if not self.closed and serial == self.login_serial and self.restoring:
                        self.restore_session()
                    return False
                self.session_retry_source = GLib.timeout_add_seconds(delay, retry)
            else:
                if self.session_watchdog:
                    GLib.source_remove(self.session_watchdog)
                    self.session_watchdog = None
                self.restoring = self.session_pending = False
                self.session_error = False
                self.session_attempt = 0
                self.refresh_login()
                self.on_connected(profile)
        if not self.run_task(work, done):
            self.end_session_wait("Plume est occupé. Réessaie dans un instant.")

    def global_find(self):
        query = self.global_search.get_text().strip()
        self.navigate("search")
        if query and (self.connected or self.demo):
            self.search(query)

    def build_player(self):
        player = box(False, 20, "player")
        left = box(False, 12)
        left.set_size_request(220, -1)
        self.player_cover = box(spacing=0)
        self.player_cover.append(label("♫", "cover"))
        left.append(self.player_cover)
        details = box(spacing=5)
        details.set_valign(Gtk.Align.CENTER)
        self.song = label("À toi de choisir", "song")
        self.artist = label("Ta prochaine écoute t’attend", "muted")
        for text in (self.song, self.artist):
            text.set_ellipsize(Pango.EllipsizeMode.END)
            text.set_max_width_chars(22)
            details.append(text)
        left.append(details)
        player.append(left)
        center = box(spacing=4)
        center.set_hexpand(True)
        row = box(False, 10)
        row.set_halign(Gtk.Align.CENTER)
        self.shuffle_btn = button("Lecture aléatoire", self.shuffle, "transport", "media-playlist-shuffle-symbolic")
        self.prev_btn = button("Précédent", lambda: self.control("/me/player/previous", "POST"), "transport", "media-skip-backward-symbolic")
        self.play_btn = button("Lecture ou pause", self.toggle, "play", "media-playback-start-symbolic")
        self.next_btn = button("Suivant", lambda: self.control("/me/player/next", "POST"), "transport", "media-skip-forward-symbolic")
        self.repeat_btn = button("Répétition", self.repeat, "transport", "media-playlist-repeat-symbolic")
        self.controls = [self.shuffle_btn, self.prev_btn, self.play_btn, self.next_btn, self.repeat_btn]
        for control in self.controls:
            control.set_sensitive(False)
            row.append(control)
        center.append(row)
        timeline = box(False, 8)
        self.elapsed = label("0:00", "tiny")
        self.duration = label("0:00", "tiny")
        self.progress = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 1)
        self.progress.set_draw_value(False)
        self.progress.set_hexpand(True)
        self.progress.set_sensitive(False)
        self.progress.connect("change-value", self.seek)
        timeline.append(self.elapsed)
        timeline.append(self.progress)
        timeline.append(self.duration)
        center.append(timeline)
        player.append(center)
        right = box(False, 4)
        right.set_valign(Gtk.Align.CENTER)
        right.append(button("Paroles", lambda: self.navigate("lyrics"), "transport", "audio-input-microphone-symbolic"))
        right.append(button("Appareil de lecture", lambda: self.navigate("devices"), "transport", "audio-speakers-symbolic"))
        self.volume = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.volume.set_size_request(80, -1)
        self.volume.set_draw_value(False)
        self.volume.set_value(50)
        self.volume.set_tooltip_text("Volume")
        self.volume.set_sensitive(False)
        self.volume.connect("change-value", self.change_volume)
        right.append(self.volume)
        player.append(right)
        return player

    def status(self, text):
        if not self.closed:
            self.login_status.set_text(text or "Ton mot de passe reste dans ton navigateur.")
            self.notice.set_text(text)
            self.notice.set_visible(bool(text))
        return False

    def navigate(self, page):
        if not self.connected and not self.demo:
            self.show_welcome()
            return
        self.stack.set_visible_child_name("music")
        self.heading.set_visible(True)
        self.page = page
        self.generation += 1
        clear(self.content)
        for key, nav in self.nav.items():
            (nav.add_css_class if key == page else nav.remove_css_class)("selected")
        self.heading.set_text({"library": "Accueil", "liked": "Titres aimés", "search": "Trouve ton prochain titre",
            "lyrics": "Les mots, avec la musique", "devices": "Ta musique, partout", "settings": "Fais comme chez toi"}[page])
        if page == "settings":
            self.show_settings()
        elif page == "lyrics":
            self.show_lyrics()
        elif not self.connected and not self.demo:
            self.empty("Tout commence par une connexion.", "Retrouve tes playlists, tes favoris et tes appareils.",
                       "Se connecter à Spotify", self.login)
        elif page == "library":
            self.library()
        elif page == "liked":
            self.tracks("/me/tracks", "Tes titres aimés")
        elif page == "search":
            self.search_page()
        elif page == "devices":
            self.devices()

    def empty(self, title, detail, action=None, callback=None):
        panel = box(spacing=20, css="hero")
        panel.append(label("♫", "hero-icon"))
        panel.append(label(title, "hero-title", True))
        panel.append(label(detail, "subtitle", True))
        if action:
            b = button(action, callback, "primary")
            b.set_halign(Gtk.Align.START)
            panel.append(b)
        self.content.append(panel)

    def load_page(self, work, done):
        generation = self.generation
        def apply(result):
            if generation == self.generation:
                done(result)
        self.run_task(work, apply)

    def cached_page(self, path, params, render, ttl=300, force=False, attempt=0):
        generation, serial = self.generation, self.login_serial
        cached = self.library_cache.get(path, params)
        if cached:
            render(cached[0])
            if cached[1] < ttl and not force:
                return
            self.status("Sélection mémorisée affichée · actualisation en cours…")
        key = (serial, self.library_cache.scope, path, tuple(sorted(params.items())))
        def deliver(result):
            if self.closed or serial != self.login_serial or generation != self.generation:
                return
            data, error = result
            if not error:
                render(data)
                self.status("")
            elif isinstance(error, RateLimited) and attempt < 3:
                delay = max(1, int(error.retry_at-time.monotonic())+1)
                self.status(f"Spotify demande {delay} s avant l’actualisation. " + ("Ta sélection reste affichée." if cached else "Le chargement reprendra automatiquement."))
                holder = []
                def retry():
                    self.page_retry_sources.discard(holder[0])
                    if serial == self.login_serial and generation == self.generation and not self.closed:
                        self.cached_page(path, params, render, ttl, True, attempt+1)
                    return False
                holder.append(GLib.timeout_add_seconds(delay, retry))
                self.page_retry_sources.add(holder[0])
            else:
                self.status(str(error) if isinstance(error, UserError) else "Chargement indisponible pour le moment.")
        if key in self.page_requests:
            self.page_requests[key].append(deliver)
            return
        self.page_requests[key] = [deliver]
        def work():
            try:
                return self.api.request(path, **params), None
            except Exception as exc:
                return None, exc
        def completed(result):
            callbacks = self.page_requests.pop(key, [])
            data, error = result
            if serial != self.login_serial:
                return
            if not error:
                self.library_cache.put(path, params, data)
            for callback in callbacks:
                callback(result)
        if not self.run_task(work, completed):
            self.page_requests.pop(key, None)

    def library(self, offset=0, force=False):
        self.generation += 1
        clear(self.content)
        self.content.append(label("Tes sélections. Ton rythme.", "subtitle"))
        if self.demo:
            self.status("Aperçu de l’interface · exemples fictifs · aucune lecture Spotify")
            self.render_library({"items": [{"name": n, "description": "Playlist de démonstration", "id": str(i)} for i, n in enumerate(["Les heures bleues", "En mouvement", "Concentration", "Au ralenti", "À redécouvrir", "Le dernier métro"])], "total": 6}, 0)
            return
        self.content.append(label("Chargement des playlists…", "muted"))
        self.cached_page("/me/playlists", {"limit":24, "offset":offset}, lambda data: self.render_library(data, offset), ttl=300, force=force)

    def render_library(self, data, offset):
        clear(self.content)
        chips = box(False, 8)
        chips.append(button("Playlists", lambda: self.navigate("library"), "chip selected"))
        chips.append(button("Titres aimés", lambda: self.navigate("liked"), "chip"))
        chips.append(button("Actualiser", lambda: self.library(offset, True), "chip"))
        self.content.append(chips)
        self.library_items = [p for p in data.get("items", []) if p]
        clear(self.sidebar_library)
        liked = box(False, 12)
        liked.append(label("♥", "liked-cover"))
        liked_text = box(spacing=4)
        liked_text.append(label("Titres aimés", "song"))
        liked_text.append(label("Playlist · tes favoris", "muted"))
        liked.append(liked_text)
        shortcut = button("Titres aimés", lambda: self.navigate("liked"), "library-shortcut")
        shortcut.set_child(liked)
        self.sidebar_library.append(shortcut)
        for index, playlist in enumerate(self.library_items[:24]):
            item = box(False, 12)
            item.append(self.playlist_art(playlist, 44, index))
            texts = box(spacing=5)
            title = label(playlist.get("name", "Playlist"), "library-name")
            title.set_ellipsize(Pango.EllipsizeMode.END)
            title.set_max_width_chars(20)
            texts.append(title)
            texts.append(label("Playlist", "muted"))
            item.append(texts)
            shortcut = button("", lambda p=playlist: self.open_playlist(p), "library-shortcut")
            shortcut.set_child(item)
            self.sidebar_library.append(shortcut)
        if offset == 0 and self.library_items:
            quick = Gtk.FlowBox()
            quick.set_selection_mode(Gtk.SelectionMode.NONE)
            quick.set_min_children_per_line(2)
            quick.set_max_children_per_line(2)
            quick.set_column_spacing(8)
            quick.set_row_spacing(8)
            for index, playlist in enumerate(self.library_items[:4]):
                row = box(False, 12)
                row.append(self.playlist_art(playlist, 48, index))
                title = label(playlist.get("name", "Playlist"), "song")
                title.set_ellipsize(Pango.EllipsizeMode.END)
                title.set_max_width_chars(26)
                title.set_hexpand(True)
                row.append(title)
                row.append(label("▶", "quick-play"))
                action = button("", lambda p=playlist: self.open_playlist(p), "quick-tile")
                action.set_child(row)
                quick.insert(action, -1)
            self.content.append(quick)
        self.content.append(label("Tes playlists", "section-title"))
        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_max_children_per_line(4)
        flow.set_min_children_per_line(2)
        flow.set_column_spacing(12)
        flow.set_row_spacing(12)
        for index, playlist in enumerate(data.get("items", [])):
            if not playlist:
                continue
            tile = box(spacing=10, css="tile")
            art = self.playlist_art(playlist, 144, index)
            art.set_halign(Gtk.Align.CENTER)
            tile.append(art)
            name = label(playlist.get("name", "Playlist"), "tile-name", True)
            name.set_lines(2)
            name.set_ellipsize(Pango.EllipsizeMode.END)
            tile.append(name)
            total = (playlist.get("items") or playlist.get("tracks") or {}).get("total")
            tile.append(label(f"{total} titres" if total is not None else "Playlist Spotify", "muted"))
            b = Gtk.Button(child=tile)
            b.add_css_class("tile-button")
            b.connect("clicked", lambda _, p=playlist: self.open_playlist(p))
            flow.insert(b, -1)
        self.content.append(flow)
        if not data.get("items"):
            self.empty("Un peu de place pour tes découvertes.", "Tes playlists apparaîtront ici après leur ajout dans Spotify.")
        self.pagination(data, offset, 24, self.library)

    def playlist_art(self, playlist, size=144, index=0):
        if self.demo:
            art = Gtk.Picture.new_for_pixbuf(GdkPixbuf.Pixbuf.new_from_file_at_scale(
                str(ROOT/"assets"/f"demo-{index % 6}.svg"), size, size, True))
            art.set_can_shrink(True)
            art.set_content_fit(Gtk.ContentFit.COVER)
            art.add_css_class("artwork")
        elif playlist.get("images"):
            art = self.artwork.picture(playlist["images"], size)
        else:
            art = label("♫", "art-placeholder")
        art.set_size_request(size, size)
        return art

    def open_playlist(self, playlist):
        self.current_playlist = playlist
        self.navigate_container_for_playlist()
        self.tracks(f"/playlists/{playlist['id']}/items", playlist.get("name", "Playlist"),
                    context=playlist.get("uri") or "spotify:playlist:"+playlist["id"])

    def playlist_header(self, title, context):
        hero = box(False, 24, "playlist-header")
        playlist = getattr(self, "current_playlist", {}) if context else {}
        if context:
            hero.append(self.playlist_art(playlist, 156))
        else:
            hero.append(label("♥", "liked-hero"))
        details = box(spacing=10)
        details.set_hexpand(True)
        details.set_valign(Gtk.Align.END)
        details.append(label("Playlist", "muted"))
        name = label(title, "playlist-title", True)
        name.set_lines(2)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        details.append(name)
        total = (playlist.get("tracks") or playlist.get("items") or {}).get("total")
        details.append(label(self.profile.get_text() + (f"  •  {total} titres" if total is not None else ""), "muted"))
        hero.append(details)
        return hero

    def navigate_container_for_playlist(self):
        self.page = "playlist"
        self.generation += 1
        self.stack.set_visible_child_name("music")
        for nav in self.nav.values():
            nav.remove_css_class("selected")

    def pagination(self, data, offset, limit, callback):
        nav = box(False)
        previous = button("← Précédent", lambda: callback(max(0, offset-limit)), "secondary")
        previous.set_sensitive(offset > 0)
        nav.append(previous)
        total = data.get("total", 0)
        nav.append(label(f"{offset+1 if total else 0}–{min(offset+limit, total)} sur {total}", "muted"))
        more = button("Suivant →", lambda: callback(offset+limit), "secondary")
        more.set_sensitive(bool(data.get("next")) or offset+limit < total)
        nav.append(more)
        self.content.append(nav)

    def tracks(self, path, title, offset=0, context=None, force=False):
        self.generation += 1
        clear(self.content)
        self.heading.set_visible(False)
        self.heading.set_text("Playlist" if context else "Bibliothèque")
        self.content.append(self.playlist_header(title, context))
        if context:
            self.content.append(button("Lire cette playlist", lambda: self.play({"context_uri": context}), "primary"))
        rows = box(spacing=3)
        self.content.append(rows)
        def show(data):
            clear(self.content)
            self.content.append(self.playlist_header(title, context))
            actions = box(False, 8)
            if context:
                actions.append(button("Lire cette playlist", lambda: self.play({"context_uri": context}), "primary"))
            actions.append(button("Actualiser", lambda: self.tracks(path, title, offset, context, True), "secondary"))
            self.content.append(actions)
            columns = box(False, 14, "track-columns")
            columns.append(label("#", "track-index"))
            name = label("Titre", "muted")
            name.set_hexpand(True)
            columns.append(name)
            album = label("Album", "muted")
            album.set_size_request(150, -1)
            columns.append(album)
            columns.append(label("Durée", "muted"))
            self.content.append(columns)
            rows = box(spacing=3)
            self.content.append(rows)
            self.render_tracks(data, rows, offset, context, lambda pos: self.tracks(path, title, pos, context))
        if self.demo:
            show({"items":[{"track":{"id":str(i),"name":n,"artists":[{"name":"Artiste de démonstration"}],
                "album":{"name":"Album de démonstration"},"duration_ms":180000+i*12000}}
                for i,n in enumerate(["Échappée", "Lumière douce", "Les heures bleues", "À l’horizon", "Dernière nuit"])],"total":5})
            return
        self.cached_page(path, {"limit":50, "offset":offset}, show, ttl=900 if path == "/me/tracks" else 300, force=force)

    def render_tracks(self, data, parent, offset=0, context=None, paginate=None, direct=False):
        items = data.get("items", [])
        tracks = items if direct else [track_from_row(item) for item in items]
        for index, track in enumerate(tracks):
            if not track:
                continue
            row = box(False, 14, "track-row")
            row.append(label(f"{offset+index+1:02}", "track-index"))
            texts = box(spacing=3)
            texts.set_hexpand(True)
            name = label(track.get("name", "Titre indisponible"), "song")
            name.set_ellipsize(Pango.EllipsizeMode.END)
            texts.append(name)
            artists = label(", ".join(a["name"] for a in track.get("artists", [])), "muted")
            artists.set_ellipsize(Pango.EllipsizeMode.END)
            texts.append(artists)
            row.append(texts)
            album = label((track.get("album") or {}).get("name", ""), "muted")
            album.set_size_request(150, -1)
            album.set_max_width_chars(20)
            album.set_ellipsize(Pango.EllipsizeMode.END)
            row.append(album)
            row.append(label(format_time(track.get("duration_ms")), "tiny"))
            payload = {"context_uri": context, "offset": {"uri": track.get("uri")}} if context else {
                "uris": [t["uri"] for t in tracks[index:] if t.get("uri") and not t.get("is_local") and t.get("is_playable") is not False]}
            b = button("Lire " + track.get("name", "ce titre"), lambda p=payload: self.play(p), "transport", "media-playback-start-symbolic")
            b.set_sensitive(bool(track.get("uri")) and not track.get("is_local") and track.get("is_playable") is not False)
            row.append(b)
            parent.append(row)
        if not tracks:
            parent.append(label("Aucun titre disponible dans cette sélection.", "muted"))
        if paginate:
            self.pagination(data, offset, 50, paginate)

    def search_page(self):
        self.global_search.grab_focus()
        self.content.append(label("Cherche un titre ou un artiste dans la barre en haut, puis appuie sur Entrée.", "muted", True))
        self.search_results = box(spacing=4)
        self.content.append(self.search_results)

    def search(self, query):
        if not query:
            return
        self.generation += 1
        clear(self.search_results)
        self.load_page(lambda: self.api.request("/search", q=query, type="track", limit=10),
                       lambda data: self.render_tracks(data.get("tracks", {}), self.search_results, direct=True))

    def devices(self):
        self.content.append(label("Reprends là où tu t’es arrêté.", "subtitle"))
        self.content.append(label("Ouvre Spotify sur ton téléphone pour le rendre disponible. Choisis ensuite la sortie audio ici.", "muted", True))
        self.content.append(button("Actualiser les appareils", lambda: self.navigate("devices"), "secondary"))
        self.load_page(lambda: self.api.request("/me/player/devices"), self.render_devices)

    def render_devices(self, data):
        for device in data.get("devices", []):
            row = box(False, 14, "device")
            row.append(label("▣" if device.get("type") == "Computer" else "▯", "device-icon"))
            text = box(spacing=5)
            text.set_hexpand(True)
            text.append(label(device.get("name", "Appareil"), "song"))
            text.append(label("Lecture en cours" if device.get("is_active") else device.get("type", "Spotify Connect"), "muted"))
            row.append(text)
            select = button("Écouter ici", lambda d=device: self.transfer(d), "primary" if device.get("is_active") else "secondary")
            select.set_sensitive(bool(device.get("id")) and not device.get("is_restricted"))
            row.append(select)
            self.content.append(row)
        if not data.get("devices"):
            self.empty("Aucun appareil pour le moment.", "Reconnecte ton compte dans Réglages ou ouvre Spotify sur ton téléphone.", "Mon compte", lambda: self.navigate("settings"))

    def transfer(self, device):
        self.control("/me/player", body={"device_ids": [device["id"]], "play": bool(self.state.get("is_playing"))})
        self.status("Transfert demandé vers « " + device["name"] + " ». La liste sera actualisée après confirmation Spotify.")
        GLib.timeout_add_seconds(2, lambda: (self.navigate("devices") if self.page == "devices" else None) or False)

    def show_settings(self):
        intro = box(spacing=14, css="settings-card")
        intro.append(label("Ton compte", "section-title"))
        intro.append(label(self.profile.get_text(), "hero-title"))
        actions = box(False)
        actions.append(button("Mes playlists", lambda: self.navigate("library"), "primary"))
        actions.append(button("Se déconnecter", self.logout, "secondary"))
        intro.append(actions)
        intro.append(label("Plume est indépendant et non affilié à Spotify.", "tiny"))
        self.content.append(intro)
        options = box(spacing=14, css="settings-card")
        options.append(label("À ta façon", "section-title"))
        quality = box(False)
        quality.append(label("Qualité audio", "muted"))
        select = Gtk.DropDown.new_from_strings(["Économe", "Équilibrée", "Haute qualité"])
        select.set_selected([96, 160, 320].index(self.settings.data["bitrate"]) if self.settings.data["bitrate"] in [96, 160, 320] else 1)
        select.set_sensitive(not self.logging_in)
        select.connect("notify::selected", self.quality_changed)
        quality.append(select)
        options.append(quality)
        toggle = Gtk.CheckButton(label="Afficher les paroles", active=self.settings.data["lyrics"])
        toggle.connect("toggled", self.lyrics_toggled)
        options.append(toggle)
        options.append(label("Les paroles viennent de LRCLIB : le titre et l’artiste sont transmis à ce service quand tu ouvres la page Paroles.", "tiny", True))
        options.append(label("Pour changer de sortie audio, ouvre Appareils et choisis ton PC ou ton téléphone.", "muted", True))
        self.content.append(options)

    def quality_changed(self, dropdown, *_):
        self.settings.data["bitrate"] = [96, 160, 320][dropdown.get_selected()]
        self.settings.save()
        self.run_task(lambda: (self.engine.stop(), self.engine.start()), lambda _: self.status("Qualité modifiée. Sélectionne à nouveau Plume dans Appareils."))

    def lyrics_toggled(self, toggle):
        self.settings.data["lyrics"] = toggle.get_active()
        self.settings.save()
        self.lyrics.cache.clear()
        self.lyric_data = None
        self.last_lyric_key = None

    def login(self):
        if self.logging_in or self.restoring or self.verifying or self.demo:
            return
        if self.session_pending:
            self.restore_session()
            return
        self.logging_in = True
        self.engine.login_cancelled.clear()
        self.login_serial += 1
        serial = self.login_serial
        self.login_url = None
        self.show_welcome()
        self.status("Préparation de la connexion sécurisée…")
        def receive_url(url):
            validate_browser_url(url)
            GLib.idle_add(self.receive_login_url, url, serial)
        def work():
            try:
                path, token = self.engine.authenticate(receive_url)
                if self.engine.login_cancelled.is_set():
                    self.engine.logout()
                    raise UserError("Connexion annulée.")
                self.oauth.accept_browser_session(path, token)
                return True, None
            except Exception as exc:
                return None, exc
        def done(result):
            self.logging_in = False
            self.login_url = None
            self.browser.close()
            self.refresh_login()
            profile, error = result
            if serial != self.login_serial:
                self.logging_in = True
                self.refresh_login()
                def cleaned(_):
                    self.logging_in = False
                    self.refresh_login()
                    self.status("Connexion annulée. Tu peux réessayer.")
                self.run_task(lambda: (self.oauth.logout(), self.engine.logout()), cleaned)
                return
            if error:
                self.show_welcome()
                self.status(str(error) if isinstance(error, UserError) else "La connexion a échoué. Réessaie.")
            else:
                self.session_attempt = 0
                self.restore_session()
        if not self.run_task(work, done):
            self.logging_in = False
            self.refresh_login()

    def receive_login_url(self, url, serial):
        if self.closed or not self.logging_in or serial != self.login_serial or self.engine.login_cancelled.is_set():
            return False
        second = bool(self.login_url)
        self.login_url = url
        self.refresh_login()
        self.status("Encore une validation pour écouter sur ce PC. Utilise le même compte Spotify."
            if second else "Connecte-toi dans ton navigateur. L’autorisation peut porter le nom « ncspot ».")
        self.reopen_login()
        return False

    def reopen_login(self, choose=False):
        if not self.login_url or not self.logging_in:
            return
        current = self.login_url
        def result(error):
            if self.logging_in and self.login_url == current and error:
                self.status(error)
        self.browser.open(current, self.window, result, choose)

    def copy_login(self):
        if self.login_url and self.logging_in:
            self.window.get_clipboard().set(self.login_url)
            self.status("Lien copié. Colle-le dans la barre d’adresse de ton navigateur, puis valide Spotify.")

    def cancel_login(self):
        self.login_serial += 1
        if self.restoring:
            self.end_session_wait("Chargement arrêté. Ta session est conservée ; tu peux réessayer plus tard.")
            return
        self.login_url = None
        self.browser.close()
        self.engine.login_cancelled.set()
        self.refresh_login()
        self.status("Annulation en cours…")

    def on_connected(self, profile):
        self.library_cache.activate(profile)
        self.connected = True
        self.run_task(self.engine.start)
        self.profile.set_text(profile.get("display_name") or "Connecté")
        self.status("Tu es connecté. Choisis une playlist et lance la musique.")
        for control in self.controls:
            control.set_sensitive(True)
        self.navigate("library")
        self.poll()

    def logout(self):
        if self.logging_in or self.audio_login:
            self.status("Termine ou annule la connexion en cours avant de te déconnecter.")
            return
        self.connected = False
        self.library_cache.clear()
        self.login_serial += 1
        self.generation += 1
        self.restoring = True
        def signed_out(_):
            self.restoring = False
            self.refresh_login()
            self.status("Tu es déconnecté. À bientôt sur Plume.")
        self.run_task(lambda: (self.oauth.logout(), self.engine.logout()), signed_out)
        clear(self.sidebar_library)
        clear(self.content)
        self.library_items = []
        self.state = {}
        self.lyrics.cache.clear()
        self.last_lyric_key = self.lyric_data = None
        self.profile.set_text("Hors connexion")
        self.update_state(None)
        for control in self.controls:
            control.set_sensitive(False)
        self.navigate("settings")

    def play(self, body):
        if not self.connected:
            return
        # Honour an already selected phone; otherwise locate our own receiver.
        def work():
            state = self.api.request("/me/player")
            target = (state or {}).get("device", {}).get("id")
            if not target:
                devices = self.api.request("/me/player/devices").get("devices", [])
                own = next((d for d in devices if d.get("name") == self.settings.data["device_name"]), None)
                if own:
                    target = own.get("id")
            if not target:
                raise UserError("Choisis une sortie dans Appareils. Si ton PC est absent, reconnecte-toi dans Réglages.")
            self.api.request("/me/player/play", "PUT", body, device_id=target)
        self.run_task(work, lambda _: self.poll())

    def control(self, path, method="PUT", body=None, **params):
        if self.connected:
            self.run_task(lambda: self.api.request(path, method, body, **params), lambda _: self.poll())

    def toggle(self):
        if self.state.get("is_playing"):
            self.control("/me/player/pause")
        else:
            self.play({})

    def shuffle(self):
        self.control("/me/player/shuffle", state=str(not self.state.get("shuffle_state", False)).lower())

    def repeat(self):
        modes = ["off", "context", "track"]
        mode = self.state.get("repeat_state", "off")
        self.control("/me/player/repeat", state=modes[(modes.index(mode)+1) % 3] if mode in modes else "off")

    def seek(self, _, __, value):
        if getattr(self, "seek_timer", None):
            GLib.source_remove(self.seek_timer)
        self.progress.set_value(value)
        def send():
            self.seek_timer = None
            self.control("/me/player/seek", position_ms=int(value))
            return False
        self.seek_timer = GLib.timeout_add(250, send)
        return True

    def change_volume(self, _, __, value):
        if hasattr(self, "volume_timer") and self.volume_timer:
            GLib.source_remove(self.volume_timer)
        self.volume.set_value(value)
        def send():
            self.volume_timer = None
            self.control("/me/player/volume", volume_percent=int(value))
            return False
        self.volume_timer = GLib.timeout_add(250, send)
        return True

    def poll(self):
        if self.polling or not self.connected or self.closed or self.demo:
            return
        self.polling = True
        serial = self.login_serial
        self.last_poll = time.monotonic()
        def work():
            try:
                return self.api.request("/me/player"), None
            except Exception as exc:
                return None, exc
        def done(result):
            self.polling = False
            if not self.connected or serial != self.login_serial:
                return
            data, error = result
            if error:
                self.status(str(error) if isinstance(error, UserError) else "Impossible d’actualiser le lecteur.")
            else:
                self.update_state(data)
        if not self.run_task(work, done, quiet=True):
            self.polling = False

    def update_state(self, data):
        self.state = data or {}
        self.state_time = time.monotonic()
        track = self.state.get("item") or {}
        self.song.set_text(track.get("name", "La musique commence ici"))
        self.artist.set_text(", ".join(a["name"] for a in track.get("artists", [])) or "Choisis un titre et un appareil.")
        self.play_btn.set_child(Gtk.Image.new_from_icon_name("media-playback-pause-symbolic" if self.state.get("is_playing") else "media-playback-start-symbolic"))
        self.progress.set_range(0, max(1, track.get("duration_ms", 1)))
        self.progress.set_sensitive(bool(track))
        self.duration.set_text(format_time(track.get("duration_ms")))
        device = self.state.get("device") or {}
        self.volume.set_sensitive(device.get("supports_volume", True) and device.get("volume_percent") is not None)
        if device.get("volume_percent") is not None:
            self.volume.set_value(device["volume_percent"])
        (self.shuffle_btn.add_css_class if self.state.get("shuffle_state") else self.shuffle_btn.remove_css_class)("active")
        (self.repeat_btn.add_css_class if self.state.get("repeat_state", "off") != "off" else self.repeat_btn.remove_css_class)("active")
        self.repeat_btn.set_tooltip_text("Répétition : " + {"off": "désactivée", "context": "sélection", "track": "ce titre"}.get(self.state.get("repeat_state"), "désactivée"))
        key = track.get("id") or track.get("uri")
        if key != self.track_key:
            self.track_key = key
            clear(self.player_cover)
            images = (track.get("album") or {}).get("images")
            self.player_cover.append(self.artwork.picture(images, 54) if images else label("♫", "cover"))
            self.lyric_data = None
            self.lyric_index = -2
            if self.page == "lyrics":
                self.navigate("lyrics")

    def show_lyrics(self):
        if not self.settings.data["lyrics"]:
            self.empty("Chaque morceau a ses mots.", "Active LRCLIB dans Réglages pour afficher les paroles disponibles, synchronisées quand le service les fournit.", "Réglages des paroles", lambda: self.navigate("settings"))
            return
        track = self.state.get("item")
        if not track:
            self.empty("Lance un morceau.", "Ses paroles apparaîtront ici si elles sont disponibles.")
            return
        self.content.append(label(track.get("name", ""), "hero-title", True))
        self.lyric_label = label("Recherche des paroles…", "lyrics", True)
        self.content.append(self.lyric_label)
        self.content.append(label("Paroles fournies par LRCLIB · disponibilité variable", "tiny"))
        if self.lyric_data is not None and self.last_lyric_key == self.track_key:
            self.render_lyrics(self.lyric_data)
            return
        key = self.track_key
        if self.lyric_pending == key:
            return
        self.lyric_pending = key
        def work():
            try:
                return self.lyrics.get(track), None
            except Exception as exc:
                return None, exc
        def done(result):
            self.lyric_pending = None
            data, error = result
            if key != self.track_key or not self.settings.data["lyrics"]:
                return
            if error:
                if self.page == "lyrics":
                    self.lyric_label.set_text("Paroles indisponibles pour le moment. Réouvre cette page pour réessayer.")
            else:
                self.last_lyric_key = key
                self.lyric_data = data
                if self.page == "lyrics":
                    self.render_lyrics(data)
        if not self.run_task(work, done):
            self.lyric_pending = None

    def render_lyrics(self, data):
        self.lyric_index = -2
        if not data["lines"]:
            self.lyric_label.set_text("Morceau instrumental." if data["instrumental"] else data["plain"] or "Aucune parole disponible pour ce morceau.")
        else:
            self.update_lyrics(self.position())

    def position(self):
        pos = self.state.get("progress_ms") or 0
        if self.state.get("is_playing"):
            pos += (time.monotonic()-self.state_time)*1000
        return min(pos, (self.state.get("item") or {}).get("duration_ms", 0))

    def update_lyrics(self, position):
        if self.page != "lyrics" or not self.lyric_data or not self.lyric_data["lines"]:
            return
        lines = self.lyric_data["lines"]
        index = bisect.bisect_right([line[0] for line in lines], position)-1
        if index != self.lyric_index:
            self.lyric_index = index
            text = "\n\n".join(("→  " if i == index else "") + lines[i][1] for i in range(max(0,index-1), min(len(lines),index+3)))
            self.lyric_label.set_text(text or "…")

    def tick(self):
        if self.closed:
            return False
        pos = self.position()
        self.progress.set_value(pos)
        self.elapsed.set_text(format_time(pos))
        self.update_lyrics(pos)
        now = time.monotonic()
        if self.restoring and self.session_retry_at:
            seconds = max(0, int(self.session_retry_at - now) + 1)
            self.status(f"{self.session_wait_reason} Ta connexion est conservée. Nouvel essai automatique dans {seconds} s.")
        if now-self.last_poll >= (6 if self.window.is_active() else 15):
            self.poll()
        if int(now) % 5 == 0 or not hasattr(self, "memory_checked"):
            self.memory_checked = True
            rss = memory_usage()
            self.ram.set_text(f"{rss/1_000_000:.0f} Mo · interface + audio")
            limit = memory_limit()
            self.cap_label.set_text("Plafond Linux actif · 300 Mo" if limit and limit <= 300_000_000 else "Objectif 300 Mo · plafond non actif")
            if rss > 250_000_000:
                self.lyrics.cache.clear()
                self.status("La mémoire approche du plafond de 300 Mo.")
        return True

    def shutdown(self, *_):
        self.closed = True
        for source in self.page_retry_sources:
            GLib.source_remove(source)
        self.page_retry_sources.clear()
        self.oauth.cancelled.set()
        if self.session_watchdog:
            GLib.source_remove(self.session_watchdog)
            self.session_watchdog = None
        if self.session_retry_source:
            GLib.source_remove(self.session_retry_source)
            self.session_retry_source = None
        self.browser.close()
        self.artwork.close()
        self.engine.close()
        self.pool.shutdown(wait=False, cancel_futures=True)


def main():
    parser = argparse.ArgumentParser(description="Plume, client musical natif Linux et Windows")
    parser.add_argument("--demo", action="store_true", help="Aperçu hors ligne, sans réseau ni compte")
    args = parser.parse_args()
    os.umask(0o077)
    if os.environ.get("PLUME_CAPPED") == "1":
        limit = memory_limit()
        if limit is None or limit > 300_000_000:
            raise SystemExit("Plume : Linux n’a pas appliqué le plafond de 300 Mo. Lancement annulé.")
    app = Plume(demo=args.demo)
    return app.run([])


if __name__ == "__main__":
    raise SystemExit(main())
