# Music — GNOME Shell's search finds songs and playlists; choosing one plays the song
# or opens the playlist (org.gnome.Shell.SearchProvider2).
from gi.repository import Gio, GLib

PATH = '/local/koen/Music/SearchProvider'
XML = '''<node>
  <interface name="org.gnome.Shell.SearchProvider2">
    <method name="GetInitialResultSet"><arg type="as" name="terms" direction="in"/><arg type="as" name="results" direction="out"/></method>
    <method name="GetSubsearchResultSet"><arg type="as" name="previous" direction="in"/><arg type="as" name="terms" direction="in"/><arg type="as" name="results" direction="out"/></method>
    <method name="GetResultMetas"><arg type="as" name="ids" direction="in"/><arg type="aa{sv}" name="metas" direction="out"/></method>
    <method name="ActivateResult"><arg type="s" name="id" direction="in"/><arg type="as" name="terms" direction="in"/><arg type="u" name="timestamp" direction="in"/></method>
    <method name="LaunchSearch"><arg type="as" name="terms" direction="in"/><arg type="u" name="timestamp" direction="in"/></method>
  </interface>
</node>'''
LIMIT = 12


class SearchProvider:
    def __init__(self, app, connection):
        self.app = app
        connection.register_object(PATH, Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0], self._call, None, None)

    def _find(self, terms):
        words = [term.casefold() for term in terms if term.strip()]
        if not words:
            return []
        found = []
        for key, title, subtitle, _art in self.app.core.search_items():
            text = f'{title}\n{subtitle}'.casefold()
            if all(word in text for word in words):
                # Titles that start with what was typed come first.
                found.append((0 if title.casefold().startswith(words[0]) else 1, title.casefold(), key))
        return [key for _rank, _title, key in sorted(found)[:LIMIT]]

    def _call(self, _connection, _sender, _path, _interface, method, parameters, invocation):
        app = self.app
        arguments = parameters.unpack()
        if method in ('GetInitialResultSet', 'GetSubsearchResultSet'):
            invocation.return_value(GLib.Variant('(as)', (self._find(arguments[-1]),)))
        elif method == 'GetResultMetas':
            known = {key: (title, subtitle, art) for key, title, subtitle, art in app.core.search_items()}
            metas = []
            for key in arguments[0]:
                if key in known:
                    title, subtitle, art = known[key]
                    metas.append({'id': GLib.Variant('s', key), 'name': GLib.Variant('s', title), 'description': GLib.Variant('s', subtitle),
                                  'gicon': GLib.Variant('s', Gio.File.new_for_path(art).get_uri())})
            invocation.return_value(GLib.Variant('(aa{sv})', (metas,)))
        elif method == 'ActivateResult':
            kind, _, key = arguments[0].partition(':')
            app.activate()
            app.core.open_item(kind, key)
            invocation.return_value(None)
        else:
            app.activate()
            if method == 'LaunchSearch' and app.window:
                app.core.go('songs')
                app.window.search_for(' '.join(arguments[0]))
            invocation.return_value(None)
