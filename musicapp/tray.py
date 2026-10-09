# Music — the top-bar item (StatusNotifierItem, shown by Ubuntu's AppIndicator support):
# what is playing and the transport controls in its menu. Middle-click plays / pauses.
import os

from gi.repository import Gio, GLib

from . import APP_ID
from .i18n import T

ITEM_PATH = '/StatusNotifierItem'
MENU_PATH = '/StatusNotifierItem/Menu'
ITEM = 'org.kde.StatusNotifierItem'
MENU = 'com.canonical.dbusmenu'
TOPBAR_PATH = '/local/koen/Music/TopBar'
TOPBAR = 'local.koen.Music.TopBar'
PANEL = 'local.koen.Music.ShellPanel'   # owned by the GNOME Shell extension while it is enabled
WATCHER = 'org.kde.StatusNotifierWatcher'
XML = f'''<node>
  <interface name="{ITEM}">
    <property name="Category" type="s" access="read"/><property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/><property name="Status" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/><property name="IconThemePath" type="s" access="read"/>
    <property name="Menu" type="o" access="read"/><property name="ItemIsMenu" type="b" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
    <signal name="NewTitle"/><signal name="NewIcon"/><signal name="NewToolTip"/><signal name="NewStatus"><arg type="s"/></signal>
  </interface>
  <interface name="{TOPBAR}"><property name="Enabled" type="b" access="read"/></interface>
  <interface name="{MENU}">
    <property name="Version" type="u" access="read"/><property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/><property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout"><arg type="i" direction="in"/><arg type="i" direction="in"/><arg type="as" direction="in"/>
      <arg type="u" direction="out"/><arg type="(ia{{sv}}av)" direction="out"/></method>
    <method name="GetGroupProperties"><arg type="ai" direction="in"/><arg type="as" direction="in"/><arg type="a(ia{{sv}})" direction="out"/></method>
    <method name="GetProperty"><arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/></method>
    <method name="Event"><arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="in"/><arg type="u" direction="in"/></method>
    <method name="EventGroup"><arg type="a(isvu)" direction="in"/><arg type="ai" direction="out"/></method>
    <method name="AboutToShow"><arg type="i" direction="in"/><arg type="b" direction="out"/></method>
    <method name="AboutToShowGroup"><arg type="ai" direction="in"/><arg type="ai" direction="out"/><arg type="ai" direction="out"/></method>
    <signal name="ItemsPropertiesUpdated"><arg type="a(ia{{sv}})"/><arg type="a(ias)"/></signal>
    <signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
    <signal name="ItemActivationRequested"><arg type="i"/><arg type="u"/></signal>
  </interface>
</node>'''


class Tray:
    def __init__(self, app, core, icon_dir):
        self.app = app
        self.core = core
        self.icon_dir = icon_dir
        self.connection = app.get_dbus_connection()
        self.revision = 1
        self.panel = False
        self._signature = None
        if not self.connection:
            return
        item, topbar, menu = Gio.DBusNodeInfo.new_for_xml(XML).interfaces
        self.connection.register_object(TOPBAR_PATH, topbar, None, lambda *_: GLib.Variant('b', self.enabled()), None)
        # The shell extension draws the full panel (cover, controls, timeline); this plain item then steps aside.
        Gio.bus_watch_name_on_connection(self.connection, PANEL, Gio.BusNameWatcherFlags.NONE, lambda *_: self._panel(True), lambda *_: self._panel(False))
        self.connection.register_object(ITEM_PATH, item, self._item_call, self._item_get, None)
        self.connection.register_object(MENU_PATH, menu, self._menu_call, self._menu_get, None)
        # Whenever something that shows such items appears (now, or after a shell restart), join it.
        Gio.bus_watch_name_on_connection(self.connection, WATCHER, Gio.BusNameWatcherFlags.NONE, lambda *_: self._register(), None)
        core.on_render.append(self.update)

    def enabled(self):
        return self.core.lib.get('topBar', True) is not False

    def _status(self):
        return 'Active' if self.enabled() and not self.panel else 'Passive'

    def _panel(self, present):
        self.panel = present
        self.changed()

    def changed(self):
        """The setting, or who draws the item, changed."""
        self.connection.emit_signal(None, ITEM_PATH, ITEM, 'NewStatus', GLib.Variant('(s)', (self._status(),)))
        self.connection.emit_signal(None, TOPBAR_PATH, 'org.freedesktop.DBus.Properties', 'PropertiesChanged',
                                    GLib.Variant('(sa{sv}as)', (TOPBAR, {'Enabled': GLib.Variant('b', self.enabled())}, [])))

    def _register(self):
        self.connection.call(WATCHER, '/StatusNotifierWatcher', WATCHER, 'RegisterStatusNotifierItem',
                             GLib.Variant('(s)', (self.connection.get_unique_name(),)), None, Gio.DBusCallFlags.NONE, -1, None, None)

    # -- what the menu says
    def _line(self):
        song = self.core.current_song()
        if not song:
            return T('notPlaying', 'Not Playing')
        return song['title'] + (f' — {song["artist"]}' if song.get('artist') else '')

    def _items(self):
        core = self.core
        song = core.current_song()
        playing = bool(song) and not core.player['paused']
        return [
            (1, {'label': self._line(), 'enabled': False}, None),
            (2, {'type': 'separator'}, None),
            (3, {'label': T('pause', 'Pause') if playing else T('play', 'Play')}, core.toggle),
            (4, {'label': T('menuNext', 'Next'), 'enabled': bool(song)}, core.next),
            (5, {'label': T('menuPrevious', 'Previous'), 'enabled': bool(song)}, core.previous),
            (6, {'type': 'separator'}, None),
            (7, {'label': T('showMusic', 'Show Music')}, self.app.activate),
            (8, {'label': T('quit', 'Quit')}, self.app.quit),
        ]

    @staticmethod
    def _properties(entry):
        packed = {}
        for name, value in entry.items():
            packed[name] = GLib.Variant('b', value) if isinstance(value, bool) else GLib.Variant('s', value.replace('_', '__') if name == 'label' else value)
        return packed

    def update(self):
        signature = [(number, tuple(sorted(entry.items()))) for number, entry, _action in self._items()]
        if signature == self._signature:
            return
        self._signature = signature
        self.revision += 1
        self.connection.emit_signal(None, MENU_PATH, MENU, 'LayoutUpdated', GLib.Variant('(ui)', (self.revision, 0)))
        self.connection.emit_signal(None, ITEM_PATH, ITEM, 'NewToolTip', None)
        self.connection.emit_signal(None, ITEM_PATH, ITEM, 'NewTitle', None)

    # -- the item
    def _item_get(self, _connection, _sender, _path, _interface, name):
        return {
            'Category': GLib.Variant('s', 'ApplicationStatus'), 'Id': GLib.Variant('s', APP_ID), 'Title': GLib.Variant('s', T('appName', 'Music')),
            'Status': GLib.Variant('s', self._status()), 'IconName': GLib.Variant('s', f'{APP_ID}-symbolic'),
            'IconThemePath': GLib.Variant('s', os.path.join(self.icon_dir, 'hicolor', 'symbolic', 'apps')),
            'Menu': GLib.Variant('o', MENU_PATH), 'ItemIsMenu': GLib.Variant('b', False),
            'ToolTip': GLib.Variant('(sa(iiay)ss)', ('', [], T('appName', 'Music'), self._line())),
        }.get(name)

    def _item_call(self, _connection, _sender, _path, _interface, method, _parameters, invocation):
        if method == 'Activate':
            self.app.activate()
        elif method == 'SecondaryActivate':
            self.core.toggle()
        invocation.return_value(None)

    # -- its menu
    def _menu_get(self, _connection, _sender, _path, _interface, name):
        return {'Version': GLib.Variant('u', 3), 'TextDirection': GLib.Variant('s', 'ltr'), 'Status': GLib.Variant('s', 'normal'),
                'IconThemePath': GLib.Variant('as', [])}.get(name)

    def _menu_call(self, _connection, _sender, _path, _interface, method, parameters, invocation):
        items = self._items()
        if method == 'GetLayout':
            children = [GLib.Variant('(ia{sv}av)', (number, self._properties(entry), [])) for number, entry, _action in items]
            root = (0, {'children-display': GLib.Variant('s', 'submenu')}, children if parameters.unpack()[0] == 0 else [])
            invocation.return_value(GLib.Variant('(u(ia{sv}av))', (self.revision, root)))
        elif method == 'GetGroupProperties':
            wanted = set(parameters.unpack()[0])
            invocation.return_value(GLib.Variant('(a(ia{sv}))', ([(number, self._properties(entry)) for number, entry, _action in items if not wanted or number in wanted],)))
        elif method == 'GetProperty':
            number, name = parameters.unpack()
            entry = next((entry for item, entry, _action in items if item == number), {})
            value = entry.get(name, '')
            invocation.return_value(GLib.Variant('(v)', (GLib.Variant('b', value) if isinstance(value, bool) else GLib.Variant('s', value),)))
        elif method in ('Event', 'EventGroup'):
            events = [parameters.unpack()[:2]] if method == 'Event' else [event[:2] for event in parameters.unpack()[0]]
            for number, kind in events:
                action = next((action for item, _entry, action in items if item == number), None)
                if kind == 'clicked' and action:
                    GLib.idle_add(lambda action=action: action() and False)
            invocation.return_value(None if method == 'Event' else GLib.Variant('(ai)', ([],)))
        elif method == 'AboutToShow':
            invocation.return_value(GLib.Variant('(b)', (False,)))
        elif method == 'AboutToShowGroup':
            invocation.return_value(GLib.Variant('(aiai)', ([], [])))
        else:
            invocation.return_value(None)
