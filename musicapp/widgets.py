# Music — the parts several views share: buttons, menus, song lists, the timeline.
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Graphene', '1.0')
from gi.repository import Gdk, Gio, GLib, GObject, Graphene, Gtk, Pango  # noqa: E402

from .art import Art  # noqa: E402
from .core import clock  # noqa: E402
from .i18n import T  # noqa: E402

DRAG_PREFIX = 'x-music/'


def icon_button(icon, pixels=16, tooltip=None, box=None, classes=('flat', 'circular')):
    image = Gtk.Image.new_from_icon_name(icon)
    image.set_pixel_size(pixels)
    button = Gtk.Button(child=image, valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
    button.image = image
    for name in classes:
        button.add_css_class(name)
    if tooltip:
        button.set_tooltip_text(tooltip)
    if box:
        button.set_size_request(box, box)
    return button


def set_class(widget, name, on):
    if on:
        widget.add_css_class(name)
    else:
        widget.remove_css_class(name)


def point_in(widget, target, x, y):
    """A point of `widget`, in `target`'s coordinates."""
    ok, point = widget.compute_point(target, Graphene.Point().init(x, y))
    return (point.x, point.y) if ok else (0, 0)


def force_ltr(widget):
    """Playback controls and the timeline keep their order (and their arrows) in a right-to-left layout."""
    widget.set_direction(Gtk.TextDirection.LTR)
    child = widget.get_first_child()
    while child is not None:
        force_ltr(child)
        child = child.get_next_sibling()


class Shrinkable(Gtk.Widget):
    """Holds one child and lets itself be squeezed below what the child needs — the child
    keeps its width and is clipped. For panels that slide in and out."""

    def __init__(self, child):
        super().__init__()
        self.child = child
        child.set_parent(self)
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.connect('destroy', lambda *_: self.child.unparent())

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.CONSTANT_SIZE

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            return 0, self.child.measure(orientation, -1)[1], -1, -1
        minimum, natural, _, _ = self.child.measure(orientation, -1)
        return minimum, natural, -1, -1

    def do_size_allocate(self, width, height, baseline):
        self.child.allocate(max(width, self.child.measure(Gtk.Orientation.HORIZONTAL, -1)[0]), height, baseline, None)


def drag_payload(kind, ids):
    return DRAG_PREFIX + kind + '\n' + '\n'.join(ids)


def parse_payload(text):
    """(kind, ids) of something dragged inside the app, or (None, [])."""
    if not isinstance(text, str) or not text.startswith(DRAG_PREFIX):
        return None, []
    kind, *ids = text[len(DRAG_PREFIX):].split('\n')
    return kind, [item for item in ids if item]


def _menu_model(items, group, counter):
    menu = Gio.Menu()
    section = Gio.Menu()
    menu.append_section(None, section)
    for item in items:
        if item is None:
            section = Gio.Menu()
            menu.append_section(None, section)
            continue
        label = item[0].replace('_', '__')
        if isinstance(item[1], list):
            section.append_submenu(label, _menu_model(item[1], group, counter))
            continue
        options = item[2] if len(item) > 2 else {}
        counter[0] += 1
        name = f'item{counter[0]}'
        if 'checked' in options:
            action = Gio.SimpleAction.new_stateful(name, None, GLib.Variant.new_boolean(bool(options['checked'])))
        else:
            action = Gio.SimpleAction.new(name, None)
        action.set_enabled(not options.get('disabled'))
        action.connect('activate', lambda _action, _parameter, callback=item[1]: callback())
        group.add_action(action)
        section.append(label, f'menu.{name}')
    return menu


def popup_menu(parent, items, x=None, y=None):
    """Show `items` — (label, callback[, {'checked', 'disabled'}]), (label, [submenu]) or
    None for a separator — under `parent`, or at a point inside it."""
    if not items:
        return None
    group = Gio.SimpleActionGroup()
    popover = Gtk.PopoverMenu.new_from_model(_menu_model(items, group, [0]))
    popover.insert_action_group('menu', group)
    popover.set_parent(parent)
    if x is not None:
        area = Gdk.Rectangle()
        area.x, area.y, area.width, area.height = int(x), int(y), 1, 1
        popover.set_pointing_to(area)
        popover.set_has_arrow(False)
        popover.set_halign(Gtk.Align.START)
        # Near the bottom of the window the menu opens upwards, whole, instead of being squeezed.
        root = parent.get_root()
        height = 20 + sum(13 if item is None else 34 for item in items)
        if root is not None:
            in_window = point_in(parent, root, x, y)[1]
            if in_window + height > root.get_height() and in_window > height:
                popover.set_position(Gtk.PositionType.TOP)
    # The chosen item acts after the menu has closed: only then may the menu go.
    popover.connect('closed', lambda menu: GLib.timeout_add(600, lambda: menu.unparent() or False))
    popover.popup()
    return popover


class RowItem(GObject.Object):
    __gsignals__ = {'changed': (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, data):
        super().__init__()
        self.data = data


class RowList(Gtk.Stack):
    """A list of rows — picture, title, subtitle, detail — that only builds what is on
    screen. Rows are dicts: id, art, title, subtitle, detail, loved, emphasis."""

    def __init__(self, icon_size, row_height, multiple=False, single_click=False, on_activate=None, on_menu=None,
                 drag_kind=None, on_reorder=None, on_delete=None, inset_bottom=0):
        super().__init__(vexpand=True, hexpand=True)
        self.icon_size = icon_size
        self.row_height = row_height
        self.on_activate = on_activate
        self.on_menu = on_menu
        self.on_reorder = on_reorder
        self.drag_kind = drag_kind
        self.reorderable = False
        self.items = []
        self.ids = []
        self.model = Gio.ListStore(item_type=RowItem)
        if single_click:
            self.selection = Gtk.NoSelection(model=self.model)
        elif multiple:
            self.selection = Gtk.MultiSelection(model=self.model)
        else:
            self.selection = Gtk.SingleSelection(model=self.model, autoselect=False, can_unselect=True)
        factory = Gtk.SignalListItemFactory()
        factory.connect('setup', self._setup)
        factory.connect('bind', self._bind)
        factory.connect('unbind', self._unbind)
        self.view = Gtk.ListView(model=self.selection, factory=factory, single_click_activate=single_click)
        self.view.add_css_class('rows')
        self.view.connect('activate', lambda _view, position: self.on_activate and self.on_activate(self.ids[position]))
        if on_delete:
            keys = Gtk.EventControllerKey()
            keys.connect('key-pressed', lambda _c, key, _code, state: key in (Gdk.KEY_Delete, Gdk.KEY_BackSpace, Gdk.KEY_KP_Delete)
                         and not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK) and (on_delete(self.selected_ids()) or True))
            self.view.add_controller(keys)
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.view)
        if inset_bottom:
            # Room for the player that floats over the end of the list.
            self.view.set_margin_bottom(inset_bottom)
        self.add_named(self.scroller, 'rows')
        self.placeholder = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER,
                                     margin_start=32, margin_end=32, margin_bottom=inset_bottom, max_width_chars=36)
        self.placeholder.add_css_class('dim-label')
        self.add_named(self.placeholder, 'empty')

    # -- rows
    def _setup(self, _factory, list_item):
        row = Gtk.Box(spacing=10)
        row.add_css_class('song-row')
        row.set_size_request(-1, self.row_height)
        row.art = Art(self.icon_size, radius=max(4, self.icon_size // 8))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True, spacing=1)
        row.title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        row.title.add_css_class('row-title')
        row.subtitle = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        row.subtitle.add_css_class('row-subtitle')
        text.append(row.title)
        text.append(row.subtitle)
        row.star = Gtk.Image(icon_name='starred-symbolic', pixel_size=12, visible=False)
        row.star.add_css_class('accent')
        row.detail = Gtk.Label(xalign=1)
        row.detail.add_css_class('row-detail')
        for child in (row.art, text, row.star, row.detail):
            row.append(child)
        list_item.set_child(row)

        if self.on_menu:
            click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)

            def pressed(gesture, _count, x, y):
                position = list_item.get_position()
                if position >= len(self.ids):
                    return
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                if not isinstance(self.selection, Gtk.NoSelection) and not self.selection.is_selected(position):
                    self.selection.select_item(position, True)
                px, py = point_in(row, self, x, y)
                self.on_menu(self, px, py, self.ids[position], self.selected_ids())
            click.connect('pressed', pressed)
            row.add_controller(click)

        if self.drag_kind:
            source = Gtk.DragSource(actions=Gdk.DragAction.COPY | Gdk.DragAction.MOVE)

            def prepare(_source, _x, _y):
                position = list_item.get_position()
                if position >= len(self.ids):
                    return None
                chosen = self.selected_ids()
                ids = chosen if self.ids[position] in chosen else [self.ids[position]]
                return Gdk.ContentProvider.new_for_value(drag_payload(self.drag_kind, ids))
            source.connect('prepare', prepare)
            source.connect('drag-begin', lambda drag_source, _drag: drag_source.set_icon(Gtk.WidgetPaintable.new(row), 24, 12))
            row.add_controller(source)

        if self.on_reorder:
            target = Gtk.DropTarget.new(str, Gdk.DragAction.MOVE)

            def mark(after=None):
                set_class(row, 'drop-before', after is False)
                set_class(row, 'drop-after', after is True)

            def motion(_target, _x, y):
                if not self.reorderable:
                    return 0
                mark(y > row.get_height() / 2)
                return Gdk.DragAction.MOVE

            def drop(_target, value, _x, y):
                mark()
                kind, dragged = parse_payload(value)
                position = list_item.get_position()
                if not self.reorderable or kind != self.drag_kind or position >= len(self.ids):
                    return False
                moving = set(dragged)
                held = self.ids[position] in moving
                index = sum(1 for other in self.ids[:position] if other not in moving) + (1 if y > row.get_height() / 2 and not held else 0)
                rest = [other for other in self.ids if other not in moving]
                self.on_reorder([*rest[:index], *[other for other in self.ids if other in moving], *rest[index:]])
                return True
            target.connect('motion', motion)
            target.connect('leave', lambda _target: mark())
            target.connect('drop', drop)
            row.add_controller(target)

    def _fill(self, row, item):
        data = item.data
        row.art.set_path(data.get('art'))
        row.title.set_label(data.get('title') or '')
        row.subtitle.set_label(data.get('subtitle') or '')
        row.subtitle.set_visible(bool(data.get('subtitle')))
        row.detail.set_label(data.get('detail') or '')
        row.star.set_visible(bool(data.get('loved')))
        set_class(row, 'playing', bool(data.get('emphasis')))

    def _bind(self, _factory, list_item):
        row, item = list_item.get_child(), list_item.get_item()
        row.handler = item.connect('changed', lambda changed: self._fill(row, changed))
        row.bound = item
        self._fill(row, item)

    def _unbind(self, _factory, list_item):
        row = list_item.get_child()
        if getattr(row, 'bound', None):
            row.bound.disconnect(row.handler)
            row.bound = None

    # -- content
    def selected_ids(self):
        if isinstance(self.selection, Gtk.NoSelection):
            return []
        chosen = self.selection.get_selection()
        return [self.ids[chosen.get_nth(index)] for index in range(chosen.get_size()) if chosen.get_nth(index) < len(self.ids)]

    def select_ids(self, ids):
        wanted = set(ids)
        if isinstance(self.selection, Gtk.MultiSelection):
            self.selection.unselect_all()
            for position, row_id in enumerate(self.ids):
                if row_id in wanted:
                    self.selection.select_item(position, False)

    def set_rows(self, rows, reorderable=False, placeholder=''):
        self.reorderable = reorderable
        self.placeholder.set_label(placeholder)
        ids = [data['id'] for data in rows]
        if ids == self.ids:
            for item, data in zip(self.items, rows):
                if item.data != data:
                    item.data = data
                    item.emit('changed')
        else:
            known = {item.data['id']: item for item in self.items}
            chosen = set(self.selected_ids()) if isinstance(self.selection, Gtk.MultiSelection) else set()
            items = []
            for data in rows:
                item = known.pop(data['id'], None)
                if item is None:
                    item = RowItem(data)
                elif item.data != data:
                    item.data = data
                    item.emit('changed')
                items.append(item)
            old = self.items
            # Only the stretch that differs is replaced, so the list keeps its place.
            head = 0
            while head < min(len(old), len(items)) and old[head] is items[head]:
                head += 1
            tail = 0
            while tail < min(len(old), len(items)) - head and old[-1 - tail] is items[-1 - tail]:
                tail += 1
            self.model.splice(head, len(old) - head - tail, items[head:len(items) - tail])
            self.items, self.ids = items, ids
            for position in range(head, len(items) - tail):
                if ids[position] in chosen:
                    self.selection.select_item(position, False)
        self.set_visible_child_name('rows' if rows else 'empty')


class SeekRow(Gtk.Box):
    """0:42 ———●——— 3:10. The clock follows the thumb; the song follows a moment later."""

    def __init__(self, core):
        super().__init__(spacing=8)
        self.core = core
        self.time = Gtk.Label(label='0:00', xalign=1, width_chars=4)
        self.duration = Gtk.Label(label='0:00', xalign=0, width_chars=4)
        for label in (self.time, self.duration):
            label.add_css_class('clock')
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 1)
        self.scale.set_hexpand(True)
        self.scale.set_draw_value(False)
        self.scale.add_css_class('seek')
        self.scale.connect('change-value', self._changed)
        self._pending = 0
        self._hold = 0
        self.append(self.time)
        self.append(self.scale)
        self.append(self.duration)
        force_ltr(self)

    def _changed(self, _scale, _scroll, value):
        value = max(0.0, min(value, self.scale.get_adjustment().get_upper()))
        self.time.set_label(clock(value))
        self._hold = GLib.get_monotonic_time() + 700_000
        if self._pending:
            GLib.source_remove(self._pending)

        def commit():
            self._pending = 0
            self.core.seek(value)
            return False
        self._pending = GLib.timeout_add(120, commit)
        return False

    def tick(self):
        player = self.core.player
        self.scale.set_sensitive(bool(self.core.current_song()))
        self.duration.set_label(clock(player['duration']))
        if self._pending or GLib.get_monotonic_time() < self._hold:
            return
        adjustment = self.scale.get_adjustment()
        upper = max(1.0, float(player['duration'] or 0))
        if abs(adjustment.get_upper() - upper) > 0.01:
            adjustment.set_upper(upper)
        adjustment.set_value(min(upper, player['time']))
        self.time.set_label(clock(player['time']))


class Transport:
    """Shuffle, previous, play/pause, next, repeat — the same five buttons wherever they appear."""

    def __init__(self, core, small, medium, large, need_song=False):
        self.core = core
        self.need_song = need_song
        self.shuffle = icon_button('media-playlist-shuffle-symbolic', small[0], T('shuffle', 'Shuffle'), small[1])
        self.previous = icon_button('media-skip-backward-symbolic', medium[0], T('menuPrevious', 'Previous'), medium[1])
        self.toggle = icon_button('media-playback-start-symbolic', large[0], None, large[1])
        self.next = icon_button('media-skip-forward-symbolic', medium[0], T('menuNext', 'Next'), medium[1])
        self.repeat = icon_button('media-playlist-repeat-symbolic', small[0], T('repeat', 'Repeat'), small[1])
        self.shuffle.connect('clicked', lambda _b: core.set_shuffle(not core.lib['shuffle']))
        self.previous.connect('clicked', lambda _b: core.previous())
        self.toggle.connect('clicked', lambda _b: core.toggle())
        self.next.connect('clicked', lambda _b: core.next())
        self.repeat.connect('clicked', lambda _b: core.cycle_repeat())
        self.buttons = (self.shuffle, self.previous, self.toggle, self.next, self.repeat)

    def update(self):
        core = self.core
        playing = core.current_song()
        repeat = core.lib['repeat']
        self.toggle.image.set_from_icon_name('media-playback-start-symbolic' if core.player['paused'] or not playing else 'media-playback-pause-symbolic')
        self.toggle.set_tooltip_text(T('play', 'Play') if core.player['paused'] or not playing else T('pause', 'Pause'))
        self.repeat.image.set_from_icon_name('media-playlist-repeat-song-symbolic' if repeat == 'one' else 'media-playlist-repeat-symbolic')
        for button, on in ((self.shuffle, core.lib['shuffle']), (self.repeat, repeat != 'off')):
            set_class(button, 'accent', bool(on))
            set_class(button, 'dim', not on)
        self.previous.set_sensitive(bool(playing))
        self.next.set_sensitive(bool(playing))
        self.toggle.set_sensitive(bool(playing) or (not self.need_song and bool(core.lib['songs'])))
