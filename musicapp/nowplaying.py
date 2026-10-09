# Music — Now Playing: the whole window. Cover (or the video itself) with the transport,
# Playing Next and History beside it.
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

from .art import Art, Backdrop  # noqa: E402
from .core import clock  # noqa: E402
from .i18n import T  # noqa: E402
from .playerbar import PlayerBar, love_button, queue_list, queue_rows, update_love  # noqa: E402
from .widgets import RowList, SeekRow, Transport, force_ltr, icon_button, popup_menu, set_class  # noqa: E402

HIDE_AFTER_MS = 2600
COLUMN = 560


class AudioPage(Gtk.Overlay):
    def __init__(self, window):
        super().__init__()
        self.core = core = window.core
        self.backdrop = Backdrop()
        self.set_child(self.backdrop)

        close = icon_button('go-down-symbolic', 16, T('closeNowPlaying', 'Back'), classes=('circular',))
        close.connect('clicked', lambda _b: window.close_now())
        bar = Adw.HeaderBar(show_title=False)
        bar.pack_start(close)

        self.art = Art(290, radius=12, minimum=130, shadow=True)   # gives way when the window is short
        self.title = Gtk.Label(xalign=0.5, ellipsize=Pango.EllipsizeMode.END)
        self.title.add_css_class('np-title')
        self.artist = Gtk.Label(xalign=0.5, ellipsize=Pango.EllipsizeMode.END)
        self.artist.add_css_class('np-artist')
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True)
        names.append(self.title)
        names.append(self.artist)
        self.love = love_button(core, 16, 30)
        self.love.remove_css_class('flat')
        # The star on one side, as much room on the other: the names sit in the middle.
        about = Gtk.Box(spacing=8, margin_top=8)
        about.append(Gtk.Box(width_request=30))
        about.append(names)
        about.append(self.love)
        self.seek = SeekRow(core)
        self.transport = Transport(core, small=(18, 36), medium=(24, 44), large=(30, 52), need_song=True)
        buttons = Gtk.Box(spacing=20, halign=Gtk.Align.CENTER)
        for button in self.transport.buttons:
            buttons.append(button)
        force_ltr(buttons)
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, width_request=320)
        for child in (about, self.seek, buttons):
            column.append(child)
        # One width whatever the title: as wide as this side allows (up to COLUMN), long names
        # are cut short, short ones centred.
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True, valign=Gtk.Align.CENTER)
        left.append(self.art)
        left.append(Adw.Clamp(child=column, maximum_size=COLUMN, tightening_threshold=COLUMN))

        self.tabs = Adw.ToggleGroup()
        self.tabs.add(Adw.Toggle(name='queue', label=T('upNext', 'Playing Next')))
        self.tabs.add(Adw.Toggle(name='history', label=T('history', 'History')))
        self.tabs.connect('notify::active-name', self._tab_changed)
        self.clear = Gtk.Button(label=T('clear', 'Clear'))
        self.clear.add_css_class('flat')
        self.clear.add_css_class('accent')
        self.clear.connect('clicked', lambda _b: core.clear_queue() if core.now_tab == 'queue' else core.clear_history())
        top = Gtk.Box(spacing=8)
        top.append(self.tabs)
        top.append(Gtk.Box(hexpand=True))
        top.append(self.clear)
        self.queue = queue_list(core, 36, 48)
        self.history = RowList(36, 48, on_activate=lambda key: core.play_from_history(int(key[2:])))
        self.lists = Gtk.Stack()
        self.lists.add_named(self.queue, 'queue')
        self.lists.add_named(self.history, 'history')
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, width_request=340)
        right.add_css_class('np-right')
        right.append(top)
        right.append(self.lists)

        body = Gtk.Box(spacing=28, margin_start=28, margin_end=28, margin_top=4, margin_bottom=24)
        body.append(left)
        body.append(Adw.Clamp(child=right, maximum_size=340, hexpand=False))
        view = Adw.ToolbarView(content=body)
        view.add_top_bar(bar)
        self.add_overlay(view)
        self.set_measure_overlay(view, True)

    def _tab_changed(self, tabs, _spec):
        name = 'history' if tabs.get_active_name() == 'history' else 'queue'
        if name != self.core.now_tab:
            self.core.now_tab = name
            self.core.render()

    def update(self):
        core = self.core
        song = core.current_song()
        art = core.art_of(song)
        self.backdrop.set_path(art)
        self.art.set_path(art)
        self.title.set_label(song['title'] if song else T('notPlaying', 'Not Playing'))
        self.artist.set_label((' — '.join(part for part in (song.get('artist'), song.get('album')) if part) or T('unknownArtist', 'Unknown Artist')) if song else '')
        update_love(self.love, song)
        self.transport.update()
        self.seek.tick()
        if self.tabs.get_active_name() != core.now_tab:
            self.tabs.set_active_name(core.now_tab)
        self.lists.set_visible_child_name(core.now_tab)
        if core.now_tab == 'queue':
            rows = queue_rows(core, detail=True)
            self.queue.set_rows(rows, reorderable=True, placeholder=T('queueEmpty', 'Nothing is queued.'))
        else:
            unknown = T('unknownArtist', 'Unknown Artist')
            rows = [{'id': f'h:{index}', 'art': core.art_of(song), 'title': song['title'], 'subtitle': song.get('artist') or unknown,
                     'detail': clock(song['duration']) if song.get('duration') else ''}
                    for index, song in enumerate(map(core.song, core.lib['history'])) if song]
            self.history.set_rows(rows, placeholder=T('historyEmpty', 'Songs you play appear here.'))
        self.clear.set_sensitive(bool(rows))

    def tick(self):
        self.seek.tick()


class VideoPage(Gtk.Overlay):
    """A video takes the whole window; the controls come up while the pointer moves over it."""

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.core = core = window.core
        self.add_css_class('video-page')
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, hexpand=True, vexpand=True)
        self.set_child(Gtk.WindowHandle(child=self.picture))   # the window moves by dragging the picture

        # The words: drawn by the app, over the picture, sized with the window.
        self.words = Gtk.Label(use_markup=True, wrap=True, justify=Gtk.Justification.CENTER, halign=Gtk.Align.CENTER, valign=Gtk.Align.END,
                               margin_start=32, margin_end=32, margin_bottom=28, visible=False)
        self.words.add_css_class('video-words')
        self.add_overlay(self.words)
        self._shown = None
        self._scale = 0
        self._placed = None
        # The words can be picked up and put anywhere on the picture.
        self.words.set_cursor(Gdk.Cursor.new_from_name('grab'))
        drag = Gtk.GestureDrag()
        drag.connect('drag-begin', self._words_grabbed)
        drag.connect('drag-update', self._words_dragged)
        drag.connect('drag-end', lambda *_: self.core.save())
        self.add_controller(drag)

        close = icon_button('go-down-symbolic', 16, T('closeNowPlaying', 'Back'), classes=('circular', 'osd'))
        close.connect('clicked', lambda _b: window.close_now())
        self.captions = icon_button('media-view-subtitles-symbolic', 16, T('subtitles', 'Subtitles'), classes=('circular', 'osd'))
        self.captions.connect('clicked', self._captions_menu)
        self.full = icon_button('view-fullscreen-symbolic', 16, T('fullscreen', 'Fullscreen'), classes=('circular', 'osd'))
        self.full.connect('clicked', lambda _b: window.toggle_fullscreen())
        controls = self.controls = Gtk.WindowControls(side=Gtk.PackType.END)
        controls.add_css_class('osd-controls')
        top = Gtk.Box(spacing=8, margin_start=10, margin_end=8, margin_top=8)
        for child in (close, Gtk.Box(hexpand=True), self.captions, self.full, controls):
            top.append(child)
        self.top = Gtk.Revealer(child=Gtk.WindowHandle(child=top), transition_type=Gtk.RevealerTransitionType.CROSSFADE, valign=Gtk.Align.START, reveal_child=True)
        self.add_overlay(self.top)

        self.bar = PlayerBar(window)
        self.bottom = Gtk.Revealer(child=self.bar, transition_type=Gtk.RevealerTransitionType.CROSSFADE, valign=Gtk.Align.END, reveal_child=True)
        self.add_overlay(self.bottom)

        self._hide = 0
        self._over = False
        self._menu = False
        self._last = (0, 0)
        motion = Gtk.EventControllerMotion()
        motion.connect('motion', self._moved)
        self.add_controller(motion)
        for part in (top, self.bar):
            hover = Gtk.EventControllerMotion()
            hover.connect('enter', lambda *_: self._set_over(True))
            hover.connect('leave', lambda *_: self._set_over(False))
            part.add_controller(hover)
        double = Gtk.GestureClick()
        double.connect('pressed', lambda gesture, count, *_: count == 2 and (gesture.set_state(Gtk.EventSequenceState.CLAIMED), window.toggle_fullscreen()))
        self.picture.add_controller(double)
        self.add_tick_callback(self._frame)

    # -- the controls come and go
    def _captions_menu(self, button):
        menu = popup_menu(button, self.core.subtitle_menu())
        if menu:
            self._menu = True
            menu.connect('closed', lambda _m: (setattr(self, '_menu', False), self.reveal()))

    def _set_over(self, over):
        self._over = over
        if not over:
            self.reveal()

    def _moved(self, _controller, x, y):
        if abs(x - self._last[0]) + abs(y - self._last[1]) > 2:
            self._last = (x, y)
            self.reveal()

    def reveal(self):
        self.top.set_reveal_child(True)
        self.bottom.set_reveal_child(True)
        self.set_cursor(None)
        if self._hide:
            GLib.source_remove(self._hide)
        self._hide = GLib.timeout_add(HIDE_AFTER_MS, self._conceal)

    def _conceal(self):
        self._hide = 0
        # They stay while the pointer rests on them, while a menu is open, and while paused.
        if self._over or self._menu or self.core.player['paused'] or not self.get_mapped():
            return False
        self.top.set_reveal_child(False)
        self.bottom.set_reveal_child(False)
        self.set_cursor(Gdk.Cursor.new_from_name('none'))
        return False

    # -- subtitles
    def _words_grabbed(self, gesture, x, y):
        picked = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        ok, bounds = self.words.compute_bounds(self)
        if picked is not self.words or not ok:
            gesture.set_state(Gtk.EventSequenceState.DENIED)   # not on the words: the picture moves the window
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._grab = (bounds.get_x() + bounds.get_width() / 2, bounds.get_y() + bounds.get_height() / 2)

    def _words_dragged(self, _gesture, dx, dy):
        width, height = max(1, self.get_width()), max(1, self.get_height())
        at = (max(0.0, min(1.0, (self._grab[0] + dx) / width)), max(0.0, min(1.0, (self._grab[1] + dy) / height)))
        self.core.set_subtitle_look(place='custom', at=at, save=False)
        self.reveal()

    def _place_words(self):
        """At the top, at the bottom (clear of the controls while they show), or where they were dragged to."""
        words, place = self.words, self.core.subtitle_place()
        width, height = self.get_width(), self.get_height()
        if place == 'custom':
            fx, fy = self.core.subtitle_at()
            # (measure() counts the margins in, and the margins are what is being set here.)
            sideways = words.get_margin_start() + words.get_margin_end()
            upright = words.get_margin_top() + words.get_margin_bottom()
            w = min(words.measure(Gtk.Orientation.HORIZONTAL, -1)[1] - sideways, max(1, width - 16))
            h = words.measure(Gtk.Orientation.VERTICAL, w + sideways)[1] - upright
            wanted = (Gtk.Align.START, Gtk.Align.START, round(max(0, min(width - w, fx * width - w / 2))), 0, round(max(0, min(height - h, fy * height - h / 2))), 0)
        elif place == 'top':
            wanted = (Gtk.Align.CENTER, Gtk.Align.START, 32, 32, 56 if self.top.get_reveal_child() or self.top.get_child_revealed() else 20, 0)
        else:
            wanted = (Gtk.Align.CENTER, Gtk.Align.END, 32, 32, 0, 96 if self.bottom.get_reveal_child() or self.bottom.get_child_revealed() else 28)
        if wanted != self._placed:
            self._placed = wanted
            words.set_halign(wanted[0])
            words.set_valign(wanted[1])
            words.set_margin_start(wanted[2])
            words.set_margin_end(wanted[3])
            words.set_margin_top(wanted[4])
            words.set_margin_bottom(wanted[5])

    def _frame(self, _widget, _clock):
        if not self.get_mapped():
            return True
        markup = self.core.subtitle_markup()
        scale = max(9, min(120, round(self.get_height() * 0.04 * self.core.subtitle_size() / 100)))
        if markup != self._shown or scale != self._scale:
            self._shown, self._scale = markup, scale
            self.words.set_visible(bool(markup))
            if markup:
                self.words.set_markup(f'<span size="{scale * 1024}">{markup}</span>')
        if markup:
            self._place_words()
        return True

    def update(self):
        core = self.core
        paintable = core.audio.paintable
        if self.picture.get_paintable() is not paintable:
            self.picture.set_paintable(paintable)
        self.bar.update()
        set_class(self.captions, 'active', core.tracks.selected >= 0)
        self.full.image.set_from_icon_name('view-restore-symbolic' if self.window.is_fullscreen() else 'view-fullscreen-symbolic')
        self.controls.set_visible(not self.window.is_fullscreen())
        if core.player['paused']:
            self.reveal()

    def tick(self):
        self.bar.tick()


class NowPlaying(Gtk.Stack):
    def __init__(self, window):
        super().__init__(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.core = window.core
        self.audio = AudioPage(window)
        self.video = VideoPage(window)
        self.add_named(self.audio, 'audio')
        self.add_named(self.video, 'video')
        self._paintable = self.core.audio.connect('paintable', lambda _audio: self.update())

    def teardown(self):
        self.core.audio.disconnect(self._paintable)

    def showing_video(self):
        song = self.core.current_song()
        return bool(song and song.get('video'))

    def update(self):
        page = self.video if self.showing_video() else self.audio
        if self.get_visible_child() is not page:
            self.set_visible_child(page)
            if page is self.video:
                self.video.reveal()
        page.update()

    def tick(self):
        (self.video if self.showing_video() else self.audio).tick()
