# Music — the middle of the window: a page header over a list of songs, or of
# albums / artists / playlists.
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gtk, Pango  # noqa: E402

from .art import Art  # noqa: E402
from .core import ALBUM_SEP, clock, long_clock  # noqa: E402
from .i18n import T  # noqa: E402
from .widgets import RowList, icon_button, popup_menu, set_class  # noqa: E402

INSET = 78   # what the floating player covers at the end of a list


class Actions(Gtk.Box):
    """Shuffle · Play · More — the buttons under a page title."""

    def __init__(self, window):
        super().__init__(spacing=8)
        core = window.core
        self.shuffle = icon_button('media-playlist-shuffle-symbolic', 16, T('shuffle', 'Shuffle'), 34, classes=('circular', 'tinted'))
        self.shuffle.connect('clicked', lambda _b: core.shuffle_visible())
        self.play = Gtk.Button(child=Adw.ButtonContent(icon_name='media-playback-start-symbolic', label=T('play', 'Play')), valign=Gtk.Align.CENTER)
        for name in ('pill', 'tinted', 'play-pill'):
            self.play.add_css_class(name)
        self.play.connect('clicked', lambda _b: core.play_visible())
        self.more = icon_button('view-more-symbolic', 16, T('more', 'More'), 34, classes=('circular',))
        self.more.connect('clicked', lambda button: popup_menu(button, core.more_menu()))
        for button in (self.shuffle, self.play, self.more):
            self.append(button)

    def update(self, has_songs):
        self.shuffle.set_sensitive(has_songs)
        self.play.set_sensitive(has_songs)


class Content(Adw.Bin):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.core = core = window.core
        self._route = None
        self._query = ''

        # The bar at the top: the page title (on pages without a cover), sort and search.
        self.bar_title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, margin_start=8)
        self.bar_title.add_css_class('page-title')
        self.search = Gtk.SearchEntry(placeholder_text=T('search', 'Search'), width_request=190, valign=Gtk.Align.CENTER)
        self.search.set_search_delay(120)
        self.search.connect('search-changed', self._searched)
        self.search.connect('stop-search', lambda entry: (entry.set_text(''), self.songs.view.grab_focus()))
        self.sort = icon_button('view-sort-descending-symbolic', 16, T('sort', 'Sort'), classes=('flat',))
        self.sort.connect('clicked', lambda button: popup_menu(button, core.sort_menu()))
        bar = Adw.HeaderBar(show_title=False)
        bar.pack_start(self.bar_title)
        bar.pack_end(self.search)
        bar.pack_end(self.sort)

        # With a cover (playlist, album, artist): the picture, and the title block beside it.
        self.cover = Art(150, radius=12)
        self.cover.set_valign(Gtk.Align.END)
        click = Gtk.GestureClick()
        click.connect('released', lambda *_: core.current_playlist() and window.choose_cover())
        self.cover.add_controller(click)
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect('accept', lambda *_: bool(core.current_playlist()))
        drop.connect('drop', lambda _t, value, _x, _y: core.add_paths([file.get_path() for file in value.get_files() if file.get_path()], cover_target=True) or True)
        self.cover.add_controller(drop)
        self.title_edit = Gtk.EditableLabel(xalign=0, max_width_chars=80)   # a playlist's name is edited in place
        self.title_edit.add_css_class('page-title')
        self.title_edit.add_css_class('editable-title')
        self.title_edit.connect('notify::editing', self._renamed)
        self.title_label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.title_label.add_css_class('page-title')
        self.subtitle = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.subtitle.add_css_class('page-subtitle')
        self.cover_meta = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.cover_meta.add_css_class('dim-label')
        self.cover_actions = Actions(window)
        self.cover_actions.set_margin_top(6)
        block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, valign=Gtk.Align.END, hexpand=True)
        for child in (self.title_edit, self.title_label, self.subtitle, self.cover_meta, self.cover_actions):
            block.append(child)
        self.cover_header = Gtk.Box(spacing=20, margin_start=20, margin_end=18, margin_top=2, margin_bottom=14)
        self.cover_header.append(self.cover)
        self.cover_header.append(block)

        # Without one (Songs, Recently Added, Favourites): the buttons and the count.
        self.plain_actions = Actions(window)
        self.plain_meta = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, hexpand=True)
        self.plain_meta.add_css_class('dim-label')
        self.plain_header = Gtk.Box(spacing=12, margin_start=20, margin_end=18, margin_top=2, margin_bottom=12)
        self.plain_header.append(self.plain_actions)
        self.plain_header.append(self.plain_meta)

        self.songs = RowList(40, 52, multiple=True, on_activate=lambda song_id: core.play_songs(core.visible_ids(), song_id),
                             on_menu=lambda parent, x, y, song_id, selected: popup_menu(parent, core.song_menu(song_id, selected), x, y),
                             drag_kind='songs', on_reorder=self._reordered, on_delete=core.remove_from_playlist, inset_bottom=INSET)
        self.collection = RowList(46, 60, single_click=True, on_activate=self._opened, inset_bottom=INSET)
        self.stack = Gtk.Stack()
        self.stack.add_named(self.songs, 'songs')
        self.stack.add_named(self.collection, 'collection')

        view = Adw.ToolbarView(content=self.stack)
        view.add_top_bar(bar)
        view.add_top_bar(self.cover_header)
        view.add_top_bar(self.plain_header)
        self.set_child(view)

    # -- events
    def _searched(self, entry):
        if entry.get_text() != self.core.query:
            self.core.query = self._query = entry.get_text()
            self.core.render()

    def _renamed(self, label, _spec):
        item = self.core.current_playlist()
        if item and not label.get_editing() and label.get_text().strip() != item['name']:
            self.core.rename_playlist(item, label.get_text())

    def _reordered(self, order):
        item = self.core.current_playlist()
        if item and self.core.manual_order():
            item['songIds'] = order
            self.core.save()
            self.core.render()

    def _opened(self, key):
        kind = self.core.route['kind']
        self.core.go('album' if kind == 'albums' else 'artist' if kind == 'artists' else 'playlist', key)

    def focus_search(self):
        self.search.grab_focus()

    def search_for(self, text):
        self.search.set_text(text)

    # -- drawing
    def update(self):
        core = self.core
        route = core.route_key()
        if route != self._route:
            # A new page starts unfiltered, at the top.
            self._route = route
            for rows in (self.songs, self.collection):
                rows.scroller.get_vadjustment().set_value(0)
        if core.query != self._query:
            # Changed from elsewhere (a new page clears it): never while it is being typed.
            self._query = core.query
            if self.search.get_text() != core.query:
                self.search.set_text(core.query)
        if core.is_detail():
            self._detail()
        else:
            self._collection()

    def _collection(self):
        core, lib = self.core, self.core.lib
        kind = core.route['kind']
        count = T('songsCount', 'songs')
        if kind == 'albums':
            rows = [{'id': album['id'], 'title': album['title'], 'subtitle': f'{album["artist"]} · {len(album["songs"])} {count}',
                     'art': core.art_of(next((song for song in album['songs'] if song.get('artId')), None))} for album in core.albums()]
        elif kind == 'artists':
            rows = [{'id': artist['id'], 'title': artist['title'], 'subtitle': f'{len(artist["songs"])} {count}',
                     'art': core.art_of(next((song for song in artist['songs'] if song.get('artId')), None))} for artist in core.artists()]
        else:
            rows = []
            for item in lib['playlists']:
                if kind == 'folder' and item.get('folderId') != core.route['id']:
                    continue
                folder = core.folder_of(item.get('folderId') or '')
                subtitle = f'{sum(1 for song_id in item["songIds"] if core.song(song_id))} {count}' + (f' · {folder["name"]}' if folder else '')
                rows.append({'id': item['id'], 'title': item['name'], 'subtitle': subtitle, 'art': core.playlist_cover(item)})
        needle = core.query.strip().casefold()
        if needle:
            rows = [row for row in rows if needle in f'{row["title"]}\n{row["subtitle"]}'.casefold()]
        folder = core.folder_of() if kind == 'folder' else None
        title = {'albums': T('albums', 'Albums'), 'artists': T('artists', 'Artists'), 'folder': folder['name'] if folder else ''}.get(kind, T('allPlaylists', 'All Playlists'))
        placeholder = (T('noResults', 'No results') if needle
                       else T('emptyFolder', 'This folder is empty. Drag playlists onto it in the sidebar.') if kind == 'folder'
                       else T('noPlaylists', 'No playlists yet. Use “New” to create one.') if kind == 'playlists'
                       else T('emptyHint', 'Drop audio files here, or choose “Import”.'))
        self.bar_title.set_label(title)
        self.bar_title.set_visible(True)
        self.sort.set_visible(False)
        self.search.set_placeholder_text(T('search', 'Search'))
        self.cover_header.set_visible(False)
        self.plain_header.set_visible(False)
        self.collection.set_rows(rows, placeholder=placeholder)
        self.stack.set_visible_child_name('collection')

    def _detail(self):
        core = self.core
        item = core.current_playlist()
        kind, target = core.route['kind'], core.route['id']
        songs = core.visible_songs()
        playing = core.current_song()
        sort = core.sorts.get(core.route_key())
        titles = {'songs': T('songs', 'Songs'), 'recent': T('recentlyAdded', 'Recently Added'), 'favorites': T('favorites', 'Favourites')}
        title = item['name'] if item else target.split(ALBUM_SEP)[0] if kind == 'album' else target if kind == 'artist' else titles.get(kind, '')
        subtitle = target.split(ALBUM_SEP)[1] if kind == 'album' and ALBUM_SEP in target else ''
        cover = core.playlist_cover(item) if item else core.art_of(next((song for song in core.base_songs() if song.get('artId')), None)) if kind in ('album', 'artist') else ''
        total = sum(song.get('duration') or 0 for song in songs)
        meta = core.busy or f'{len(songs)} {T("songsCount", "songs")}' + (f' · {long_clock(total)}' if songs else '')

        self.sort.set_visible(True)
        set_class(self.sort, 'accent', bool(sort))
        self.search.set_placeholder_text(T('findInPlaylist', 'Find in Playlist') if item else T('search', 'Search'))
        self.cover_header.set_visible(bool(cover))
        self.plain_header.set_visible(not cover)
        self.bar_title.set_visible(not cover)
        if cover:
            self.cover.set_path(cover)
            self.cover.set_tooltip_text(T('coverHint', 'Click or drop an image to change the cover') if item else None)
            set_class(self.cover, 'clickable', bool(item))
            self.title_edit.set_visible(bool(item))
            self.title_label.set_visible(not item)
            if item and not self.title_edit.get_editing() and self.title_edit.get_text() != title:
                self.title_edit.set_text(title)
            self.title_label.set_label(title)
            self.subtitle.set_label(subtitle)
            self.subtitle.set_visible(bool(subtitle))
            self.cover_meta.set_label(meta)
            self.cover_actions.update(bool(songs))
        else:
            self.bar_title.set_label(title)
            self.plain_meta.set_label(meta)
            self.plain_actions.update(bool(songs))

        unknown = T('unknownArtist', 'Unknown Artist')
        rows = [{'id': song['id'], 'art': core.art_of(song), 'title': song['title'],
                 'subtitle': ' — '.join(part for part in (song.get('artist'), song.get('album')) if part) or unknown,
                 'detail': clock(song['duration']) if song.get('duration') else '', 'loved': bool(song.get('loved')),
                 'emphasis': bool(playing) and playing['id'] == song['id']} for song in songs]
        self.songs.set_rows(rows, reorderable=core.manual_order(),
                            placeholder=T('noResults', 'No results') if core.query.strip() else T('emptyHint', 'Drop audio files here, or choose “Import”.'))
        self.stack.set_visible_child_name('songs')
