# Music — the application without its window: state, the library, playback.
#
# The window draws what is here and calls back into it; `render()` asks for a redraw of
# whatever changed, `tick()` only moves the clock and the timeline.
import functools
import json
import locale
import os
import random
import re
import threading

from gi.repository import GLib

from . import importer, subtitles
from .audio import Audio
from .i18n import T
from .store import FileStore, LibraryFile, chosen_data_dir, data_dir, extension_of, now_ms, set_data_dir, uid

ACCENTS = {'red': '#ff5257', 'pink': '#f74f9e', 'orange': '#f7821b', 'yellow': '#f5b60a', 'green': '#62ba46', 'blue': '#0a84ff', 'purple': '#a550a7', 'graphite': '#8c8c8c'}
DEFAULT_ACCENT = 'red'
FADE_SECONDS = 0.45
STALE_MS = 90 * 86_400_000
ALBUM_SEP = ' ⧸ '
_NUMBER = re.compile(r'([0-9]+)')


@functools.lru_cache(maxsize=50000)
def natkey(text):
    """Sort key: case-insensitive, in the locale's order, numbers by value."""
    return tuple((0, int(part), '') if part[0] in '0123456789' else (1, 0, locale.strxfrm(part))
                 for part in _NUMBER.split(str(text or '').casefold()) if part)


def clock(seconds):
    total = max(0, round(float(seconds or 0)))
    return f'{total // 60}:{total % 60:02d}'


def long_clock(seconds):
    minutes = max(1, round(seconds / 60))
    if minutes >= 60:
        return f'{minutes // 60} {T("hours", "hr")} {minutes % 60} {T("minutes", "min")}'
    return f'{minutes} {T("minutes", "min")}'


def megabytes_of(size):
    if size >= 1_048_576_000:
        return f'{size / 1_073_741_824:.2f} GB'
    if size >= 1_048_576:
        return f'{size / 1_048_576:.{0 if size > 104_857_600 else 1}f} MB'
    return f'{max(1 if size else 0, round(size / 1024))} KB'


def safe_name(text):
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', str(text or 'untitled')).strip()[:80] or 'untitled'


def new_player():
    return {'original': [], 'queue': [], 'index': -1, 'paused': True, 'time': 0.0, 'duration': 0.0, 'crossing': False}


class Core:
    def __init__(self, resource_dir):
        try:
            locale.setlocale(locale.LC_COLLATE, '')
        except locale.Error:
            pass
        self.resource_dir = resource_dir
        root = self.root = data_dir()
        chosen = '' if os.environ.get('MUSIC_DATA_DIR') else chosen_data_dir()
        self.library_missing = chosen if chosen and chosen != root else ''   # chosen, but not there
        self.store = FileStore(root)
        self.file = LibraryFile(root)
        self.lib = self.file.data
        self.route = {'kind': 'songs', 'id': ''}   # songs | recent | favorites | albums | artists | playlists | folder | album | artist | playlist
        self.query = ''
        self.sorts = {}                            # route key -> {'field', 'descending'}
        self.player = new_player()
        self.show_queue = False
        self.show_now = False                      # the full-window Now Playing view
        self.now_tab = 'queue'                     # queue | history
        self.importing = None                      # {'done', 'total', 'exporting'} while files are being stored
        self.busy = ''
        self.tracks = subtitles.Tracks()
        self._file_tracks = []
        self._embedded = {}                        # file id -> the subtitle tracks read out of that video
        self.ui = None
        self.on_render = []
        self.on_tick = []
        self.on_seeked = []
        self._render_queued = False
        self.worker = importer.Worker()
        self.audio = Audio()
        self.audio.set_volume(self.lib['volume'])
        self.audio.connect('event', self._on_audio)
        self._cover_videos()
        self._restore_route()

    def _cover_videos(self):
        """Videos that came in without a cover get a frame of themselves — quietly, one by one."""
        pending = [song['id'] for song in self.all_songs() if song.get('video') and not song.get('artId')]

        def following():
            while pending:
                song = self.song(pending.pop())
                if song and not song.get('artId') and self.store.exists(song.get('fileId')):
                    path, duration = self.store.path(song['fileId']), song.get('duration') or 0
                    return self.worker.submit(lambda: importer.video_frame(path, duration), lambda frame, _error: found(song, frame))

        def found(song, frame):
            if frame and self.song(song['id']) and not song.get('artId'):
                song['artId'] = self.store.add_art(frame, '.jpg')
                self.save()
                self.render()
            following()
        following()

    # ------------------------------------------------------------ plumbing
    def save(self):
        self.file.save()

    def render(self):
        if self._render_queued:
            return
        self._render_queued = True
        GLib.idle_add(self._render_now, priority=GLib.PRIORITY_HIGH_IDLE)

    def _render_now(self):
        self._render_queued = False
        for callback in self.on_render:
            callback()
        return False

    def tick(self):
        for callback in self.on_tick:
            callback()

    def status(self, text):
        self.busy = text
        self.render()

    def _restore_route(self):
        """Start where the last session was: the same playlist, folder, album…"""
        last = self.lib.get('lastRoute')
        if not isinstance(last, dict):
            return
        kind, target = str(last.get('kind') or ''), str(last.get('id') or '')
        known = (kind in ('songs', 'recent', 'favorites', 'albums', 'artists', 'playlists')
                 or (kind == 'playlist' and self.playlist_of(target)) or (kind == 'folder' and self.folder_of(target))
                 or (kind == 'album' and any(album['id'] == target for album in self.albums()))
                 or (kind == 'artist' and any(artist['id'] == target for artist in self.artists())))
        if known:
            self.route = {'kind': kind, 'id': target if kind in ('playlist', 'folder', 'album', 'artist') else ''}

    def remember_route(self):
        if self.lib.get('lastRoute') != self.route:
            self.lib['lastRoute'] = dict(self.route)
            self.save()

    def go(self, kind, target=''):
        self.route = {'kind': kind, 'id': target}
        self.remember_route()
        self.query = ''
        self.render()

    def alert(self, title, message):
        if self.ui:
            self.ui.alert(title, message)
        else:
            print(f'{title}: {message}')

    # ------------------------------------------------------------ where the library lives
    def plan_library_move(self, chosen):
        """What choosing `chosen` means: ('same' | 'inside' | 'exists' | 'move', the library folder it stands for)."""
        def has_library(folder):
            return os.path.isfile(os.path.join(folder, 'library.json'))
        try:
            empty = not os.listdir(chosen)
        except OSError:
            empty = False
        # An empty folder becomes the library; in a folder with other things it gets a folder of its own.
        target = chosen if empty or has_library(chosen) else os.path.join(chosen, T('libraryFolderName', 'Music Library'))
        old, new = os.path.realpath(self.root), os.path.realpath(target)
        if new == old:
            return 'same', target
        if new.startswith(old + os.sep) or old.startswith(new + os.sep):
            return 'inside', target
        return ('exists' if has_library(target) else 'move'), target

    def _use_library(self, root):
        self.root = root
        self.library_missing = ''
        self.store.move_to(root)
        self.file.move_to(root)
        set_data_dir(root)
        self._embedded = {}

    def open_library(self, root):
        """Another library that is already there: this one is left as it is."""
        self.stop()
        self.file.flush()
        self._use_library(root)
        fresh = LibraryFile(root)
        self.file.data = self.lib = fresh.data
        self.audio.set_volume(self.lib['volume'])
        self.route = {'kind': 'songs', 'id': ''}
        self._restore_route()
        self._cover_videos()
        self.render()

    def move_library(self, target, progress, finished):
        """This library to `target`; the old place is emptied once everything has arrived."""
        self.stop()
        self.file.flush()
        old = self.root
        try:
            os.makedirs(target, exist_ok=True)
        except OSError as error:
            return finished(error)
        self.file.frozen = True

        def done(_result, error):
            self.file.frozen = False
            if not error:
                self._use_library(target)
            self.file.save()
            self.render()
            finished(error)
        self.worker.submit(lambda: importer.move_library(old, target, lambda index, total: GLib.idle_add(lambda: progress(index, total) and False),
                                                         T('notEnoughSpace', 'There is not enough free space there.')), done)

    # ------------------------------------------------------------ selectors
    def route_key(self):
        return f'{self.route["kind"]}:{self.route["id"]}'

    def playlist_of(self, playlist_id=None):
        playlist_id = self.route['id'] if playlist_id is None else playlist_id
        return next((item for item in self.lib['playlists'] if item['id'] == playlist_id), None)

    def folder_of(self, folder_id=None):
        folder_id = self.route['id'] if folder_id is None else folder_id
        return next((item for item in self.lib['folders'] if item['id'] == folder_id), None)

    def current_playlist(self):
        return self.playlist_of() if self.route['kind'] == 'playlist' else None

    def song(self, song_id):
        return self.lib['songs'].get(song_id)

    def current_song(self):
        player = self.player
        return self.song(player['queue'][player['index']]) if 0 <= player['index'] < len(player['queue']) else None

    # Songs without a cover of their own show the user's default picture, if one was chosen in Settings.
    def default_art(self):
        chosen = self.lib.get('defaultArtId')
        return self.store.path(chosen) if chosen and self.store.exists(chosen) else os.path.join(self.resource_dir, 'cover.svg')

    def art_of(self, song):
        return self.store.path(song['artId']) if song and song.get('artId') else self.default_art()

    def all_songs(self):
        return list(self.lib['songs'].values())

    @staticmethod
    def artist_of(song):
        return song.get('artist') or T('unknownArtist', 'Unknown Artist')

    @staticmethod
    def album_of(song):
        return song.get('album') or T('unknownAlbum', 'Unknown Album')

    def album_key(self, song):
        return f'{self.album_of(song)}{ALBUM_SEP}{song.get("albumArtist") or self.artist_of(song)}'

    def albums(self):
        found = {}
        for song in self.all_songs():
            key = self.album_key(song)
            if key not in found:
                found[key] = {'id': key, 'title': self.album_of(song), 'artist': song.get('albumArtist') or self.artist_of(song), 'songs': []}
            found[key]['songs'].append(song)
        return sorted(found.values(), key=lambda album: natkey(album['title']))

    def artists(self):
        found = {}
        for song in self.all_songs():
            key = self.artist_of(song)
            if key not in found:
                found[key] = {'id': key, 'title': key, 'songs': []}
            found[key]['songs'].append(song)
        return sorted(found.values(), key=lambda artist: natkey(artist['title']))

    def playlist_cover(self, item):
        if item.get('coverFileId') and self.store.exists(item['coverFileId']):
            return self.store.path(item['coverFileId'])
        first = next((song for song in map(self.song, item['songIds']) if song and song.get('artId')), None)
        return self.art_of(first)

    def playlist_songs(self, item):
        return [song for song in map(self.song, dict.fromkeys(item['songIds'])) if song]

    def folder_playlists(self, folder_id):
        return [item for item in self.lib['playlists'] if item.get('folderId') == folder_id]

    def folder_songs(self, folder_id):
        ids = dict.fromkeys(song_id for item in self.folder_playlists(folder_id) for song_id in item['songIds'])
        return [song for song in map(self.song, ids) if song]

    @staticmethod
    def _track(song):
        try:
            return int(song.get('track') or 0) or 999
        except (TypeError, ValueError):
            return 999

    # Songs of the current detail route, before search / sort.
    def base_songs(self):
        kind, target = self.route['kind'], self.route['id']
        songs = self.all_songs()
        if kind == 'playlist':
            item = self.playlist_of()
            return [song for song in map(self.song, item['songIds'] if item else []) if song]
        if kind == 'album':
            return sorted((song for song in songs if self.album_key(song) == target), key=lambda song: (self._track(song), natkey(song['title'])))
        if kind == 'artist':
            return sorted((song for song in songs if self.artist_of(song) == target), key=lambda song: (natkey(self.album_of(song)), self._track(song)))
        if kind == 'favorites':
            return sorted((song for song in songs if song.get('loved')), key=lambda song: natkey(song['title']))
        if kind == 'recent':
            return sorted(songs, key=lambda song: -(song.get('added') or 0))
        return sorted(songs, key=lambda song: natkey(song['title']))

    def visible_songs(self):
        songs = self.base_songs()
        needle = self.query.strip().casefold()
        if needle:
            songs = [song for song in songs if needle in f'{song["title"]}\n{song.get("artist", "")}\n{song.get("album", "")}'.casefold()]
        sort = self.sorts.get(self.route_key())
        if sort:
            field = sort['field']
            numbers = {'time': 'duration', 'added': 'added', 'plays': 'plays'}
            if field in numbers:
                songs = sorted(songs, key=lambda song: song.get(numbers[field]) or 0)
            else:
                value = {'artist': self.artist_of, 'album': self.album_of}.get(field, lambda song: song['title'])
                songs = sorted(songs, key=lambda song: natkey(value(song)))
            if sort['descending']:
                songs.reverse()
        return songs

    def visible_ids(self):
        return [song['id'] for song in self.visible_songs()]

    def is_detail(self):
        return self.route['kind'] not in ('albums', 'artists', 'playlists', 'folder')

    def manual_order(self):
        return bool(self.current_playlist()) and not self.sorts.get(self.route_key()) and not self.query.strip()

    # ------------------------------------------------------------ library
    def song_from_file(self, file):
        base = re.sub(r'\.[a-z0-9]{1,5}$', '', str(file.get('name') or 'Untitled'), flags=re.I)
        base = re.sub(r'^\d{1,3}[\s._-]+(?=\S)', '', base).strip() or 'Untitled'
        parts = re.split(r'\s+[-–—]\s+', base)
        tags = file.get('tags') or {}
        song = {
            'id': uid('s'), 'fileId': file['id'], 'artId': file.get('artworkId') or '',
            'title': tags.get('title') or (' - '.join(parts[1:]) if len(parts) > 1 else base).strip(),
            'artist': tags.get('artist') or (parts[0].strip() if len(parts) > 1 else ''), 'album': tags.get('album') or '',
            'albumArtist': tags.get('albumArtist') or '', 'track': tags.get('track') or '', 'year': tags.get('year') or '',
            'duration': file.get('duration') or 0, 'size': file.get('size') or 0, 'type': file.get('type') or '', 'fileName': file.get('name') or '',
            'added': now_ms(), 'plays': 0, 'loved': False, 'video': bool(file.get('video')),
        }
        if file.get('subtitles'):
            song['subtitleFiles'] = list(file['subtitles'])
        self.lib['songs'][song['id']] = song
        return song

    def _progress(self, done, total, exporting=False):
        def show():
            self.importing = {'done': done, 'total': total, 'exporting': exporting}
            self.render()
            return False
        GLib.idle_add(show)

    def add_paths(self, paths, cover_target=False, playlist_id=''):
        """Files chosen in a dialog or dropped on the window."""
        target = self.playlist_of(playlist_id) if playlist_id else self.current_playlist()
        files = importer.expand(paths)
        single = len(files) == 1
        audio, rejected = [], []
        for path in files:
            name = os.path.basename(path)
            if importer.is_audio(name):
                audio.append(path)
            elif importer.is_image(name) and target and (cover_target or single):
                self.set_cover(target, self.store.add_file(path)['id'])
            elif importer.is_archive(name):
                self.import_archive(path)
            else:
                rejected.append(name)   # not something this app keeps
        if rejected and self.ui:
            self.ui.toast(f'{T("someSkipped", "Some files were skipped")}: {", ".join(rejected)[:200]}')
        if not audio:
            return
        total = len(audio)

        def job():
            stored = []
            for index, path in enumerate(audio):
                self._progress(index, total)
                try:
                    stored.append(importer.store_song(self.store, path))
                except OSError as error:
                    stored.append(error)
            return stored

        def done(stored, error):
            self.importing = None
            failed = [str(item) for item in (stored or []) if isinstance(item, Exception)]
            for file in stored or []:
                if isinstance(file, Exception):
                    continue
                song = self.song_from_file(file)
                if target and song['id'] not in target['songIds']:
                    target['songIds'].append(song['id'])
            self.save()
            self.render()
            if error or failed:
                self.alert(T('importFailed', 'Import failed'), str(error or failed[0]))
        self.worker.submit(job, done)

    def set_cover(self, item, file_id):
        old = item.get('coverFileId')
        item['coverFileId'] = file_id
        if old and old != file_id:
            self.store.remove(old)
        self.save()
        self.render()

    def referenced_files(self):
        lib = self.lib
        wanted = {lib.get('defaultArtId')} | {item.get('coverFileId') for item in lib['playlists']}
        for song in self.all_songs():
            wanted.update((song.get('fileId'), song.get('artId')))
            wanted.update(entry.get('fileId') for entry in song.get('subtitleFiles') or [])
        wanted.discard(None)
        wanted.discard('')
        return wanted

    def delete_songs(self, ids):
        lib = self.lib
        gone = []
        for song_id in ids:
            song = self.song(song_id)
            if not song:
                continue
            current = self.current_song()
            if current and current['id'] == song_id:
                self.stop()
            del lib['songs'][song_id]
            for item in lib['playlists']:
                item['songIds'] = [other for other in item['songIds'] if other != song_id]
            lib['history'] = [other for other in lib['history'] if other != song_id]
            player = self.player
            before = sum(1 for other in player['queue'][:max(0, player['index'])] if other == song_id)
            player['queue'] = [other for other in player['queue'] if other != song_id]
            player['index'] -= before
            player['original'] = [other for other in player['original'] if other != song_id]
            gone.append(song)
        # A cover may be shared by the songs of an album: it goes with the last of them.
        keep = self.referenced_files()
        for song in gone:
            self.store.remove(song.get('fileId'))
            if song.get('artId') and song['artId'] not in keep:
                self.store.remove(song['artId'])
            for entry in song.get('subtitleFiles') or []:
                self.store.remove(entry.get('fileId'))
        self.save()
        self.render()

    def ask_delete_songs(self, ids):
        question = T('deleteSongsQuestion', 'Delete {count} song(s) from the library?').replace('{count}', str(len(ids)))
        self.ui.confirm(question, T('deleteSongsDetail', 'The audio files stored in this app are removed. This cannot be undone.'), T('delete', 'Delete'),
                        lambda: self.delete_songs(ids))

    def _new_playlist(self, name, folder_id=''):
        item = {'id': uid('p'), 'name': (name or '').strip()[:80] or T('untitledPlaylist', 'Untitled Playlist'), 'coverFileId': '', 'songIds': [], 'folderId': folder_id}
        self.lib['playlists'].append(item)
        return item

    def create_playlist(self, folder_id='', then=None):
        def made(name):
            item = self._new_playlist(name, folder_id)
            folder = self.folder_of(folder_id)
            if folder:
                folder['collapsed'] = False
            self.save()
            if then:
                then(item)
            else:
                self.go('playlist', item['id'])
        self.ui.prompt(T('newPlaylist', 'New Playlist'), T('newPlaylistPrompt', 'Name of the new playlist:'), T('untitledPlaylist', 'Untitled Playlist'), made)

    def create_folder(self, then=None):
        def made(name):
            folder = {'id': uid('f'), 'name': name.strip()[:80] or T('untitledFolder', 'Untitled Folder'), 'collapsed': False}
            self.lib['folders'].append(folder)
            if then:
                then(folder)
            self.save()
            self.render()
        self.ui.prompt(T('newFolder', 'New Playlist Folder'), T('newFolderPrompt', 'Name of the new folder:'), T('untitledFolder', 'Untitled Folder'), made)

    def rename_thing(self, item):
        if not item:
            return

        def renamed(name):
            if not name.strip():
                return
            item['name'] = name.strip()[:80]
            self.save()
            self.render()
        self.ui.prompt(T('rename', 'Rename…').replace('…', ''), T('renamePrompt', 'New name:'), item['name'], renamed)

    def rename_playlist(self, item, name):
        item['name'] = str(name or '').strip()[:80] or T('untitledPlaylist', 'Untitled Playlist')
        self.save()
        self.render()

    def delete_playlist(self, playlist_id):
        item = self.playlist_of(playlist_id)
        if not item:
            return

        def confirmed():
            self.lib['playlists'] = [entry for entry in self.lib['playlists'] if entry['id'] != playlist_id]
            if item.get('coverFileId'):
                self.store.remove(item['coverFileId'])
            self.save()
            if self.route == {'kind': 'playlist', 'id': playlist_id}:
                self.go('playlists')
            else:
                self.render()
        self.ui.confirm(T('deletePlaylistQuestion', 'Delete the playlist “{name}”?').replace('{name}', item['name']),
                        T('deletePlaylistDetail', 'The songs stay in your library.'), T('delete', 'Delete'), confirmed)

    def set_playlist_folder(self, playlist_id, folder_id):
        item = self.playlist_of(playlist_id)
        if not item or (item.get('folderId') or '') == folder_id:
            return
        item['folderId'] = folder_id
        folder = self.folder_of(folder_id)
        if folder:
            folder['collapsed'] = False
        self.save()
        self.render()

    def delete_folder(self, folder_id):
        folder = self.folder_of(folder_id)
        if not folder:
            return

        def confirmed():
            for item in self.lib['playlists']:
                if item.get('folderId') == folder_id:
                    item['folderId'] = ''
            self.lib['folders'] = [entry for entry in self.lib['folders'] if entry['id'] != folder_id]
            self.save()
            if self.route == {'kind': 'folder', 'id': folder_id}:
                self.go('playlists')
            else:
                self.render()
        self.ui.confirm(T('deleteFolderQuestion', 'Delete the folder “{name}”?').replace('{name}', folder['name']),
                        T('deleteFolderDetail', 'Its playlists are kept and move out of the folder.'), T('delete', 'Delete'), confirmed)

    def toggle_folder(self, folder_id):
        folder = self.folder_of(folder_id)
        if folder:
            folder['collapsed'] = not folder.get('collapsed')
            self.save()
            self.render()

    def add_to_playlist(self, target, song_ids):
        for song_id in song_ids:
            if self.song(song_id) and song_id not in target['songIds']:
                target['songIds'].append(song_id)
        self.save()
        self.render()

    def remove_from_playlist(self, song_ids):
        item = self.current_playlist()
        if not item or not song_ids:
            return
        item['songIds'] = [song_id for song_id in item['songIds'] if song_id not in song_ids]
        self.save()
        self.render()

    def toggle_loved(self, song_ids):
        songs = [song for song in map(self.song, song_ids) if song]
        on = not all(song.get('loved') for song in songs)
        for song in songs:
            song['loved'] = on
        self.save()
        self.render()

    def edit_song(self, song_id, title, artist, album):
        song = self.song(song_id)
        if song:
            song['title'] = title.strip()[:200] or song['title']
            song['artist'] = artist.strip()[:200]
            song['album'] = album.strip()[:200]
            self.save()
        self.render()

    # ------------------------------------------------------------ export / import
    @staticmethod
    def export_name(song):
        ext = extension_of(song.get('fileName'), '.mp3')
        title = f'{song["artist"]} - {song["title"]}' if song.get('artist') else song['title']
        return f'{safe_name(title)}{ext}'

    # One .zip per playlist: playlist.json + cover + the audio files, in order.
    def export_song_list(self, name, songs, cover_file_id=''):
        if not songs:
            return

        def chosen(target):
            entries = []
            manifest = {'format': 'macos-music-playlist-v1', 'name': name, 'cover': '', 'songs': []}
            if cover_file_id and self.store.exists(cover_file_id):
                manifest['cover'] = f'cover{extension_of(cover_file_id, ".png")}'
                entries.append({'path': manifest['cover'], 'source': self.store.path(cover_file_id)})
            for index, song in enumerate(songs):
                path = f'songs/{index + 1:02d} {self.export_name(song)}'
                manifest['songs'].append({'path': path, 'title': song['title'], 'artist': song.get('artist') or '', 'album': song.get('album') or '',
                                          'duration': song.get('duration') or 0, 'loved': bool(song.get('loved'))})
                entries.append({'path': path, 'source': self.store.path(song['fileId'])})
            entries.insert(0, {'path': 'playlist.json', 'text': json.dumps(manifest, ensure_ascii=False, indent=2)})
            self.status(T('exporting', 'Exporting…'))

            def done(_result, error):
                self.importing = None
                self.status('')
                if error:
                    self.alert(T('exportFailed', 'Export failed'), str(error))
                elif self.ui:
                    self.ui.toast(T('exportedTo', 'Exported to “{name}”').replace('{name}', os.path.basename(target)))
            self.worker.submit(lambda: importer.create_archive(target, entries, lambda done, total: self._progress(done, total, True)), done)
        self.ui.save_file(f'{safe_name(name)}.zip', chosen)

    def export_playlist(self, playlist_id):
        item = self.playlist_of(playlist_id)
        if item:
            self.export_song_list(item['name'], [song for song in map(self.song, item['songIds']) if song], item.get('coverFileId') or '')

    def export_visible(self):
        item = self.current_playlist()
        if item:
            return self.export_playlist(item['id'])
        kind, target = self.route['kind'], self.route['id']
        name = target.split(ALBUM_SEP)[0] if kind == 'album' else target if kind == 'artist' else T('songs', 'Songs')
        self.export_song_list(name, self.visible_songs())

    def export_songs(self, ids):
        songs = [song for song in map(self.song, ids) if song]
        if not songs:
            return

        def finished(_result, error):
            if error:
                self.alert(T('exportFailed', 'Export failed'), str(error))
        if len(songs) == 1:
            source = self.store.path(songs[0]['fileId'])
            self.ui.save_file(self.export_name(songs[0]), lambda target: self.worker.submit(lambda: importer.copy_out(source, target), finished))
            return

        def into(folder):
            def job():
                for index, song in enumerate(songs):
                    self._progress(index, len(songs), True)
                    name = self.export_name(song)
                    stem, ext = os.path.splitext(name)
                    target, count = os.path.join(folder, name), 1
                    while os.path.exists(target):
                        count += 1
                        target = os.path.join(folder, f'{stem} {count}{ext}')
                    importer.copy_out(self.store.path(song['fileId']), target)

            def done(result, error):
                self.importing = None
                self.render()
                finished(result, error)
            self.worker.submit(job, done)
        self.ui.pick_folder(into)

    # The whole library as one file: library.json and every file it refers to.
    def export_library(self):
        def chosen(target):
            self.file.flush()
            entries = [{'path': 'export.json', 'text': json.dumps({'format': importer.LIBRARY_FORMAT}, indent=2)},
                       {'path': 'library.json', 'text': json.dumps(self.lib, ensure_ascii=False)}]
            entries += [{'path': f'files/{name}', 'source': self.store.path(name)} for name in sorted(self.referenced_files()) if self.store.exists(name)]
            self.status(T('exporting', 'Exporting…'))

            def done(_result, error):
                self.importing = None
                self.status('')
                if error:
                    self.alert(T('exportFailed', 'Export failed'), str(error))
                elif self.ui:
                    self.ui.toast(T('exportedTo', 'Exported to “{name}”').replace('{name}', os.path.basename(target)))
            self.worker.submit(lambda: importer.create_archive(target, entries, lambda done, total: self._progress(done, total, True)), done)
        stamp = GLib.DateTime.new_now_local().format('%Y-%m-%d')
        self.ui.save_file(f'{T("libraryExportName", "Music Library")} {stamp}.zip', chosen)

    def import_library(self, path):
        """Adds what is in the export and not here yet. Into an empty library, the settings come along too."""
        lib = self.lib
        empty = not lib['songs'] and not lib['playlists'] and not lib['folders']
        known_songs, known_playlists = set(lib['songs']), {item['id'] for item in lib['playlists']}
        self.status(T('importing', 'Importing…'))

        def done(result, error):
            self.importing = None
            self.busy = ''
            if error:
                self.render()
                invalid = isinstance(error, (ValueError, importer.zipfile.BadZipFile))
                return self.alert(T('importFailed', 'Import failed'), T('libraryArchiveInvalid', 'That file is not a library export.') if invalid else str(error))
            data, songs = result
            lib['songs'].update(songs)
            folders = {folder['id'] for folder in lib['folders']}
            for folder in data.get('folders') or []:
                if isinstance(folder, dict) and folder.get('id') and folder['id'] not in folders:
                    lib['folders'].append(folder)
            lists = 0
            for item in data.get('playlists') or []:
                if not isinstance(item, dict) or not item.get('id') or not isinstance(item.get('songIds'), list):
                    continue
                mine = self.playlist_of(item['id'])
                if mine:   # the same playlist on both sides: what it lacks is added at the end
                    mine['songIds'].extend(song_id for song_id in item['songIds'] if song_id in lib['songs'] and song_id not in mine['songIds'])
                else:
                    item['songIds'] = [song_id for song_id in item['songIds'] if song_id in lib['songs']]
                    if item.get('coverFileId') and not self.store.exists(item['coverFileId']):
                        item['coverFileId'] = ''
                    lib['playlists'].append(item)
                    lists += 1
            lib['history'] = [*lib['history'], *[song_id for song_id in data.get('history') or [] if song_id in lib['songs'] and song_id not in lib['history']]][:60]
            if empty:
                for key in ('volume', 'shuffle', 'repeat', 'accent', 'fade', 'crossfade', 'appearance', 'language', 'topBar', 'subtitleSize', 'subtitlePlace', 'subtitleAt'):
                    if key in data:
                        lib[key] = data[key]
                if data.get('defaultArtId') and self.store.exists(data['defaultArtId']):
                    lib['defaultArtId'] = data['defaultArtId']
                self.audio.set_volume(lib['volume'])
            self.save()
            self.render()
            if self.ui:
                if empty:
                    self.ui.settings_changed()
                self.ui.toast(T('libraryImported', 'Imported {songs} songs and {lists} playlists.').replace('{songs}', str(len(songs))).replace('{lists}', str(lists))
                              if songs or lists else T('libraryNothingNew', 'Everything in that file is already in the library.'))
        self.worker.submit(lambda: importer.read_library_archive(self.store, path, known_songs, known_playlists, empty, self._progress), done)

    def import_archive(self, path):
        if importer.is_library_archive(path):
            return self.import_library(path)
        self.status(T('importing', 'Importing…'))

        def done(entries, error):
            self.importing = None
            self.busy = ''
            if error:
                self.render()
                return self.alert(T('importFailed', 'Import failed'), str(error))
            texts = {entry['path']: entry['text'] for entry in entries if 'text' in entry}
            by_path = {entry['path']: entry['file'] for entry in entries if 'file' in entry}
            manifest = importer.manifest_of(texts.get('playlist.json'))
            item = {'id': uid('p'), 'name': str((manifest or {}).get('name') or T('importedPlaylist', 'Imported Playlist'))[:80], 'coverFileId': '', 'songIds': [], 'folderId': ''}
            used = set()
            described_songs = manifest.get('songs') if manifest and manifest.get('format') == 'macos-music-playlist-v1' else []
            for described in described_songs if isinstance(described_songs, list) else []:
                file = by_path.get(described.get('path')) if isinstance(described, dict) else None
                if not file or 'tags' not in file:
                    continue
                song = self.song_from_file(file)
                song['title'] = str(described.get('title') or song['title'])[:200]
                song['artist'] = str(described.get('artist') or song['artist'])[:200]
                song['album'] = str(described.get('album') or song['album'])[:200]
                song['loved'] = bool(described.get('loved'))
                item['songIds'].append(song['id'])
                used.add(described['path'])
            cover = manifest.get('cover') if manifest else ''
            if cover and cover in by_path:
                item['coverFileId'] = by_path[cover]['id']
                used.add(cover)
            # A plain zip of audio files (no manifest) still becomes a playlist.
            for entry_path, file in by_path.items():
                if entry_path in used:
                    continue
                if 'tags' in file:
                    item['songIds'].append(self.song_from_file(file)['id'])
                elif importer.is_image(file['name']) and not item['coverFileId']:
                    item['coverFileId'] = file['id']
                else:
                    self.store.remove(file['id'])
            if item['songIds']:
                self.lib['playlists'].append(item)
                self.route = {'kind': 'playlist', 'id': item['id']}
                self.query = ''
            elif item['coverFileId']:
                self.store.remove(item['coverFileId'])
            self.save()
            self.render()
        self.worker.submit(lambda: importer.extract_archive(self.store, path, self._progress), done)

    # ------------------------------------------------------------ playback
    def crossfade_seconds(self):
        try:
            return max(0, min(12, int(self.lib.get('crossfade') or 0)))
        except (TypeError, ValueError):
            return 0

    def load(self, source=None):
        song = self.current_song()
        if not song:
            return
        player = self.player
        # A picture cannot be blended, so videos start and end with a cut.
        crossfade = self.crossfade_seconds() if source and not source.get('video') and not song.get('video') else 0
        player['crossing'] = False
        player['time'] = 0.0
        player['duration'] = song.get('duration') or 0
        player['paused'] = False
        self.lib['history'] = [song['id'], *[other for other in self.lib['history'] if other != song['id']]][:60]
        song['lastPlayed'] = now_ms()
        # Subtitles: the tracks inside the video come first, then the files added for this song.
        self.tracks.reset()
        self._file_tracks = []
        if song.get('video'):
            for entry in song.get('subtitleFiles') or []:
                try:
                    cues = subtitles.load(self.store.path(entry.get('fileId')))
                except OSError:
                    cues = []
                self._file_tracks.append((entry.get('name') or T('subtitles', 'Subtitles'), cues))
        self.save()
        self.render()
        if song.get('video'):
            self._read_tracks(song)
        try:
            self.audio.load(self.store.path(song['fileId']), crossfade=crossfade, video=bool(song.get('video')))
        except FileNotFoundError:
            player['paused'] = True
            self.render()
            self.alert(song['title'], T('fileMissing', 'The file of this song is missing from the library.'))
        except GLib.Error as error:
            player['paused'] = True
            self.render()
            self.alert(song['title'], error.message)

    # Start playing `ids`. With shuffle on, the chosen song (if any) comes first and the
    # rest follow in random order; the original order is kept so shuffle can be undone.
    def play_songs(self, ids, start_id=''):
        ids = list(ids)
        if not ids:
            return
        player = self.player
        player['original'] = list(ids)
        if self.lib['shuffle']:
            first = start_id or random.choice(ids)
            rest = [song_id for song_id in ids if song_id != first]
            random.shuffle(rest)
            player['queue'] = [first, *rest]
            player['index'] = 0
        else:
            player['queue'] = list(ids)
            player['index'] = ids.index(start_id) if start_id in ids else 0
        self.load()

    def play_visible(self):
        ids = self.visible_ids()
        self.play_songs(ids, '' if self.lib['shuffle'] or not ids else ids[0])

    def shuffle_visible(self):
        self.lib['shuffle'] = True
        self.save()
        self.play_songs(self.visible_ids())

    def set_shuffle(self, on):
        player = self.player
        self.lib['shuffle'] = bool(on)
        current = player['queue'][player['index']] if 0 <= player['index'] < len(player['queue']) else None
        if current:
            if on:
                rest = player['queue'][player['index'] + 1:]
                random.shuffle(rest)
                player['queue'] = [*player['queue'][:player['index'] + 1], *rest]
            elif current in player['original']:
                player['queue'] = list(player['original'])
                player['index'] = player['original'].index(current)
        self.save()
        self.render()

    def cycle_repeat(self):
        self.lib['repeat'] = {'off': 'all', 'all': 'one', 'one': 'off'}.get(self.lib['repeat'], 'off')
        self.save()
        self.render()

    def next(self, auto=False):
        player = self.player
        if not player['queue']:
            return
        source = None if player['paused'] else self.current_song()
        if auto and self.lib['repeat'] == 'one':
            return self.load(source)
        if player['index'] < len(player['queue']) - 1:
            player['index'] += 1
            return self.load(source)
        if self.lib['repeat'] == 'all' or not auto:
            if self.lib['shuffle']:
                random.shuffle(player['queue'])
            player['index'] = 0
            return self.load(source)
        player['paused'] = True
        player['time'] = 0.0
        self.render()

    def previous(self):
        player = self.player
        if not player['queue']:
            return
        if player['time'] > 3:
            return self.audio.seek(0)
        source = None if player['paused'] else self.current_song()
        player['index'] = (player['index'] - 1) % len(player['queue'])
        self.load(source)

    def toggle(self):
        if not self.current_song():
            return self.play_songs([song['id'] for song in (self.visible_songs() if self.is_detail() else self.all_songs())])
        fade = FADE_SECONDS if self.lib.get('fade') else 0
        if self.player['paused']:
            self.audio.play(fade=fade)
        else:
            self.audio.pause(fade=fade)

    def play(self):
        if self.player['paused']:
            self.toggle()

    def pause(self):
        if self.current_song() and not self.player['paused']:
            self.toggle()

    def stop(self):
        self.audio.stop()
        self.player = new_player()
        self.tracks.reset()

    def seek(self, seconds):
        if self.current_song():
            self.audio.seek(seconds)

    def enqueue(self, ids, next_up=False):
        ids = [song_id for song_id in ids if self.song(song_id)]
        if not self.current_song():
            return self.play_songs(ids)
        player = self.player
        if next_up:
            player['queue'][player['index'] + 1:player['index'] + 1] = ids
        else:
            player['queue'].extend(ids)
        player['original'].extend(dict.fromkeys(song_id for song_id in ids if song_id not in player['original']))
        self.render()

    def play_queue_position(self, position):
        if 0 <= position < len(self.player['queue']):
            self.player['index'] = position
            self.load()

    def remove_from_queue(self, position):
        if self.player['index'] < position < len(self.player['queue']):
            del self.player['queue'][position]
            self.render()

    def reorder_queue(self, positions):
        player = self.player
        player['queue'] = [*player['queue'][:player['index'] + 1], *[player['queue'][position] for position in positions if 0 <= position < len(player['queue'])]]
        self.render()

    def clear_queue(self):
        self.player['queue'] = self.player['queue'][:self.player['index'] + 1]
        self.render()

    def clear_history(self):
        self.lib['history'] = []
        self.save()
        self.render()

    def play_from_history(self, index):
        history, player = self.lib['history'], self.player
        song_id = history[index] if 0 <= index < len(history) else None
        if not self.song(song_id):
            return
        player['queue'].insert(player['index'] + 1, song_id)
        player['index'] += 1
        if song_id not in player['original']:
            player['original'].append(song_id)
        self.load()

    def set_volume(self, volume):
        was_muted = self.lib['volume'] == 0
        self.lib['volume'] = max(0.0, min(1.0, float(volume)))
        self.audio.set_volume(self.lib['volume'])
        self.save()
        if was_muted != (self.lib['volume'] == 0):
            self.render()

    def toggle_mute(self):
        self.lib['volume'] = 0 if self.lib['volume'] > 0 else 0.8
        self.audio.set_volume(self.lib['volume'])
        self.save()
        self.render()

    def _count_play(self):
        song = self.current_song()
        if song:
            song['plays'] = (song.get('plays') or 0) + 1
            self.save()

    def _on_audio(self, audio, kind, detail):
        player = self.player
        song = self.current_song()
        if kind == 'ended':
            self._count_play()
            return self.next(auto=True)
        if kind == 'error':
            player['paused'] = True
            self.render()
            return self.alert(song['title'] if song else T('appName', 'Music'), detail)
        if kind == 'seeked':
            player['time'] = audio.time
            self.tick()
            for callback in self.on_seeked:
                callback()
            return
        was_paused = player['paused']
        player['paused'] = audio.paused
        player['time'] = audio.time
        if audio.duration:
            player['duration'] = audio.duration
        # With cross-fading on, the next song starts under the end of this one.
        blend = self.crossfade_seconds()
        if (blend and kind == 'timeupdate' and not audio.paused and not player['crossing'] and player['duration'] > blend * 2 + 1
                and player['duration'] - player['time'] <= blend and song and not song.get('video')
                and (player['index'] < len(player['queue']) - 1 or self.lib['repeat'] != 'off')):
            player['crossing'] = True
            self._count_play()
            return self.next(auto=True)
        if kind == 'loadedmetadata' and song and audio.duration and abs((song.get('duration') or 0) - audio.duration) > 1:
            song['duration'] = audio.duration
            self.save()
        # Progress ticks only touch the clock and the timeline; everything else is redrawn.
        if kind == 'timeupdate' and was_paused == audio.paused:
            self.tick()
        else:
            self.render()

    # ------------------------------------------------------------ subtitles
    def _read_tracks(self, song):
        """The subtitle tracks inside the video are read out beside playback (a moment for a
        large file); the player never decodes or switches them itself."""
        key = song['fileId']
        path = self.store.path(key)

        def found(tracks):
            if tracks is not None:
                self._embedded[key] = tracks
            if self.current_song() is song:
                self._tracks_found(song, tracks or [])
            return False

        def read():
            try:
                tracks = importer.embedded_subtitles(path)
            except Exception:   # an unreadable file simply has no tracks
                tracks = []
            GLib.idle_add(found, tracks)
        if key in self._embedded:
            GLib.idle_add(found, None)
        else:
            threading.Thread(target=read, daemon=True).start()

    def _tracks_found(self, song, _fresh):
        tracks = []
        for index, track in enumerate(self._embedded.get(song['fileId']) or []):
            fallback = T('subtitleTrack', 'Track {n}').replace('{n}', str(index + 1))
            label = ' — '.join(part for part in (track['language'], track['title']) if part) or fallback
            cues = []
            for start, seconds, text, is_markup in track['cues']:
                markup = text if is_markup else subtitles.to_markup(text)
                if markup:
                    cues.append(subtitles.Cue(start, start + max(0.3, seconds), markup))
            cues.sort(key=lambda cue: cue.start)
            tracks.append((label, cues))
        self.tracks.set_items([*tracks, *self._file_tracks])
        wanted = -1 if song.get('subtitleOff') else int(song.get('subtitleIndex') or 0)
        self.tracks.select(wanted if wanted < len(self.tracks.items) else -1)
        self.render()

    def choose_subtitle(self, index):
        song = self.current_song()
        if not song:
            return
        self.tracks.select(index)
        song['subtitleOff'] = self.tracks.selected < 0
        song['subtitleIndex'] = max(0, self.tracks.selected)
        self.save()
        self.render()

    def add_subtitle_file(self, path):
        song = self.current_song()
        if not song:
            return
        name = os.path.basename(path)
        try:
            cues = subtitles.load(path)
        except OSError:
            cues = []
        if not cues:
            return self.alert(name, T('subtitleUnreadable', 'No subtitles could be read from that file.'))
        file = self.store.add_file(path)
        song.setdefault('subtitleFiles', []).append({'fileId': file['id'], 'name': name})
        self._file_tracks.append((name, cues))
        self.choose_subtitle(self.tracks.add_file(name, cues))

    def subtitle_markup(self):
        """What the video shows right now."""
        if self.tracks.selected < 0 or not self.audio.active.loaded:
            return ''
        position = self.audio.active.position()
        return self.tracks.markup_at(self.player['time'] if position is None else position)

    # ------------------------------------------------------------ menus
    # A menu is a list of (label, callback[, options]) — or (label, [submenu…]) — with None as separator.
    def sort_menu(self):
        key = self.route_key()
        sort = self.sorts.get(key)
        fields = [('title', T('colTitle', 'Title')), ('artist', T('colArtist', 'Artist')), ('album', T('colAlbum', 'Album')), ('time', T('colTime', 'Time')),
                  ('added', T('dateAdded', 'Date Added')), ('plays', T('plays', 'Plays'))]

        def default():
            self.sorts.pop(key, None)
            self.render()

        def by(field):
            self.sorts[key] = {'field': field, 'descending': (not sort['descending']) if sort and sort['field'] == field else False}
            self.render()

        def direction(descending):
            if key in self.sorts:
                self.sorts[key]['descending'] = descending
                self.render()
        return [
            (T('playlistOrder', 'Playlist Order') if self.current_playlist() else T('defaultOrder', 'Default Order'), default, {'checked': not sort}), None,
            *[(label, lambda field=field: by(field), {'checked': bool(sort) and sort['field'] == field}) for field, label in fields], None,
            (T('ascending', 'Ascending'), lambda: direction(False), {'checked': bool(sort) and not sort['descending'], 'disabled': not sort}),
            (T('descending', 'Descending'), lambda: direction(True), {'checked': bool(sort) and sort['descending'], 'disabled': not sort}),
        ]

    def folder_submenu(self, playlist_id):
        item = self.playlist_of(playlist_id)

        def move(folder_id):
            item['folderId'] = folder_id
            self.save()
            self.render()
        folders = [(folder['name'], lambda folder=folder: move(folder['id']), {'checked': item.get('folderId') == folder['id']}) for folder in self.lib['folders']]
        return [*folders, *([None] if folders else []),
                (T('noFolder', 'No Folder'), lambda: move(''), {'checked': not item.get('folderId')}),
                (T('newFolder', 'New Playlist Folder'), lambda: self.create_folder(lambda folder: move(folder['id'])))]

    def playlist_submenu(self, song_ids):
        current = self.current_playlist()
        lists = [(item['name'], lambda item=item: self.add_to_playlist(item, song_ids)) for item in self.lib['playlists'] if item is not current]
        return [*lists, *([None] if lists else []),
                (T('newPlaylist', 'New Playlist'), lambda: self.create_playlist(then=lambda item: self.add_to_playlist(item, song_ids)))]

    def more_menu(self):
        item = self.current_playlist()
        empty = not self.visible_songs()
        return [
            (T('addSongs', 'Add Songs…'), self.ui.import_songs),
            *([(T('chooseCover', 'Choose Cover…'), self.ui.choose_cover), (T('rename', 'Rename…'), lambda: self.rename_thing(item)),
               (T('moveToFolder', 'Move to Folder…').replace('…', ''), self.folder_submenu(item['id']))] if item else []),
            None,
            (T('addToQueue', 'Add to Queue'), lambda: self.enqueue(self.visible_ids()), {'disabled': empty}),
            (T('exportPlaylist', 'Export Playlist…') if item else T('exportSongs', 'Export as Playlist…'), self.export_visible, {'disabled': empty}),
            *([None, (T('deletePlaylist', 'Delete Playlist'), lambda: self.delete_playlist(item['id']))] if item else []),
        ]

    def add_menu(self):
        return [
            (T('newPlaylist', 'New Playlist'), lambda: self.create_playlist(self.route['id'] if self.route['kind'] == 'folder' else '')),
            (T('newFolder', 'New Playlist Folder'), self.create_folder), None,
            (T('importPlaylist', 'Import Playlist…'), self.ui.import_playlist),
        ]

    def playlist_menu(self, playlist_id):
        item = self.playlist_of(playlist_id)
        if not item:
            return []
        ids = [song_id for song_id in item['songIds'] if self.song(song_id)]

        def shuffle():
            self.lib['shuffle'] = True
            self.play_songs(ids)
        return [
            (T('play', 'Play'), lambda: self.play_songs(ids, '' if self.lib['shuffle'] or not ids else ids[0])), (T('shuffle', 'Shuffle'), shuffle), None,
            (T('rename', 'Rename…'), lambda: self.rename_thing(item)), (T('moveToFolder', 'Move to Folder…').replace('…', ''), self.folder_submenu(playlist_id)),
            (T('exportPlaylist', 'Export Playlist…'), lambda: self.export_playlist(playlist_id)), None,
            (T('deletePlaylist', 'Delete Playlist'), lambda: self.delete_playlist(playlist_id)),
        ]

    def folder_menu(self, folder_id):
        return [
            (T('newPlaylist', 'New Playlist'), lambda: self.create_playlist(folder_id)), (T('rename', 'Rename…'), lambda: self.rename_thing(self.folder_of(folder_id))), None,
            (T('deleteFolder', 'Delete Folder'), lambda: self.delete_folder(folder_id)),
        ]

    def song_menu(self, song_id, selected):
        targets = list(selected) if selected else [song_id]
        in_playlist = bool(self.current_playlist())
        return [
            (T('play', 'Play'), lambda: self.play_songs(self.visible_ids(), song_id)), (T('playNext', 'Play Next'), lambda: self.enqueue(targets, next_up=True)),
            (T('addToQueue', 'Add to Queue'), lambda: self.enqueue(targets)), None,
            (T('addToPlaylist', 'Add to Playlist…').replace('…', ''), self.playlist_submenu(targets)),
            (T('toggleFavourite', 'Favourite / Unfavourite'), lambda: self.toggle_loved(targets)),
            (T('getInfo', 'Get Info…'), lambda: self.ui.edit_info(song_id)), (T('exportSong', 'Export Song…'), lambda: self.export_songs(targets)), None,
            *([(T('removeFromPlaylist', 'Remove from Playlist'), lambda: self.remove_from_playlist(targets))] if in_playlist else []),
            (T('deleteFromLibrary', 'Delete from Library'), lambda: self.ask_delete_songs(targets)),
        ]

    def queue_menu(self, position):
        return [(T('play', 'Play'), lambda: self.play_queue_position(position)), (T('removeFromQueue', 'Remove from Queue'), lambda: self.remove_from_queue(position))]

    # Subtitles of the video that is playing: off, one of its tracks, or a file to add.
    def subtitle_menu(self):
        tracks = self.tracks
        return [
            (T('subtitlesOff', 'Off'), lambda: self.choose_subtitle(-1), {'checked': tracks.selected < 0}),
            *[(track['label'], lambda index=index: self.choose_subtitle(index), {'checked': index == tracks.selected}) for index, track in enumerate(tracks.items)],
            None, (T('addSubtitleFile', 'Add Subtitle File…'), self.ui.add_subtitle),
            None,
            (T('subtitleSize', 'Size'), [(f'{size}%', lambda size=size: self.set_subtitle_look(size=size), {'checked': self.subtitle_size() == size})
                                         for size in (50, 75, 100, 125, 150, 200, 250)]),
            (T('subtitlePosition', 'Position'), [(label, lambda place=place: self.set_subtitle_look(place=place), {'checked': self.subtitle_place() == place})
                                                 for place, label in (('top', T('posAutoTop', 'Automatic — top')), ('bottom', T('posAutoBottom', 'Automatic — bottom')),
                                                                      ('custom', T('posCustom', 'Custom (drag the subtitle)')))]),
        ]

    # How subtitles look, for every video: size in percent; at the top, at the bottom, or
    # wherever they were dragged to (the middle of the words, as fractions of the picture).
    def subtitle_size(self):
        try:
            return max(50, min(250, int(self.lib.get('subtitleSize') or 100)))
        except (TypeError, ValueError):
            return 100

    def subtitle_place(self):
        place = self.lib.get('subtitlePlace')
        return place if place in ('top', 'custom') else 'bottom'

    def subtitle_at(self):
        at = self.lib.get('subtitleAt')
        try:
            return max(0.0, min(1.0, float(at[0]))), max(0.0, min(1.0, float(at[1])))
        except (TypeError, ValueError, IndexError):
            return 0.5, 0.8

    def set_subtitle_look(self, size=None, place=None, at=None, save=True):
        if size is not None:
            self.lib['subtitleSize'] = size
        if place is not None:
            self.lib['subtitlePlace'] = place
        if at is not None:
            self.lib['subtitleAt'] = [round(at[0], 4), round(at[1], 4)]
        if save:
            self.save()

    # ------------------------------------------------------------ from outside
    # music://song/<id> and music://playlist/<id>: how other applications point at something here.
    def open_url(self, url):
        match = re.match(r'^music://(song|playlist)/([^/?#]+)', str(url or ''), re.I)
        if match:
            self.open_item(match.group(1).lower(), GLib.uri_unescape_string(match.group(2), None) or '')

    def open_item(self, kind, key):
        if kind == 'song' and self.song(key):
            self.show_now = False
            self.go('songs')
            self.play_songs([song['id'] for song in sorted(self.all_songs(), key=lambda song: natkey(song['title']))], key)
        if kind == 'playlist' and self.playlist_of(key):
            self.show_now = False
            self.go('playlist', key)

    def search_items(self):
        """What the desktop's search can find: songs and playlists."""
        items = [(f'song:{song["id"]}', song.get('title') or '', ' — '.join(part for part in (song.get('artist'), song.get('album')) if part), self.art_of(song))
                 for song in self.all_songs()]
        items += [(f'playlist:{item["id"]}', item.get('name') or '', T('playlists', 'Playlists'), self.playlist_cover(item)) for item in self.lib['playlists']]
        return [item for item in items if item[1]]
