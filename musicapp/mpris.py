# Music — the desktop's media controls (MPRIS): what is playing, with its cover and the
# transport, in GNOME Shell's panel and on the lock screen; media keys arrive here too.
from gi.repository import Gio, GLib

from . import APP_ID
from .i18n import T

PATH = '/org/mpris/MediaPlayer2'
ROOT = 'org.mpris.MediaPlayer2'
PLAYER = 'org.mpris.MediaPlayer2.Player'
XML = f'''<node>
  <interface name="{ROOT}">
    <method name="Raise"/><method name="Quit"/>
    <property name="CanQuit" type="b" access="read"/><property name="CanRaise" type="b" access="read"/>
    <property name="HasTrackList" type="b" access="read"/><property name="Identity" type="s" access="read"/>
    <property name="DesktopEntry" type="s" access="read"/>
    <property name="SupportedUriSchemes" type="as" access="read"/><property name="SupportedMimeTypes" type="as" access="read"/>
  </interface>
  <interface name="{PLAYER}">
    <method name="Next"/><method name="Previous"/><method name="Pause"/><method name="PlayPause"/><method name="Stop"/><method name="Play"/>
    <method name="Seek"><arg name="Offset" type="x" direction="in"/></method>
    <method name="SetPosition"><arg name="TrackId" type="o" direction="in"/><arg name="Position" type="x" direction="in"/></method>
    <method name="OpenUri"><arg name="Uri" type="s" direction="in"/></method>
    <signal name="Seeked"><arg name="Position" type="x"/></signal>
    <property name="PlaybackStatus" type="s" access="read"/><property name="LoopStatus" type="s" access="readwrite"/>
    <property name="Rate" type="d" access="readwrite"/><property name="Shuffle" type="b" access="readwrite"/>
    <property name="Metadata" type="a{{sv}}" access="read"/><property name="Volume" type="d" access="readwrite"/>
    <property name="Position" type="x" access="read"/><property name="MinimumRate" type="d" access="read"/>
    <property name="MaximumRate" type="d" access="read"/><property name="CanGoNext" type="b" access="read"/>
    <property name="CanGoPrevious" type="b" access="read"/><property name="CanPlay" type="b" access="read"/>
    <property name="CanPause" type="b" access="read"/><property name="CanSeek" type="b" access="read"/>
    <property name="CanControl" type="b" access="read"/>
  </interface>
</node>'''
LOOP = {'off': 'None', 'all': 'Playlist', 'one': 'Track'}


class Mpris:
    def __init__(self, app, core):
        self.app = app
        self.core = core
        self.connection = app.get_dbus_connection()
        self._last = {}
        if not self.connection:
            return
        for interface in Gio.DBusNodeInfo.new_for_xml(XML).interfaces:
            self.connection.register_object(PATH, interface, self._call, self._get, self._set)
        Gio.bus_own_name_on_connection(self.connection, f'{ROOT}.{APP_ID}', Gio.BusNameOwnerFlags.NONE, None, None)
        core.on_render.append(self.update)
        core.on_seeked.append(lambda: self.connection.emit_signal(None, PATH, PLAYER, 'Seeked', GLib.Variant('(x)', (self._position(),))))

    def _position(self):
        return int(self.core.player['time'] * 1_000_000)

    def _metadata(self):
        song = self.core.current_song()
        if not song:
            return {'mpris:trackid': GLib.Variant('o', '/org/mpris/MediaPlayer2/TrackList/NoTrack')}
        data = {
            'mpris:trackid': GLib.Variant('o', '/local/koen/Music/track/' + ''.join(char if char.isalnum() else '_' for char in song['id'])),
            'xesam:title': GLib.Variant('s', song['title']),
            'mpris:artUrl': GLib.Variant('s', Gio.File.new_for_path(self.core.art_of(song)).get_uri()),
        }
        if self.core.player['duration']:
            data['mpris:length'] = GLib.Variant('x', int(self.core.player['duration'] * 1_000_000))
        if song.get('artist'):
            data['xesam:artist'] = GLib.Variant('as', [song['artist']])
        if song.get('album'):
            data['xesam:album'] = GLib.Variant('s', song['album'])
        return data

    def _properties(self):
        core, song = self.core, self.core.current_song()
        return {
            'PlaybackStatus': GLib.Variant('s', 'Stopped' if not song else 'Paused' if core.player['paused'] else 'Playing'),
            'LoopStatus': GLib.Variant('s', LOOP.get(core.lib['repeat'], 'None')),
            'Shuffle': GLib.Variant('b', bool(core.lib['shuffle'])),
            'Metadata': GLib.Variant('a{sv}', self._metadata()),
            'Volume': GLib.Variant('d', float(core.lib['volume'])),
            'CanGoNext': GLib.Variant('b', bool(song)), 'CanGoPrevious': GLib.Variant('b', bool(song)),
            'CanPlay': GLib.Variant('b', bool(song) or bool(core.lib['songs'])), 'CanPause': GLib.Variant('b', bool(song)),
            'CanSeek': GLib.Variant('b', bool(song)),
        }

    def update(self):
        current = self._properties()
        changed = {name: value for name, value in current.items() if self._last.get(name) != value}
        self._last = current
        if changed:
            self.connection.emit_signal(None, PATH, 'org.freedesktop.DBus.Properties', 'PropertiesChanged', GLib.Variant('(sa{sv}as)', (PLAYER, changed, [])))

    def _get(self, _connection, _sender, _path, interface, name):
        if interface == ROOT:
            return {
                'CanQuit': GLib.Variant('b', True), 'CanRaise': GLib.Variant('b', True), 'HasTrackList': GLib.Variant('b', False),
                'Identity': GLib.Variant('s', T('appName', 'Music')), 'DesktopEntry': GLib.Variant('s', APP_ID),
                'SupportedUriSchemes': GLib.Variant('as', ['file', 'music']), 'SupportedMimeTypes': GLib.Variant('as', []),
            }.get(name)
        fixed = {'Rate': 1.0, 'MinimumRate': 1.0, 'MaximumRate': 1.0}
        if name in fixed:
            return GLib.Variant('d', fixed[name])
        if name == 'Position':
            return GLib.Variant('x', self._position())
        if name == 'CanControl':
            return GLib.Variant('b', True)
        return self._properties().get(name)

    def _set(self, _connection, _sender, _path, _interface, name, value):
        core = self.core
        if name == 'Volume':
            core.set_volume(value.get_double())
            core.render()
        elif name == 'Shuffle':
            core.set_shuffle(value.get_boolean())
        elif name == 'LoopStatus':
            core.lib['repeat'] = {status: mode for mode, status in LOOP.items()}.get(value.get_string(), 'off')
            core.save()
            core.render()
        return True

    def _call(self, _connection, _sender, _path, interface, method, parameters, invocation):
        core = self.core
        if interface == ROOT:
            if method == 'Raise':
                self.app.activate()
            elif method == 'Quit':
                self.app.quit()
        elif method == 'Next':
            core.next()
        elif method == 'Previous':
            core.previous()
        elif method == 'PlayPause':
            core.toggle()
        elif method == 'Play':
            core.play()
        elif method in ('Pause', 'Stop'):
            core.pause()
        elif method == 'Seek':
            core.seek(max(0.0, core.player['time'] + parameters.unpack()[0] / 1_000_000))
        elif method == 'SetPosition':
            core.seek(max(0.0, parameters.unpack()[1] / 1_000_000))
        elif method == 'OpenUri':
            self.app.open_uris([parameters.unpack()[0]])
        invocation.return_value(None)
