# Music — the sheets: song info, Settings, Manage Storage, shortcuts, About.
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Graphene', '1.0')
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gsk, Gtk, Pango  # noqa: E402

from . import APP_ID, VERSION  # noqa: E402
from .art import Art  # noqa: E402
from .core import ACCENTS, DEFAULT_ACCENT, STALE_MS, megabytes_of, natkey  # noqa: E402
from . import i18n  # noqa: E402
from .i18n import T  # noqa: E402
from .store import now_ms  # noqa: E402
from .widgets import RowItem  # noqa: E402


def info_dialog(window, song_id):
    core = window.core
    song = core.song(song_id)
    if not song:
        return
    dialog = Adw.Dialog(title=T('getInfo', 'Get Info…').replace('…', ''), content_width=400)
    title = Adw.EntryRow(title=T('colTitle', 'Title'), text=song['title'])
    artist = Adw.EntryRow(title=T('colArtist', 'Artist'), text=song.get('artist') or '')
    album = Adw.EntryRow(title=T('colAlbum', 'Album'), text=song.get('album') or '')

    def save(*_):
        core.edit_song(song_id, title.get_text(), artist.get_text(), album.get_text())
        dialog.close()
    group = Adw.PreferencesGroup(margin_start=14, margin_end=14, margin_top=6, margin_bottom=16)
    for row in (title, artist, album):
        row.connect('entry-activated', save)
        group.add(row)
    cancel = Gtk.Button(label=T('cancel', 'Cancel'))
    cancel.connect('clicked', lambda _b: dialog.close())
    done = Gtk.Button(label=T('save', 'Save'))
    done.add_css_class('suggested-action')
    done.connect('clicked', save)
    bar = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
    bar.pack_start(cancel)
    bar.pack_end(done)
    view = Adw.ToolbarView(content=group)
    view.add_top_bar(bar)
    dialog.set_child(view)
    dialog.present(window)


# ---------------------------------------------------------------- settings
def settings_dialog(window):
    core = window.core
    lib = core.lib
    dialog = Adw.PreferencesDialog(title=T('settings', 'Settings'), content_width=520, search_enabled=False)
    page = Adw.PreferencesPage()

    look = Adw.PreferencesGroup(title=T('appearance', 'Appearance'))
    modes = Adw.ToggleGroup(valign=Gtk.Align.CENTER)
    for name, label in (('system', T('appearanceSystem', 'Automatic')), ('light', T('appearanceLight', 'Light')), ('dark', T('appearanceDark', 'Dark'))):
        modes.add(Adw.Toggle(name=name, label=label))
    modes.set_active_name(lib.get('appearance') if lib.get('appearance') in ('light', 'dark') else 'system')

    def mode_chosen(group, _spec):
        lib['appearance'] = group.get_active_name() or 'system'
        window.get_application().apply_appearance()
        core.save()
    modes.connect('notify::active-name', mode_chosen)
    mode_row = Adw.ActionRow(title=T('appearance', 'Appearance'))
    mode_row.add_suffix(modes)
    look.add(mode_row)

    swatches = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
    buttons = {}

    def mark():
        chosen = lib.get('accent') or DEFAULT_ACCENT
        for name, button in buttons.items():
            button.get_child().set_visible(name == chosen)

    def accent_chosen(_button, name):
        lib['accent'] = '' if name == DEFAULT_ACCENT else name
        window.get_application().apply_accent()
        core.save()
        mark()
    for name in ACCENTS:
        tick = Gtk.Image(icon_name='object-select-symbolic', pixel_size=12)
        button = Gtk.Button(child=tick, tooltip_text=T(f'accent_{name}', name), valign=Gtk.Align.CENTER)
        for css in ('circular', 'swatch', f'swatch-{name}'):
            button.add_css_class(css)
        button.connect('clicked', accent_chosen, name)
        buttons[name] = button
        swatches.append(button)
    mark()
    accent_row = Adw.ActionRow(title=T('accentColor', 'Accent colour'))
    accent_row.add_suffix(swatches)
    look.add(accent_row)
    top_bar = Adw.SwitchRow(title=T('topBar', 'Show in the top bar'), subtitle=T('topBarHint', 'What is playing and the playback controls, next to the clock.'),
                            active=lib.get('topBar', True) is not False)

    def top_bar_changed(row, _spec):
        lib['topBar'] = row.get_active()
        core.save()
        tray = window.get_application().tray
        if tray:
            tray.changed()
    top_bar.connect('notify::active', top_bar_changed)
    look.add(top_bar)
    codes = ['', *i18n.NAMES]
    languages = Adw.ComboRow(title=T('language', 'Language'), model=Gtk.StringList.new([T('appearanceSystem', 'Automatic'), *i18n.NAMES.values()]))
    languages.set_selected(codes.index(lib.get('language')) if lib.get('language') in codes else 0)

    def language_chosen(row, _spec):
        chosen = codes[row.get_selected()]
        if chosen != (lib.get('language') or ''):
            dialog.close()
            GLib.idle_add(lambda: window.get_application().set_language(chosen) and False)
    languages.connect('notify::selected', language_chosen)
    look.add(languages)
    page.add(look)

    words = Adw.PreferencesGroup(title=T('subtitles', 'Subtitles'))

    def slider_row(title, subtitle, low, high, step, value, changed, show):
        label = Gtk.Label(xalign=1, width_chars=5)
        label.add_css_class('numeric')
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, low, high, step)
        scale.set_size_request(170, -1)
        scale.set_draw_value(False)
        scale.set_valign(Gtk.Align.CENTER)
        scale.set_value(value)

        def moved(control):
            number = int(round(control.get_value() / step) * step)
            label.set_label(show(number))
            changed(number)
        scale.connect('value-changed', moved)
        label.set_label(show(int(value)))
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        row.add_suffix(scale)
        row.add_suffix(label)
        return row
    words.add(slider_row(T('subtitleSize', 'Size'), '', 50, 250, 5, core.subtitle_size(), lambda number: core.set_subtitle_look(size=number), lambda number: f'{number}%'))
    places = ['top', 'bottom', 'custom']
    where = Adw.ComboRow(title=T('subtitlePosition', 'Position'), subtitle=T('subtitleDragHint', 'Subtitles can be dragged anywhere on the picture.'),
                         model=Gtk.StringList.new([T('posAutoTop', 'Automatic — top'), T('posAutoBottom', 'Automatic — bottom'), T('posCustom', 'Custom (drag the subtitle)')]))
    where.set_selected(places.index(core.subtitle_place()))
    where.connect('notify::selected', lambda row, _spec: core.set_subtitle_look(place=places[row.get_selected()]))
    words.add(where)
    page.add(words)

    playback = Adw.PreferencesGroup(title=T('playback', 'Playback'))
    fade = Adw.SwitchRow(title=T('fadePause', 'Fade when pausing and resuming'), active=bool(lib.get('fade')))

    def fade_changed(row, _spec):
        lib['fade'] = row.get_active()
        core.save()
    fade.connect('notify::active', fade_changed)
    playback.add(fade)
    seconds = Gtk.Label(xalign=1, width_chars=5)
    seconds.add_css_class('numeric')
    slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 12, 1)
    slider.set_size_request(150, -1)
    slider.set_draw_value(False)
    slider.set_valign(Gtk.Align.CENTER)
    slider.set_value(core.crossfade_seconds())

    def blend_changed(scale):
        value = max(0, min(12, round(scale.get_value())))
        seconds.set_label(T('nSeconds', '{n} s').replace('{n}', str(value)) if value else T('off', 'Off'))
        if value != lib.get('crossfade'):
            lib['crossfade'] = value
            core.save()
    slider.connect('value-changed', blend_changed)
    blend_changed(slider)
    blend = Adw.ActionRow(title=T('crossfade', 'Crossfade songs'))
    blend.add_suffix(slider)
    blend.add_suffix(seconds)
    playback.add(blend)
    page.add(playback)

    artwork = Adw.PreferencesGroup(title=T('defaultArtwork', 'Default artwork'), description=T('defaultArtworkHint', 'Shown for songs that have no cover of their own.'))
    preview = Art(44, radius=6)
    choose = Gtk.Button(label=T('chooseImage', 'Choose Image…'), valign=Gtk.Align.CENTER)
    restore = Gtk.Button(label=T('restoreDefault', 'Restore Default'), valign=Gtk.Align.CENTER)

    def show_art():
        preview.set_path(core.default_art())
        restore.set_sensitive(bool(lib.get('defaultArtId')))

    def set_art(file_id):
        old = lib.get('defaultArtId')
        lib['defaultArtId'] = file_id
        if old and old != file_id:
            core.store.remove(old)
        core.save()
        core.render()
        show_art()
    choose.connect('clicked', lambda _b: window.pick_files('image', False, lambda paths: set_art(core.store.add_file(paths[0])['id']), parent=dialog))
    restore.connect('clicked', lambda _b: set_art(''))
    show_art()
    art_box = Gtk.Box(spacing=10, margin_start=12, margin_end=12, margin_top=10, margin_bottom=10)
    for child in (preview, choose, restore):
        art_box.append(child)
    artwork.add(Adw.PreferencesRow(child=art_box, activatable=False))
    page.add(artwork)

    size = sum(core.store.list().values())
    summary = T('storageSummaryLocal', '{count} songs · {size} stored in the library').replace('{count}', str(len(lib['songs']))).replace('{size}', megabytes_of(size))
    storage = Adw.PreferencesGroup(title=T('storage', 'Storage'))
    manage = Adw.ActionRow(title=summary, subtitle=T('manageStorageHint', 'See what takes up space — largest, never played, not played for a long time — and delete exactly what you choose.'))
    open_storage = Gtk.Button(label=T('manageStorage', 'Manage Storage…'), valign=Gtk.Align.CENTER)

    def manage_clicked(_button):
        dialog.close()
        storage_dialog(window)
    open_storage.connect('clicked', manage_clicked)
    manage.add_suffix(open_storage)
    storage.add(manage)
    place = Adw.ActionRow(title=T('libraryLocation', 'Library location'), subtitle=core.root, subtitle_selectable=True)
    place.add_css_class('property')
    show = Gtk.Button(icon_name='folder-open-symbolic', tooltip_text=T('showInFiles', 'Show Library Folder'), valign=Gtk.Align.CENTER)
    show.add_css_class('flat')
    show.connect('clicked', lambda _b: Gtk.FileLauncher.new(Gio.File.new_for_path(core.root)).launch(window, None, None))
    change = Gtk.Button(label=T('change', 'Change…'), valign=Gtk.Align.CENTER)

    def change_clicked(_button):
        dialog.close()
        window.pick_folder(lambda chosen: change_library(window, chosen))
    change.connect('clicked', change_clicked)
    place.add_suffix(show)
    place.add_suffix(change)
    storage.add(place)
    page.add(storage)

    dialog.add(page)
    dialog.present(window)


def change_library(window, chosen):
    """The library moves to the chosen folder — everything in it, and the old place is emptied."""
    core = window.core
    plan, target = core.plan_library_move(chosen)
    if plan == 'same':
        return
    if plan == 'inside':
        return window.alert(T('libraryLocation', 'Library location'), T('libraryInside', 'Choose a folder outside the current library.'))
    if plan == 'exists':
        dialog = Adw.AlertDialog(heading=T('libraryExistsQuestion', 'There is already a library in that folder. Use it?'),
                                 body=T('libraryExistsDetail', 'The current library stays where it is; nothing is deleted.'))
        dialog.add_response('cancel', T('cancel', 'Cancel'))
        dialog.add_response('use', T('useLibrary', 'Use It'))
        dialog.set_response_appearance('use', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_close_response('cancel')
        dialog.connect('response', lambda _dialog, response: response == 'use' and (core.open_library(target), window.get_application().apply_library_settings()))
        return dialog.present(window)
    bar = Gtk.ProgressBar(show_text=True, text=' ')
    busy = Adw.AlertDialog(heading=T('movingLibrary', 'Moving the library…'), body=target, extra_child=bar, can_close=False)

    def progress(done, total):
        bar.set_fraction(done / max(1, total))
        bar.set_text(T('movingProgress', '{done} of {total}').replace('{done}', str(done)).replace('{total}', str(total)))

    def finished(error):
        busy.force_close()
        if error:
            window.alert(T('moveFailed', 'The library could not be moved'), f'{error}\n\n{T("moveFailedDetail", "Nothing was changed: the library is still in its old place.")}')
        else:
            window.toast(T('libraryMoved', 'The library is now in “{name}”.').replace('{name}', target))
    busy.present(window)
    core.move_library(target, progress, finished)


# ---------------------------------------------------------------- manage storage
# Every song with what it occupies, so space is freed by choice — the largest files, what
# was never played, what has not been played for months — rather than all or nothing.
# Files nothing refers to any more are offered as one separate item.
class StorageBar(Gtk.Widget):
    COLOURS = {'purple': '#a550a7', 'orange': '#f7821b', 'gray': '#8c8c8c'}

    def __init__(self):
        super().__init__(height_request=12, hexpand=True)
        self.parts = []
        self.accent = ACCENTS[DEFAULT_ACCENT]

    def set_parts(self, parts, accent):
        self.parts, self.accent = parts, accent
        self.queue_draw()

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        bounds = Graphene.Rect().init(0, 0, width, height)
        outline = Gsk.RoundedRect()
        outline.init_from_rect(bounds, 4)
        snapshot.push_rounded_clip(outline)
        colour = Gdk.RGBA()
        colour.parse('rgba(128,128,128,0.22)')
        snapshot.append_color(colour, bounds)
        total = max(1, sum(size for _label, size, _colour in self.parts))
        x = 0.0
        for _label, size, name in self.parts:
            w = max(3.0, size / total * width)
            colour = Gdk.RGBA()
            colour.parse(self.accent if name == 'accent' else self.COLOURS[name])
            snapshot.append_color(colour, Graphene.Rect().init(x, 0, w - 1, height))
            x += w
        snapshot.pop()


class StorageDialog(Adw.Dialog):
    def __init__(self, window):
        super().__init__(title=T('manageStorageTitle', 'Storage'), content_width=700, content_height=620)
        self.window = window
        self.core = window.core
        self.files = {}
        self.group = 'songs'
        self.filter = 'all'
        self.sort = ('size', True)   # column, descending
        self.row_ids = []
        self._building = False
        self.filters = {
            'all': lambda song: True,
            'video': lambda song: bool(song.get('video')),
            'never': lambda song: not song.get('plays') and not song.get('lastPlayed'),
            'stale': lambda song: bool(song.get('lastPlayed') or song.get('plays')) and now_ms() - (song.get('lastPlayed') or song.get('added') or 0) > STALE_MS,
            'loose': lambda song: not song.get('loved') and not any(song['id'] in item['songIds'] for item in self.core.lib['playlists']),
        }

        self.total = Gtk.Label()
        self.total.add_css_class('dim-label')
        bar = Adw.HeaderBar()
        bar.pack_end(self.total)

        self.bar = StorageBar()
        self.legend = Gtk.Box(spacing=14)

        # Songs one by one, or whole playlists / playlist folders at a time.
        self.groups = Adw.ToggleGroup()
        for name in ('songs', 'playlists', 'folders'):
            self.groups.add(Adw.Toggle(name=name))
        self.groups.connect('notify::active-name', self._group_changed)
        show = Gtk.Label(label=T('storageShow', 'Show'))
        show.add_css_class('dim-label')
        group_row = Gtk.Box(spacing=10)
        group_row.append(show)
        group_row.append(self.groups)
        self.kinds = Adw.ToggleGroup(halign=Gtk.Align.START)
        for name in self.filters:
            self.kinds.add(Adw.Toggle(name=name))
        self.kinds.connect('notify::active-name', self._filter_changed)
        self.hint = Gtk.Label(xalign=0, wrap=True)
        self.hint.add_css_class('dim-label')
        self.hint.add_css_class('caption')

        self.model = Gio.ListStore(item_type=RowItem)
        self.selection = Gtk.MultiSelection(model=self.model)
        self.selection.connect('selection-changed', lambda *_: self._selection_changed())
        self.table = Gtk.ColumnView(model=self.selection, show_row_separators=False, vexpand=True)
        self.table.add_css_class('storage-table')
        self.columns = {}
        for name, expand, width, end in (('title', True, -1, False), ('artist', True, -1, False), ('played', False, 120, False), ('size', False, 90, True)):
            factory = Gtk.SignalListItemFactory()
            factory.connect('setup', self._setup_cell, name, end)
            factory.connect('bind', self._bind_cell, name)
            column = Gtk.ColumnViewColumn(factory=factory, expand=expand, resizable=True, id=name)
            if width > 0:
                column.set_fixed_width(width)
            column.set_sorter(Gtk.CustomSorter.new(lambda *_: 0, None))
            self.table.append_column(column)
            self.columns[name] = column
        self.table.sort_by_column(self.columns['size'], Gtk.SortType.DESCENDING)
        self.table.get_sorter().connect('changed', self._sort_changed)
        self.empty = Gtk.Label(label=T('storageNothing', 'Nothing here.'), vexpand=True)
        self.empty.add_css_class('dim-label')
        self.body = Gtk.Stack(vexpand=True)
        frame = Gtk.Frame(child=Gtk.ScrolledWindow(child=self.table, hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.body.add_named(frame, 'rows')
        self.body.add_named(self.empty, 'empty')

        self.select_all = Gtk.Button()
        self.select_all.connect('clicked', self._toggle_all)
        self.clean = Gtk.Button()
        self.clean.connect('clicked', self._clean)
        self.delete = Gtk.Button()
        self.delete.add_css_class('destructive-action')
        self.delete.connect('clicked', self._delete)
        done = Gtk.Button(label=T('done', 'Done'))
        done.add_css_class('suggested-action')
        done.connect('clicked', lambda _b: self.close())
        footer = Gtk.Box(spacing=8, margin_top=4)
        for child in (self.select_all, self.clean, Gtk.Box(hexpand=True), self.delete, done):
            footer.append(child)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_start=18, margin_end=18, margin_top=6, margin_bottom=18)
        for child in (self.bar, self.legend, group_row, self.kinds, self.hint, self.body, footer):
            content.append(child)
        view = Adw.ToolbarView(content=content)
        view.add_top_bar(bar)
        self.set_child(view)
        self.refresh()

    # -- what things weigh
    def song_bytes(self, song):
        return self.files.get(song.get('fileId'), song.get('size') or 0) + self.files.get(song.get('artId'), 0)

    def sum_bytes(self, songs):
        return sum(self.song_bytes(song) for song in songs)

    # What a selection in the current view stands for: the songs to delete, and the
    # playlists / folders that go with them.
    def selection_of(self, ids):
        core, lib = self.core, self.core.lib
        if self.group == 'songs':
            return [song for song in map(core.song, ids) if song], [], []
        folders = [folder for folder in lib['folders'] if folder['id'] in ids] if self.group == 'folders' else []
        playlists = ([item for folder in folders for item in core.folder_playlists(folder['id'])] if self.group == 'folders'
                     else [item for item in lib['playlists'] if item['id'] in ids])
        song_ids = dict.fromkeys(song_id for item in playlists for song_id in item['songIds'])
        return [song for song in map(core.song, song_ids) if song], playlists, folders

    def selected_ids(self):
        chosen = self.selection.get_selection()
        return [self.row_ids[chosen.get_nth(index)] for index in range(chosen.get_size()) if chosen.get_nth(index) < len(self.row_ids)]

    def rows(self):
        core, lib = self.core, self.core.lib
        column, descending = self.sort
        count = T('songsCount', 'songs')

        def ordered(items, value):
            return sorted(items, key=lambda item: value(item) if isinstance(value(item), (int, float)) else natkey(value(item)), reverse=descending)
        if self.group == 'playlists':
            folder_name = lambda item: (core.folder_of(item.get('folderId') or '') or {}).get('name', '')   # noqa: E731
            value = {'title': lambda item: item['name'], 'artist': lambda item: len(core.playlist_songs(item)), 'played': folder_name,
                     'size': lambda item: self.sum_bytes(core.playlist_songs(item))}[column]
            return [{'id': item['id'], 'art': core.playlist_cover(item), 'title': item['name'], 'artist': f'{len(core.playlist_songs(item))} {count}',
                     'played': folder_name(item) or '—', 'size': megabytes_of(self.sum_bytes(core.playlist_songs(item)))} for item in ordered(lib['playlists'], value)]
        if self.group == 'folders':
            value = {'title': lambda item: item['name'], 'artist': lambda item: len(core.folder_playlists(item['id'])), 'played': lambda item: len(core.folder_songs(item['id'])),
                     'size': lambda item: self.sum_bytes(core.folder_songs(item['id']))}[column]
            return [{'id': item['id'], 'art': None, 'title': item['name'], 'artist': f'{len(core.folder_playlists(item["id"]))} {T("playlistsCount", "playlists")}',
                     'played': f'{len(core.folder_songs(item["id"]))} {count}', 'size': megabytes_of(self.sum_bytes(core.folder_songs(item['id'])))}
                    for item in ordered(lib['folders'], value)]
        value = {'title': lambda song: song['title'], 'artist': core.artist_of, 'played': lambda song: song.get('lastPlayed') or 0, 'size': self.song_bytes}[column]

        def played(song):
            if song.get('lastPlayed'):
                return GLib.DateTime.new_from_unix_local(song['lastPlayed'] // 1000).format('%x')
            return '—' if song.get('plays') else T('neverPlayed', 'Never')
        return [{'id': song['id'], 'art': core.art_of(song), 'title': song['title'], 'artist': core.artist_of(song), 'played': played(song),
                 'size': megabytes_of(self.song_bytes(song))} for song in ordered(filter(self.filters[self.filter], core.all_songs()), value)]

    # -- table
    def _setup_cell(self, _factory, cell, name, end):
        label = Gtk.Label(xalign=1 if end else 0, ellipsize=Pango.EllipsizeMode.END)
        if name != 'title':
            if name != 'size':
                label.add_css_class('dim-label')
            cell.set_child(label)
            return
        box = Gtk.Box(spacing=8)
        box.art = Art(24, radius=4)
        box.folder = Gtk.Image(icon_name='folder-symbolic', pixel_size=16, width_request=24)
        box.label = label
        for child in (box.art, box.folder, label):
            box.append(child)
        cell.set_child(box)

    def _bind_cell(self, _factory, cell, name):
        data = cell.get_item().data
        child = cell.get_child()
        if name == 'title':
            child.art.set_visible(bool(data['art']))
            child.folder.set_visible(not data['art'])
            if data['art']:
                child.art.set_path(data['art'])
            child.label.set_label(data['title'])
        else:
            child.set_label(data[name])

    def _sort_changed(self, sorter, _change):
        column = sorter.get_primary_sort_column()
        if self._building or column is None:
            return
        self.sort = (column.get_id(), sorter.get_primary_sort_order() == Gtk.SortType.DESCENDING)
        self._fill(keep=True)

    def _group_changed(self, group, _spec):
        name = group.get_active_name() or 'songs'
        if self._building or name == self.group:
            return
        self.group = name
        self.sort = ('size', True)
        self._building = True
        self.table.sort_by_column(self.columns['size'], Gtk.SortType.DESCENDING)
        self._building = False
        self.refresh()

    def _filter_changed(self, group, _spec):
        name = group.get_active_name() or 'all'
        if self._building or name == self.filter:
            return
        self.filter = name
        self._fill()

    def _fill(self, keep=False):
        chosen = set(self.selected_ids()) if keep else set()
        rows = self.rows()
        self._building = True
        self.row_ids = [row['id'] for row in rows]
        self.model.splice(0, self.model.get_n_items(), [RowItem(row) for row in rows])
        for position, row_id in enumerate(self.row_ids):
            if row_id in chosen:
                self.selection.select_item(position, False)
        self._building = False
        self.body.set_visible_child_name('rows' if rows else 'empty')
        self._selection_changed()

    def _selection_changed(self):
        if self._building:
            return
        chosen = self.selected_ids()
        songs, _playlists, _folders = self.selection_of(chosen)
        everything = bool(self.row_ids) and len(chosen) == len(self.row_ids)
        self.select_all.set_label(T('selectNone', 'Select None') if everything else T('selectAllShown', 'Select All Shown'))
        self.select_all.set_sensitive(bool(self.row_ids))
        self.delete.set_sensitive(bool(chosen))
        self.delete.set_label(T('deleteSelectedN', 'Delete {count} · {size}').replace('{count}', str(len(chosen))).replace('{size}', megabytes_of(self.sum_bytes(songs)))
                              if chosen else T('deleteSelected', 'Delete Selected'))

    def _toggle_all(self, _button):
        if self.row_ids and len(self.selected_ids()) == len(self.row_ids):
            self.selection.unselect_all()
        else:
            self.selection.select_all()

    # -- the sheet as a whole
    def refresh(self):
        core, lib = self.core, self.core.lib
        self.files = core.store.list()
        keep = core.referenced_files()
        song_files = {song.get('fileId') for song in core.all_songs()}
        audio = sum(self.files.get(song.get('fileId'), song.get('size') or 0) for song in core.all_songs() if not song.get('video'))
        video = sum(self.files.get(song.get('fileId'), song.get('size') or 0) for song in core.all_songs() if song.get('video'))
        art = sum(size for file_id, size in self.files.items() if file_id in keep and file_id not in song_files)
        unused = [(file_id, size) for file_id, size in self.files.items() if file_id not in keep]
        unused_bytes = sum(size for _file_id, size in unused)
        parts = [part for part in ((T('storageAudio', 'Songs'), audio, 'accent'), (T('storageVideo', 'Videos'), video, 'purple'),
                                   (T('storageArt', 'Artwork'), art, 'orange'), (T('storageUnused', 'Unused files'), unused_bytes, 'gray')) if part[1] > 0]
        self.total.set_label(megabytes_of(audio + video + art + unused_bytes))
        self.bar.set_parts(parts, ACCENTS.get(lib.get('accent') or DEFAULT_ACCENT, ACCENTS[DEFAULT_ACCENT]))
        while (child := self.legend.get_first_child()) is not None:
            self.legend.remove(child)
        for label, size, colour in parts:
            dot = Gtk.Box(width_request=8, height_request=8, valign=Gtk.Align.CENTER)
            dot.add_css_class('legend-dot')
            dot.add_css_class(f'legend-{colour}')
            text = Gtk.Label(label=f'{label}  {megabytes_of(size)}')
            text.add_css_class('caption')
            text.add_css_class('dim-label')
            entry = Gtk.Box(spacing=5)
            entry.append(dot)
            entry.append(text)
            self.legend.append(entry)

        self._building = True
        labels = {'songs': f'{T("songsHeading", "Songs")} {len(lib["songs"])}', 'playlists': f'{T("playlistsHeading", "Playlists")} {len(lib["playlists"])}',
                  'folders': f'{T("foldersHeading", "Folders")} {len(lib["folders"])}'}
        for name, label in labels.items():
            self.groups.get_toggle_by_name(name).set_label(label)
        self.groups.set_active_name(self.group)
        names = {'all': T('filterAll', 'All'), 'video': T('storageVideo', 'Videos'), 'never': T('filterNever', 'Never Played'),
                 'stale': T('filterStale', 'Not Played in 3 Months'), 'loose': T('filterLoose', 'Not in a Playlist')}
        for name, label in names.items():
            self.kinds.get_toggle_by_name(name).set_label(f'{label} {sum(1 for song in core.all_songs() if self.filters[name](song))}')
        self.kinds.set_active_name(self.filter)
        self._building = False
        grouped = self.group != 'songs'
        self.kinds.set_visible(not grouped)
        self.hint.set_visible(grouped)
        self.hint.set_label(T('storagePlaylistHint', 'Deleting a playlist here deletes the playlist and the songs in it from the whole library.') if self.group == 'playlists'
                            else T('storageFolderHint', 'Deleting a folder here deletes its playlists and the songs in them from the whole library.'))
        self.columns['title'].set_title(T('colName', 'Name') if grouped else T('colTitle', 'Title'))
        self.columns['artist'].set_title({'songs': T('colArtist', 'Artist'), 'playlists': T('songsHeading', 'Songs'), 'folders': T('playlistsHeading', 'Playlists')}[self.group])
        self.columns['played'].set_title({'songs': T('lastPlayed', 'Last Played'), 'playlists': T('folderHeading', 'Folder'), 'folders': T('songsHeading', 'Songs')}[self.group])
        self.columns['size'].set_title(T('colSize', 'Size'))
        self.clean.set_visible(bool(unused))
        self.clean.set_label(f'{T("cleanStorage", "Remove Unused Files")} ({megabytes_of(unused_bytes)})')
        self._fill()

    def _clean(self, _button):
        keep = self.core.referenced_files()
        for file_id in self.core.store.list():
            if file_id not in keep:
                self.core.store.remove(file_id)
        self.refresh()

    def _delete(self, _button):
        core, lib = self.core, self.core.lib
        songs, playlists, folders = self.selection_of(self.selected_ids())
        if not songs and not playlists and not folders:
            return
        if self.group == 'songs':
            question = T('storageDeleteQuestion', 'Delete {count} songs and free {size}?')
        elif self.group == 'playlists':
            question = T('storageDeletePlaylistsQuestion', 'Delete {lists} playlists with their {count} songs and free {size}?')
        else:
            question = T('storageDeleteFoldersQuestion', 'Delete {folders} folders with their {lists} playlists and {count} songs, and free {size}?')
        question = (question.replace('{count}', str(len(songs))).replace('{lists}', str(len(playlists))).replace('{folders}', str(len(folders)))
                    .replace('{size}', megabytes_of(self.sum_bytes(songs))))

        def confirmed():
            core.delete_songs([song['id'] for song in songs])
            for item in playlists:
                if item.get('coverFileId'):
                    core.store.remove(item['coverFileId'])
            gone_lists = {item['id'] for item in playlists}
            gone_folders = {folder['id'] for folder in folders}
            lib['playlists'] = [item for item in lib['playlists'] if item['id'] not in gone_lists]
            lib['folders'] = [folder for folder in lib['folders'] if folder['id'] not in gone_folders]
            route = core.route
            if (route['kind'] == 'playlist' and route['id'] in gone_lists) or (route['kind'] == 'folder' and route['id'] in gone_folders):
                core.route = {'kind': 'songs', 'id': ''}
            core.save()
            core.render()
            self.refresh()
        self.window.confirm(question, T('storageDeleteDetail', 'They are removed from the library and from every playlist. This cannot be undone.'),
                            T('delete', 'Delete'), confirmed, parent=self)


def storage_dialog(window):
    StorageDialog(window).present(window)


# ---------------------------------------------------------------- shortcuts, about
SHORTCUTS = (
    ('menuFile', 'File', (('menuImportSongs', 'Import Songs…', '<Control>o'), ('newPlaylist', 'New Playlist', '<Control>n'),
                          ('menuNewFolder', 'New Playlist Folder', '<Control><Shift>n'), ('exportPlaylist', 'Export Playlist…', '<Control>e'))),
    ('menuControls', 'Controls', (('menuPlayPause', 'Play / Pause', 'space'), ('menuNext', 'Next', '<Alt>Right'), ('menuPrevious', 'Previous', '<Alt>Left'),
                                  ('menuShuffle', 'Shuffle', '<Control>s'), ('menuRepeat', 'Repeat', '<Control>r'), ('nowPlaying', 'Now Playing', '<Control>l'),
                                  ('upNext', 'Playing Next', '<Control>u'), ('fullscreen', 'Fullscreen', 'F11'))),
    ('general', 'General', (('search', 'Search', '<Control>f'), ('settings', 'Settings', '<Control>comma'),
                            ('keyboardShortcuts', 'Keyboard Shortcuts', '<Control>question'), ('quit', 'Quit', '<Control>q'))),
)


def shortcuts_dialog(window):
    dialog = Adw.ShortcutsDialog()
    for key, fallback, entries in SHORTCUTS:
        section = Adw.ShortcutsSection(title=T(key, fallback))
        for item_key, item_fallback, accelerator in entries:
            section.add(Adw.ShortcutsItem(title=T(item_key, item_fallback).replace('…', ''), accelerator=accelerator))
        dialog.add(section)
    dialog.present(window)


def about_dialog(window):
    Adw.AboutDialog(application_name=T('appName', 'Music'), application_icon=APP_ID, version=VERSION,
                    comments=T('appComment', 'Play and organise your own music library')).present(window)
