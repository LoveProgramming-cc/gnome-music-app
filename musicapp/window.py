# Music — the window: sidebar | content | (Playing Next), with Now Playing sliding over all of it.
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import dialogs  # noqa: E402
from .content import Content  # noqa: E402
from .i18n import T  # noqa: E402
from .nowplaying import NowPlaying  # noqa: E402
from .playerbar import PlayerBar, QueuePanel  # noqa: E402
from .sidebar import Sidebar  # noqa: E402

AUDIO_PATTERNS = ('*.mp3', '*.m4a', '*.aac', '*.flac', '*.wav', '*.ogg', '*.oga', '*.opus', '*.weba', '*.mp4', '*.m4v', '*.mov', '*.webm', '*.mkv')
FILTERS = {
    'audio': (('audio/*', 'video/*'), AUDIO_PATTERNS),
    'image': (('image/*',), ()),
    'archive': (('application/zip',), ('*.zip',)),
    'subtitle': (('text/vtt', 'application/x-subrip'), ('*.srt', '*.vtt', '*.ass', '*.ssa', '*.lrc')),
}


class MusicWindow(Adw.ApplicationWindow):
    def __init__(self, app, core):
        super().__init__(application=app, title=T('appName', 'Music'), default_width=980, default_height=640)
        self.set_size_request(760, 460)
        self.core = core
        core.ui = self
        self._actions()

        self.sidebar = Sidebar(self)
        self.content = Content(self)
        self.player = PlayerBar(self)
        middle = Gtk.Overlay(child=self.content)
        middle.add_overlay(self.player)
        self.queue = QueuePanel(self)
        self.inner = Adw.OverlaySplitView(content=middle, sidebar=self.queue, sidebar_position=Gtk.PackType.END, show_sidebar=False,
                                          min_sidebar_width=280, max_sidebar_width=280)
        self.outer = Adw.OverlaySplitView(content=self.inner, sidebar=self.sidebar, min_sidebar_width=214, max_sidebar_width=240, sidebar_width_fraction=0.22)
        self.now = NowPlaying(self)
        self.pages = Gtk.Stack(transition_type=Gtk.StackTransitionType.OVER_UP_DOWN, transition_duration=260)
        self.pages.add_named(self.outer, 'main')
        self.pages.add_named(self.now, 'now')
        self.toasts = Adw.ToastOverlay(child=self.pages)
        self.set_content(self.toasts)
        # In a narrow window Playing Next lies over the content instead of squeezing it.
        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse('max-width: 960sp'))
        narrow.add_setter(self.inner, 'collapsed', True)
        self.add_breakpoint(narrow)
        self.inner.connect('notify::show-sidebar', self._queue_moved)

        # Files dragged onto the window: songs, a playlist archive, a cover for the open playlist.
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect('drop', lambda _t, value, _x, _y: core.add_paths([file.get_path() for file in value.get_files() if file.get_path()]) or True)
        self.add_controller(drop)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect('key-pressed', self._key)
        self.add_controller(keys)
        self.connect('notify::fullscreened', lambda *_: self.refresh())

        core.on_render.append(self.refresh)
        if core.library_missing:
            missing, core.library_missing = core.library_missing, ''
            GLib.idle_add(lambda: self.alert(T('libraryLocation', 'Library location'), T('libraryMissing', 'The library folder “{name}” was not found.').replace('{name}', missing)) and False)
        core.on_tick.append(self.tick)
        self.refresh()

    # ------------------------------------------------------------ actions and menus
    def _actions(self):
        core = self.core
        entries = {
            'import-songs': self.import_songs, 'import-playlist': self.import_playlist, 'new-playlist': core.create_playlist, 'new-folder': core.create_folder,
            'export-playlist': lambda: core.current_playlist() and core.export_playlist(core.current_playlist()['id']),
            'toggle': core.toggle, 'next': core.next, 'previous': core.previous, 'shuffle': lambda: core.set_shuffle(not core.lib['shuffle']),
            'repeat': core.cycle_repeat, 'now-playing': self.toggle_now, 'queue': self.toggle_queue, 'search': lambda: self.pages.get_visible_child_name() == 'main' and self.content.focus_search(),
            'fullscreen': self.toggle_fullscreen, 'settings': lambda: dialogs.settings_dialog(self), 'shortcuts': lambda: dialogs.shortcuts_dialog(self),
            'about': lambda: dialogs.about_dialog(self),
        }
        for name, callback in entries.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', lambda _action, _parameter, callback=callback: callback())
            self.add_action(action)

    def primary_menu(self):
        menu = Gio.Menu()
        file = Gio.Menu()
        file.append(T('menuImportSongs', 'Import Songs…'), 'win.import-songs')
        file.append(T('importPlaylist', 'Import Playlist…'), 'win.import-playlist')
        file.append(T('newPlaylist', 'New Playlist'), 'win.new-playlist')
        file.append(T('menuNewFolder', 'New Playlist Folder'), 'win.new-folder')
        file.append(T('exportPlaylist', 'Export Playlist…'), 'win.export-playlist')
        menu.append_section(None, file)
        controls = Gio.Menu()
        for label, action in ((T('menuPlayPause', 'Play / Pause'), 'toggle'), (T('menuNext', 'Next'), 'next'), (T('menuPrevious', 'Previous'), 'previous'),
                              (T('menuShuffle', 'Shuffle'), 'shuffle'), (T('menuRepeat', 'Repeat'), 'repeat')):
            controls.append(label, f'win.{action}')
        section = Gio.Menu()
        section.append_submenu(T('menuControls', 'Controls'), controls)
        menu.append_section(None, section)
        app = Gio.Menu()
        app.append(T('settings', 'Settings'), 'win.settings')
        app.append(T('keyboardShortcuts', 'Keyboard Shortcuts'), 'win.shortcuts')
        app.append(T('aboutApp', 'About Music'), 'win.about')
        menu.append_section(None, app)
        return menu

    def _key(self, _controller, key, _code, state):
        core = self.core
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK):
            return False
        if self.get_visible_dialog() is not None:
            return False   # a sheet is open: its keys are its own
        typing = isinstance(self.get_focus(), (Gtk.Text, Gtk.TextView))
        if key == Gdk.KEY_Escape and core.show_now and not typing:
            if self.is_fullscreen():
                self.unfullscreen()
            else:
                self.close_now()
            return True
        if key == Gdk.KEY_space and not typing:
            core.toggle()
            return True
        return False

    # ------------------------------------------------------------ views
    def toggle_now(self):
        self.core.show_now = not self.core.show_now and bool(self.core.current_song())
        self.core.render()

    def close_now(self):
        self.core.show_now = False
        self.core.render()

    def toggle_queue(self):
        self.core.show_queue = not self.core.show_queue
        self.core.render()

    def _queue_moved(self, split, _spec):
        # Closed by a click beside it (while it lies over the content).
        if split.get_show_sidebar() != self.core.show_queue:
            self.core.show_queue = split.get_show_sidebar()
            self.core.render()

    def toggle_fullscreen(self):
        if self.is_fullscreen():
            self.unfullscreen()
        else:
            self.fullscreen()

    def search_for(self, text):
        self.content.search_for(text)

    def teardown(self):
        """This window is being replaced (the language changed): stop listening."""
        for callbacks, mine in ((self.core.on_render, self.refresh), (self.core.on_tick, self.tick)):
            if mine in callbacks:
                callbacks.remove(mine)
        self.now.teardown()

    def refresh(self):
        core = self.core
        if core.show_now and not core.current_song():
            core.show_now = False
        showing = 'now' if core.show_now else 'main'
        if self.pages.get_visible_child_name() != showing:
            self.pages.set_visible_child_name(showing)
            if showing == 'main' and self.is_fullscreen():
                self.unfullscreen()
        self.sidebar.update()
        self.content.update()
        self.player.update()
        if self.inner.get_show_sidebar() != core.show_queue:
            self.inner.set_show_sidebar(core.show_queue)
        if core.show_queue:
            self.queue.update()
        if core.show_now:
            self.now.update()

    def tick(self):
        if self.core.show_now:
            self.now.tick()
        else:
            self.player.tick()

    # ------------------------------------------------------------ what the core asks for
    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=4))

    def alert(self, title, message):
        dialog = Adw.AlertDialog(heading=title, body=message)
        dialog.add_response('ok', T('done', 'Done'))
        dialog.present(self)

    def confirm(self, question, detail, confirm_label, confirmed, parent=None):
        dialog = Adw.AlertDialog(heading=question, body=detail)
        dialog.add_response('cancel', T('cancel', 'Cancel'))
        dialog.add_response('confirm', confirm_label)
        dialog.set_response_appearance('confirm', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.connect('response', lambda _dialog, response: response == 'confirm' and confirmed())
        dialog.present(parent or self)

    def prompt(self, title, message, value, entered):
        entry = Gtk.Entry(text=value, activates_default=True, max_length=80)
        dialog = Adw.AlertDialog(heading=title, body=message, extra_child=entry)
        dialog.add_response('cancel', T('cancel', 'Cancel'))
        dialog.add_response('ok', T('save', 'Save'))
        dialog.set_response_appearance('ok', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('ok')
        dialog.set_close_response('cancel')
        dialog.connect('response', lambda _dialog, response: response == 'ok' and entered(entry.get_text()))
        dialog.present(self)
        entry.grab_focus()

    def edit_info(self, song_id):
        dialogs.info_dialog(self, song_id)

    @staticmethod
    def _filters(kind):
        mime_types, patterns = FILTERS[kind]
        chosen = Gtk.FileFilter()
        for mime_type in mime_types:
            chosen.add_mime_type(mime_type)
        for pattern in patterns:
            chosen.add_pattern(pattern)
            chosen.add_pattern(pattern.upper())
        filters = Gio.ListStore(item_type=Gtk.FileFilter)
        filters.append(chosen)
        return filters

    def pick_files(self, kind, multiple, picked, parent=None):
        dialog = Gtk.FileDialog(modal=True, filters=self._filters(kind))

        def finished(source, result):
            try:
                files = source.open_multiple_finish(result) if multiple else [source.open_finish(result)]
            except GLib.Error:
                return   # dismissed
            paths = [file.get_path() for file in files if file and file.get_path()]
            if paths:
                picked(paths)
        (dialog.open_multiple if multiple else dialog.open)(self, None, finished)

    def save_file(self, name, chosen):
        dialog = Gtk.FileDialog(modal=True, initial_name=name)

        def finished(source, result):
            try:
                file = source.save_finish(result)
            except GLib.Error:
                return
            if file and file.get_path():
                chosen(file.get_path())
        dialog.save(self, None, finished)

    def pick_folder(self, chosen):
        dialog = Gtk.FileDialog(modal=True)

        def finished(source, result):
            try:
                folder = source.select_folder_finish(result)
            except GLib.Error:
                return
            if folder and folder.get_path():
                chosen(folder.get_path())
        dialog.select_folder(self, None, finished)

    def import_songs(self):
        self.pick_files('audio', True, self.core.add_paths)

    def import_playlist(self):
        self.pick_files('archive', False, lambda paths: self.core.import_archive(paths[0]))

    def choose_cover(self):
        item = self.core.current_playlist()
        if item:
            self.pick_files('image', False, lambda paths: self.core.set_cover(item, self.core.store.add_file(paths[0])['id']))

    def add_subtitle(self):
        self.pick_files('subtitle', False, lambda paths: self.core.add_subtitle_file(paths[0]))
