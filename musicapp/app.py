# Music — a music player for GNOME: your own library, playlists and playlist folders,
# shuffle, repeat, a queue, history, a full Now Playing view, videos with subtitles.
import os
import sys

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, i18n  # noqa: E402
from .core import ACCENTS, DEFAULT_ACCENT, Core  # noqa: E402
from .i18n import T  # noqa: E402
from .search import SearchProvider  # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
ACCELS = {
    'win.import-songs': ['<Control>o'], 'win.new-playlist': ['<Control>n'], 'win.new-folder': ['<Control><Shift>n'], 'win.export-playlist': ['<Control>e'],
    'win.next': ['<Alt>Right'], 'win.previous': ['<Alt>Left'], 'win.shuffle': ['<Control>s'], 'win.repeat': ['<Control>r'],
    'win.now-playing': ['<Control>l'], 'win.queue': ['<Control>u'], 'win.search': ['<Control>f'], 'win.fullscreen': ['F11'],
    'win.settings': ['<Control>comma'], 'win.shortcuts': ['<Control>question'], 'app.quit': ['<Control>q'],
}


class MusicApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.core = None
        self.window = None
        self.mpris = None
        self.search = None
        self.tray = None
        self._accent = Gtk.CssProvider()

    def do_dbus_register(self, connection, object_path):
        # Registered before the name is owned, so GNOME Shell's first question finds it.
        self.search = SearchProvider(self, connection)
        return Adw.Application.do_dbus_register(self, connection, object_path)

    def do_startup(self):
        Adw.Application.do_startup(self)
        Gtk.Window.set_default_icon_name(APP_ID)
        self.core = Core(DATA)
        i18n.set_language(self.core.lib.get('language') or '')
        GLib.set_application_name(T('appName', 'Music'))
        if i18n.is_rtl():
            Gtk.Widget.set_default_direction(Gtk.TextDirection.RTL)
        display = Gdk.Display.get_default()
        if display:
            Gtk.IconTheme.get_for_display(display).add_search_path(os.path.join(DATA, 'icons'))
            style = Gtk.CssProvider()
            style.load_from_path(os.path.join(DATA, 'style.css'))
            Gtk.StyleContext.add_provider_for_display(display, style, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            Gtk.StyleContext.add_provider_for_display(display, self._accent, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        self.apply_appearance()
        self.apply_accent()
        quit_action = Gio.SimpleAction.new('quit', None)
        quit_action.connect('activate', lambda *_: self.quit())
        self.add_action(quit_action)
        for action, keys in ACCELS.items():
            self.set_accels_for_action(action, keys)

    def do_activate(self):
        if not self.window:
            from .mpris import Mpris
            from .window import MusicWindow
            self.window = MusicWindow(self, self.core)
            # The desktop's media controls appear with the window, not while the app only answers searches.
            self.mpris = Mpris(self, self.core)
            from .tray import Tray
            self.tray = Tray(self, self.core, os.path.join(DATA, 'icons'))
        self.window.present()

    def set_language(self, chosen):
        """The interface in another language, at once: the window is built again around the same state."""
        from . import dialogs
        from .window import MusicWindow
        self.core.lib['language'] = chosen
        self.core.save()
        i18n.set_language(chosen)
        Gtk.Widget.set_default_direction(Gtk.TextDirection.RTL if i18n.is_rtl() else Gtk.TextDirection.LTR)
        old = self.window
        if old:
            width, height, maximized = old.get_width(), old.get_height(), old.is_maximized()
            old.teardown()
            self.window = MusicWindow(self, self.core)
            self.window.set_default_size(width, height)
            if maximized:
                self.window.maximize()
            self.window.present()
            old.destroy()
            dialogs.settings_dialog(self.window)
        self.core.render()

    def do_open(self, files, _count, _hint):
        self.activate()
        self.open_uris([file.get_uri() for file in files])

    def do_shutdown(self):
        if self.core:
            self.core.remember_route()
            self.core.audio.stop()
            self.core.file.flush()
        Adw.Application.do_shutdown(self)

    def open_uris(self, uris):
        """music://… points at a song or a playlist; files are added to the library."""
        paths = []
        for uri in uris:
            if uri.lower().startswith('music:'):
                self.core.open_url(uri)
            else:
                path = Gio.File.new_for_uri(uri).get_path()
                if path:
                    paths.append(path)
        if paths:
            self.core.add_paths(paths)

    def apply_library_settings(self):
        """Another library was opened: its appearance, accent and language are the ones that count."""
        self.apply_appearance()
        self.apply_accent()
        if (self.core.lib.get('language') or '') != ('' if i18n.language == i18n._detect() else i18n.language):
            self.set_language(self.core.lib.get('language') or '')

    # Light, dark or following the system: the app's own choice.
    def apply_appearance(self):
        scheme = {'light': Adw.ColorScheme.FORCE_LIGHT, 'dark': Adw.ColorScheme.FORCE_DARK}.get(self.core.lib.get('appearance'), Adw.ColorScheme.DEFAULT)
        Adw.StyleManager.get_default().set_color_scheme(scheme)

    def apply_accent(self):
        colour = ACCENTS.get(self.core.lib.get('accent') or DEFAULT_ACCENT, ACCENTS[DEFAULT_ACCENT])
        # The fill is the colour itself; text and icons get the legible version of it.
        self._accent.load_from_string(f':root {{ --accent-bg-color: {colour}; --accent-fg-color: white; '
                                      f'--accent-color: oklab(from {colour} var(--standalone-color-oklab)); }}')


def main(argv=None):
    i18n.init(os.path.join(DATA, 'locales'))
    return MusicApp().run(sys.argv if argv is None else argv)
