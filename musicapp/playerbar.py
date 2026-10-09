# Music — the player that floats over the content, and the Playing Next panel.
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk, Pango  # noqa: E402

from .art import Art  # noqa: E402
from .core import clock  # noqa: E402
from .i18n import T  # noqa: E402
from .widgets import RowList, SeekRow, Shrinkable, Transport, force_ltr, icon_button, popup_menu, set_class  # noqa: E402


def love_button(core, pixels, box):
    button = icon_button('non-starred-symbolic', pixels, T('toggleFavourite', 'Favourite / Unfavourite'), box)
    button.add_css_class('love')
    button.connect('clicked', lambda _b: core.current_song() and core.toggle_loved([core.current_song()['id']]))
    return button


def update_love(button, song):
    button.set_visible(bool(song))
    loved = bool(song and song.get('loved'))
    button.image.set_from_icon_name('starred-symbolic' if loved else 'non-starred-symbolic')
    set_class(button, 'loved', loved)
    set_class(button, 'accent', loved)


def queue_rows(core, detail=False):
    player = core.player
    rows = []
    for position in range(player['index'] + 1, len(player['queue'])):
        song = core.song(player['queue'][position])
        if song:
            rows.append({'id': f'q:{position}', 'art': core.art_of(song), 'title': song['title'], 'subtitle': song.get('artist') or '',
                         'detail': clock(song['duration']) if detail and song.get('duration') else ''})
    return rows


def queue_list(core, icon_size, row_height, inset_bottom=0):
    position = lambda key: int(key[2:])   # noqa: E731
    return RowList(icon_size, row_height, on_activate=lambda key: core.play_queue_position(position(key)),
                   on_menu=lambda parent, x, y, key, _selected: popup_menu(parent, core.queue_menu(position(key)), x, y),
                   drag_kind='queue', on_reorder=lambda order: core.reorder_queue([position(key) for key in order]), inset_bottom=inset_bottom)


class PlayerBar(Gtk.Box):
    def __init__(self, window, floating=True):
        super().__init__(spacing=4, valign=Gtk.Align.END)
        self.window = window
        self.core = core = window.core
        self.add_css_class('player-bar')
        if floating:
            self.set_margin_start(12)
            self.set_margin_end(12)
            self.set_margin_bottom(12)
        self.transport = Transport(core, small=(16, 30), medium=(18, 30), large=(20, 36))
        for button in self.transport.buttons:
            self.append(button)

        # Pointing at the cover offers the full-window Now Playing view.
        self.art = Art(38, radius=6)
        self.expand = icon_button('view-fullscreen-symbolic', 16, classes=('flat', 'expand'))
        self.expand.set_size_request(38, 38)
        self.expand.connect('clicked', lambda _b: window.toggle_now())
        wrap = Gtk.Overlay(child=self.art, margin_start=6, margin_end=4, valign=Gtk.Align.CENTER)
        wrap.add_overlay(self.expand)
        wrap.add_css_class('art-wrap')
        self.append(wrap)

        self.title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.title.add_css_class('now-title')
        # The star appears while the pointer is on the title row; a favourite keeps it lit.
        self.love = love_button(core, 13, 20)
        title_row = Gtk.Box(spacing=3)
        title_row.add_css_class('now-row')
        title_row.append(self.title)
        title_row.append(self.love)
        self.artist = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.artist.add_css_class('now-artist')
        self.seek = SeekRow(core)
        middle = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        middle.append(title_row)
        middle.append(self.artist)
        middle.append(self.seek)
        self.append(middle)

        self.queue = icon_button('queue-symbolic', 16, T('upNext', 'Playing Next'), 30)
        self.queue.connect('clicked', lambda _b: window.toggle_queue())
        self.mute = icon_button('audio-volume-high-symbolic', 16, box=30)
        self.mute.connect('clicked', lambda _b: core.toggle_mute())
        self.volume = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.volume.set_draw_value(False)
        self.volume.set_size_request(64, -1)
        self.volume.add_css_class('volume')
        self.volume.connect('change-value', lambda _s, _t, value: core.set_volume(max(0, min(100, value)) / 100) and False)
        # While a video fills the window: the whole screen, one click away.
        self.full = icon_button('view-fullscreen-symbolic', 16, T('fullscreen', 'Fullscreen'), 30)
        self.full.connect('clicked', lambda _b: window.toggle_fullscreen())
        for child in (self.queue, self.mute, self.volume, self.full):
            self.append(child)
        force_ltr(self)

    def update(self):
        core = self.core
        song = core.current_song()
        self.transport.update()
        self.art.set_path(core.art_of(song))
        self.expand.set_visible(bool(song))
        self.expand.set_tooltip_text(T('closeNowPlaying', 'Back') if core.show_now else T('nowPlaying', 'Now Playing'))
        self.title.set_label(song['title'] if song else T('notPlaying', 'Not Playing'))
        self.artist.set_label(' — '.join(part for part in (song.get('artist'), song.get('album')) if part) if song else '')
        update_love(self.love, song)
        set_class(self.queue, 'accent', core.show_queue)
        self.full.set_visible(core.show_now and bool(song and song.get('video')))
        self.full.image.set_from_icon_name('view-restore-symbolic' if self.window.is_fullscreen() else 'view-fullscreen-symbolic')
        self.queue.set_visible(not core.show_now)
        volume = core.lib['volume']
        self.mute.image.set_from_icon_name('audio-volume-muted-symbolic' if volume <= 0 else 'audio-volume-low-symbolic' if volume < 0.34
                                           else 'audio-volume-medium-symbolic' if volume < 0.67 else 'audio-volume-high-symbolic')
        # The volume controls give way when the queue panel narrows the content area.
        narrow = core.show_queue and not core.show_now and not self.window.inner.get_collapsed()
        self.mute.set_visible(not narrow)
        self.volume.set_visible(not narrow)
        if abs(self.volume.get_value() - volume * 100) > 0.5:
            self.volume.set_value(round(volume * 100))
        self.seek.tick()

    def tick(self):
        self.seek.tick()


class QueuePanel(Adw.Bin):
    def __init__(self, window):
        super().__init__()
        self.core = core = window.core
        title = Gtk.Label(label=T('upNext', 'Playing Next'), xalign=0, margin_start=8, ellipsize=Pango.EllipsizeMode.END)
        title.add_css_class('heading')
        self.clear = Gtk.Button(label=T('clear', 'Clear'))
        self.clear.add_css_class('flat')
        self.clear.add_css_class('accent')
        self.clear.connect('clicked', lambda _b: core.clear_queue())
        bar = Adw.HeaderBar(show_title=False)
        bar.pack_start(title)
        bar.pack_end(self.clear)
        self.rows = queue_list(core, 34, 46)
        view = Adw.ToolbarView(content=self.rows)
        view.add_top_bar(bar)
        self.add_css_class('queue-panel')
        self.set_child(Shrinkable(view))

    def update(self):
        rows = queue_rows(self.core)
        self.clear.set_sensitive(bool(rows))
        self.rows.set_rows(rows, reorderable=True, placeholder=T('queueEmpty', 'Nothing is queued.'))
