# Music — the sidebar: the library, playlists and their folders, import progress.
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gtk, Pango  # noqa: E402

from .art import Art  # noqa: E402
from .i18n import T  # noqa: E402
from .widgets import drag_payload, icon_button, parse_payload, point_in, popup_menu  # noqa: E402


class Sidebar(Adw.Bin):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.core = window.core
        self._signature = None
        self._rows = {}

        header = Adw.HeaderBar(title_widget=Adw.WindowTitle(title=T('appName', 'Music')))
        menu = Gtk.MenuButton(icon_name='open-menu-symbolic', menu_model=window.primary_menu(), tooltip_text=T('mainMenu', 'Main Menu'), primary=True)
        header.pack_end(menu)

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.add_css_class('navigation-sidebar')
        self.list.connect('row-activated', lambda _list, row: self.core.go(*row.key.split(':', 1)))
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.list, vexpand=True)
        # A right-click on the empty part (or on a library row): new playlist, new folder, import.
        blank = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)

        def blank_pressed(gesture, _count, x, y):
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            popup_menu(self, self.core.add_menu(), *point_in(scroller, self, x, y))
        blank.connect('pressed', blank_pressed)
        scroller.add_controller(blank)

        # Storing files takes a while (tags, artwork, duration): show how far it is.
        self.progress_label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.progress_label.add_css_class('caption')
        self.progress_bar = Gtk.ProgressBar()
        self.progress = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5, margin_start=14, margin_end=14, margin_bottom=4, visible=False)
        self.progress.append(self.progress_label)
        self.progress.append(self.progress_bar)

        new = Gtk.Button(hexpand=True, child=Adw.ButtonContent(icon_name='list-add-symbolic', label=T('new', 'New')))
        new.add_css_class('tinted')
        new.connect('clicked', lambda button: popup_menu(button, self.core.add_menu()))
        add = Gtk.Button(label=T('import', 'Import'), hexpand=True)
        add.add_css_class('flat')
        add.connect('clicked', lambda _b: window.import_songs())
        buttons = Gtk.Box(spacing=6, margin_start=10, margin_end=10, margin_top=6, margin_bottom=10, homogeneous=True)
        buttons.append(new)
        buttons.append(add)
        bottom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bottom.append(self.progress)
        bottom.append(buttons)

        view = Adw.ToolbarView(content=scroller)
        view.add_top_bar(header)
        view.add_bottom_bar(bottom)
        self.set_child(view)

    # -- rows
    def _header(self, title):
        label = Gtk.Label(label=title, xalign=0)
        label.add_css_class('sidebar-heading')
        row = Gtk.ListBoxRow(child=label, selectable=False, activatable=False, can_focus=False)
        row.key = ''
        return row

    def _row(self, key, title, icon=None, cover=None, indent=0, folder=None):
        box = Gtk.Box(spacing=8, margin_start=16 * indent)
        if folder is not None:
            # The chevron folds a folder without leaving the current view.
            chevron = icon_button('pan-end-symbolic' if folder.get('collapsed') else 'pan-down-symbolic', 14, box=20)
            chevron.add_css_class('disclosure')
            chevron.connect('clicked', lambda _b: self.core.toggle_folder(folder['id']))
            box.append(chevron)
        if cover:
            art = Art(20, radius=4)
            art.set_path(cover)
            box.append(art)
        else:
            image = Gtk.Image(icon_name=icon, pixel_size=16)
            image.add_css_class('nav-icon')
            box.append(image)
        box.append(Gtk.Label(label=title, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END))
        row = Gtk.ListBoxRow(child=box)
        row.key = key
        return row

    def _menu_on(self, row, build):
        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)

        def pressed(gesture, _count, x, y):
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            # (Under the list itself the menu comes out too short and scrolls.)
            popup_menu(self, build(), *point_in(row, self, x, y))
        click.connect('pressed', pressed)
        row.add_controller(click)

    def _drops_on(self, row, kind, target_id):
        """What may land on a sidebar row: a playlist (onto a folder, or out of one),
        songs or files (onto a playlist)."""
        core = self.core

        def dropped_text(_target, value, _x, _y):
            what, ids = parse_payload(value)
            if what == 'playlist' and ids:
                if kind == 'folder':
                    core.set_playlist_folder(ids[0], target_id)
                elif kind == 'playlists':
                    core.set_playlist_folder(ids[0], '')
                elif kind == 'playlist' and ids[0] != target_id:
                    core.set_playlist_folder(ids[0], (core.playlist_of(target_id) or {}).get('folderId') or '')
                return True
            if what == 'songs' and kind == 'playlist' and core.playlist_of(target_id):
                core.add_to_playlist(core.playlist_of(target_id), ids)
                return True
            return False
        text = Gtk.DropTarget.new(str, Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        text.connect('drop', dropped_text)
        row.add_controller(text)
        if kind == 'playlist':
            files = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
            files.connect('drop', lambda _t, value, _x, _y: core.add_paths([file.get_path() for file in value.get_files() if file.get_path()], playlist_id=target_id) or True)
            row.add_controller(files)

    def _playlist_row(self, item, indent):
        row = self._row(f'playlist:{item["id"]}', item['name'], cover=self.core.playlist_cover(item), indent=indent)
        self._menu_on(row, lambda: self.core.playlist_menu(item['id']))
        source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        source.connect('prepare', lambda *_: Gdk.ContentProvider.new_for_value(drag_payload('playlist', [item['id']])))
        row.add_controller(source)
        self._drops_on(row, 'playlist', item['id'])
        return row

    def _build(self):
        core, lib = self.core, self.core.lib
        while (child := self.list.get_first_child()) is not None:
            self.list.remove(child)
        rows = [
            self._header(T('library', 'Library')),
            self._row('recent:', T('recentlyAdded', 'Recently Added'), 'document-open-recent-symbolic'),
            self._row('artists:', T('artists', 'Artists'), 'avatar-default-symbolic'),
            self._row('albums:', T('albums', 'Albums'), 'media-optical-symbolic'),
            self._row('songs:', T('songs', 'Songs'), 'music-note-symbolic'),
            self._row('favorites:', T('favorites', 'Favourites'), 'starred-symbolic'),
            self._header(T('playlists', 'Playlists')),
        ]
        every = self._row('playlists:', T('allPlaylists', 'All Playlists'), 'playlist-symbolic')
        self._drops_on(every, 'playlists', '')
        rows.append(every)
        for folder in lib['folders']:
            row = self._row(f'folder:{folder["id"]}', folder['name'], 'folder-symbolic', folder=folder)
            self._menu_on(row, lambda folder=folder: core.folder_menu(folder['id']))
            self._drops_on(row, 'folder', folder['id'])
            rows.append(row)
            if not folder.get('collapsed'):
                rows.extend(self._playlist_row(item, 1) for item in lib['playlists'] if item.get('folderId') == folder['id'])
        folders = {folder['id'] for folder in lib['folders']}
        rows.extend(self._playlist_row(item, 0) for item in lib['playlists'] if item.get('folderId') not in folders)
        self._rows = {}
        for row in rows:
            self.list.append(row)
            if row.key:
                self._rows[row.key] = row

    def update(self):
        core, lib = self.core, self.core.lib
        signature = (
            tuple((folder['id'], folder['name'], bool(folder.get('collapsed'))) for folder in lib['folders']),
            tuple((item['id'], item['name'], item.get('folderId') or '', core.playlist_cover(item)) for item in lib['playlists']),
        )
        if signature != self._signature:
            self._signature = signature
            self._build()
        kind = core.route['kind']
        selected = f'{kind}s:' if kind in ('album', 'artist') else core.route_key()
        row = self._rows.get(selected)
        if row is not None and not core.show_now:
            if self.list.get_selected_row() is not row:
                self.list.select_row(row)
        else:
            self.list.unselect_all()

        importing = core.importing
        self.progress.set_visible(bool(importing))
        if importing:
            text = T('exportingProgress', 'Exporting {done} of {total}…') if importing.get('exporting') else T('importingProgress', 'Importing {done} of {total}…')
            self.progress_label.set_label(text.replace('{done}', str(importing['done'])).replace('{total}', str(importing['total'])))
            self.progress_bar.set_fraction(importing['done'] / max(1, importing['total']))
