# Music — what is kept on disk: the file store and the library document.
#
# Imported songs are copied into the store (<data dir>/files); the library only keeps
# their ids and metadata. Covers, playlist covers and subtitle files live there too.
import hashlib
import json
import mimetypes
import os
import random
import shutil
import time

from gi.repository import GLib

from . import APP_ID

_DIGITS = '0123456789abcdefghijklmnopqrstuvwxyz'


def _base36(number):
    text = ''
    while number:
        number, digit = divmod(number, 36)
        text = _DIGITS[digit] + text
    return text or '0'


def uid(prefix):
    return f'{prefix}-{_base36(int(time.time() * 1000))}{"".join(random.choice(_DIGITS) for _ in range(5))}'


def now_ms():
    return int(time.time() * 1000)


def default_data_dir():
    return os.path.join(GLib.get_user_data_dir(), APP_ID)


def _config_path():
    return os.path.join(GLib.get_user_config_dir(), APP_ID, 'config.json')


def chosen_data_dir():
    """Where the user put the library (Settings ▸ Storage), or '' for the default place."""
    try:
        with open(_config_path(), encoding='utf-8') as handle:
            return str(json.load(handle).get('library') or '')
    except (OSError, ValueError, AttributeError):
        return ''


def set_data_dir(path):
    if os.environ.get('MUSIC_DATA_DIR'):
        return   # the environment decides; nothing to remember
    os.makedirs(os.path.dirname(_config_path()), exist_ok=True)
    with open(_config_path(), 'w', encoding='utf-8') as handle:
        json.dump({'library': '' if os.path.realpath(path) == os.path.realpath(default_data_dir()) else path}, handle, ensure_ascii=False)


def data_dir():
    # A chosen place that is not there (a drive that is not connected) is never created
    # anew: the default library is used meanwhile and the choice is kept.
    if os.environ.get('MUSIC_DATA_DIR'):
        return os.environ['MUSIC_DATA_DIR']
    chosen = chosen_data_dir()
    return chosen if chosen and os.path.isdir(chosen) else default_data_dir()


def extension_of(name, fallback=''):
    ext = os.path.splitext(str(name or ''))[1].lower()
    return ext if 1 < len(ext) <= 6 and ext[1:].isalnum() else fallback


def type_of(name):
    return mimetypes.guess_type(str(name or ''))[0] or ''


class FileStore:
    def __init__(self, root):
        self.dir = os.path.join(root, 'files')
        os.makedirs(self.dir, exist_ok=True)

    def move_to(self, root):
        self.dir = os.path.join(root, 'files')
        os.makedirs(self.dir, exist_ok=True)

    def path(self, file_id):
        # Ids are plain file names; anything else in a damaged library never leaves the store.
        return os.path.join(self.dir, os.path.basename(str(file_id))) if file_id else ''

    def exists(self, file_id):
        return bool(file_id) and os.path.isfile(self.path(file_id))

    def size(self, file_id):
        try:
            return os.path.getsize(self.path(file_id))
        except OSError:
            return 0

    def _describe(self, file_id, name):
        return {'id': file_id, 'name': name, 'size': self.size(file_id), 'type': type_of(name)}

    def add_file(self, source, name=None):
        name = name or os.path.basename(source)
        file_id = uid('f') + extension_of(name)
        shutil.copyfile(source, self.path(file_id))
        return self._describe(file_id, name)

    def add_stream(self, stream, name):
        file_id = uid('f') + extension_of(name)
        with open(self.path(file_id), 'wb') as target:
            shutil.copyfileobj(stream, target)
        return self._describe(file_id, name)

    def add_art(self, data, ext):
        # Songs of one album carry the same picture: it is stored once.
        file_id = f'art-{hashlib.sha1(data).hexdigest()[:20]}{ext}'
        if not self.exists(file_id):
            with open(self.path(file_id), 'wb') as target:
                target.write(data)
        return file_id

    def remove(self, file_id):
        if not file_id:
            return
        try:
            os.remove(self.path(file_id))
        except OSError:
            pass

    def list(self):
        files = {}
        try:
            with os.scandir(self.dir) as entries:
                for entry in entries:
                    if entry.is_file():
                        files[entry.name] = entry.stat().st_size
        except OSError:
            pass
        return files


DEFAULTS = {'songs': {}, 'playlists': [], 'folders': [], 'volume': 0.8, 'shuffle': False, 'repeat': 'off', 'accent': '',
            'defaultArtId': '', 'history': [], 'fade': False, 'crossfade': 0, 'appearance': 'system'}


class LibraryFile:
    """library.json: read once, written a moment after the last change."""

    def __init__(self, root):
        os.makedirs(root, exist_ok=True)
        self.path = os.path.join(root, 'library.json')
        self.data = json.loads(json.dumps(DEFAULTS))
        self._timer = 0
        self.frozen = False   # while the library is being moved, nothing is written
        self.on_saved = None
        try:
            with open(self.path, encoding='utf-8') as handle:
                saved = json.load(handle)
            if isinstance(saved, dict):
                self.data.update(saved)
                for key, kind in (('songs', dict), ('playlists', list), ('folders', list), ('history', list)):
                    if not isinstance(self.data.get(key), kind):
                        self.data[key] = kind()
        except (OSError, ValueError):
            pass

    def move_to(self, root):
        self.path = os.path.join(root, 'library.json')

    def save(self):
        if self._timer:
            GLib.source_remove(self._timer)
        self._timer = GLib.timeout_add(250, self._write)

    def flush(self):
        if self._timer:
            GLib.source_remove(self._timer)
            self._write()

    def _write(self):
        self._timer = 0
        if self.frozen:
            return False
        try:
            temporary = self.path + '.tmp'
            with open(temporary, 'w', encoding='utf-8') as handle:
                json.dump(self.data, handle, ensure_ascii=False)
            os.replace(temporary, self.path)
        except OSError as error:
            print('save failed', error)
        if self.on_saved:
            self.on_saved()
        return False
