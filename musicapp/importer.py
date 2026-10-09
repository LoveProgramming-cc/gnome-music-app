# Music — getting files in and out: copying into the store, reading tags and covers,
# playlist archives. Everything here runs on the worker thread.
import json
import os
import queue
import re
import shutil
import threading
import zipfile

import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstPbutils', '1.0')
gi.require_version('GstTag', '1.0')
from gi.repository import GLib, Gst, GstPbutils, GstTag  # noqa: E402

from .store import type_of  # noqa: E402

VIDEO_EXT = re.compile(r'\.(mp4|m4v|mov|webm|mkv)$', re.I)
AUDIO_EXT = re.compile(r'\.(mp3|m4a|aac|flac|wav|ogg|oga|opus|weba)$', re.I)
SUBTITLE_EXT = ('.srt', '.vtt', '.ass', '.ssa')
IMAGE_TYPES = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp', 'image/gif': '.gif', 'image/bmp': '.bmp'}


def is_video(name):
    return type_of(name).startswith('video/') or bool(VIDEO_EXT.search(name))


# Videos are songs with a picture: same library, same queue; Now Playing shows the video.
def is_audio(name):
    return type_of(name).startswith('audio/') or bool(AUDIO_EXT.search(name)) or is_video(name)


def is_image(name):
    return type_of(name).startswith('image/')


def is_archive(name):
    return name.lower().endswith('.zip')


class Worker:
    """One background thread: jobs run in order, their results come back on the main loop."""

    def __init__(self):
        self._jobs = queue.Queue()
        threading.Thread(target=self._run, daemon=True).start()

    def submit(self, job, done=None):
        self._jobs.put((job, done))

    def _run(self):
        while True:
            job, done = self._jobs.get()
            result = error = None
            try:
                result = job()
            except Exception as problem:   # reported to whoever asked for the job
                error = problem
            if done:
                GLib.idle_add(lambda done=done, result=result, error=error: done(result, error) and False)


_discoverer = None


def _image_ext(sample, data):
    caps = sample.get_caps()
    name = caps.get_structure(0).get_name() if caps and caps.get_size() else ''
    if name in IMAGE_TYPES:
        return IMAGE_TYPES[name]
    if data[:3] == b'\xff\xd8\xff':
        return '.jpg'
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return '.png'
    return ''


def _cover(tag_lists):
    for tags in tag_lists:
        for name in ('image', 'preview-image'):
            best = None
            for index in range(tags.get_tag_size(name)):
                ok, sample = tags.get_sample_index(name, index)
                if not ok:
                    continue
                info = sample.get_info()
                front = bool(info and info.has_field('image-type') and 'FRONT' in str(info.get_value('image-type')).upper())
                if best is None or front:
                    best = sample
                if front:
                    break
            if best is None:
                continue
            buffer = best.get_buffer()
            data = buffer.extract_dup(0, buffer.get_size()) if buffer and buffer.get_size() else b''
            ext = _image_ext(best, data)
            if ext and data:
                return data, ext
    return None


def probe(path):
    """Tags, duration, cover and whether there is a picture — what GStreamer can tell."""
    global _discoverer
    found = {'tags': {}, 'duration': 0.0, 'video': None, 'cover': None}
    try:
        if _discoverer is None:
            Gst.init(None)
            _discoverer = GstPbutils.Discoverer.new(8 * Gst.SECOND)
        info = _discoverer.discover_uri(Gst.filename_to_uri(path))
    except GLib.Error:
        return found
    videos = [stream for stream in info.get_video_streams() if not stream.is_image()]
    audios = info.get_audio_streams()
    if not videos and not audios:
        return found
    found['video'] = bool(videos)
    duration = info.get_duration()
    found['duration'] = duration / Gst.SECOND if duration and duration != Gst.CLOCK_TIME_NONE else 0.0
    tag_lists = [tags for tags in [stream.get_tags() for stream in [info.get_stream_info(), *audios, *videos] if stream] if tags]

    # In a video, a stream's "title" is the name of that track ("Stereo", "Commentary"),
    # not of the film: only the container speaks for the whole file.
    whole = [tags for tags in [info.get_stream_info().get_tags()] if tags] if videos and info.get_stream_info() else tag_lists

    def text(name):
        for tags in (whole if name == 'title' else tag_lists):
            ok, value = tags.get_string(name)
            if ok and value and value.strip():
                return value.strip()
        return ''
    tags = found['tags']
    tags['title'], tags['artist'], tags['album'], tags['albumArtist'] = text('title'), text('artist'), text('album'), text('album-artist')
    if videos and tags['title'].lower() in ('audio', 'video'):
        tags['title'] = ''
    for source in tag_lists:
        ok, number = source.get_uint('track-number')
        if ok and number:
            tags['track'] = number
            break
    for source in tag_lists:
        ok, moment = source.get_date_time('datetime')
        if ok and moment and moment.has_year():
            tags['year'] = moment.get_year()
            break
    found['cover'] = _cover(tag_lists)
    return found


def video_frame(path, duration=0.0):
    """One picture out of a video, as JPEG — the cover of a video that has none."""
    Gst.init(None)
    pipeline = Gst.parse_launch('uridecodebin name=source ! videoconvert ! videoscale ! '
                                'video/x-raw,pixel-aspect-ratio=1/1 ! jpegenc quality=88 ! appsink name=sink sync=false max-buffers=1')
    source = pipeline.get_by_name('source')
    source.set_property('uri', Gst.filename_to_uri(path))
    bus = pipeline.get_bus()

    def settled():
        message = bus.timed_pop_filtered(10 * Gst.SECOND, Gst.MessageType.ASYNC_DONE | Gst.MessageType.ERROR | Gst.MessageType.EOS)
        return message is not None and message.type == Gst.MessageType.ASYNC_DONE
    try:
        pipeline.set_state(Gst.State.PAUSED)
        if not settled():
            return None
        if not duration:
            ok, length = pipeline.query_duration(Gst.Format.TIME)
            duration = length / Gst.SECOND if ok and length > 0 else 0.0
        # Not the first frame (often black, or a title card): a little way in.
        moment = min(duration * 0.2, 30.0)
        if moment > 0.5 and pipeline.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, int(moment * Gst.SECOND)):
            if not settled():
                return None
        sample = pipeline.get_by_name('sink').emit('pull-preroll')
        if sample is None:
            return None
        buffer = sample.get_buffer()
        return buffer.extract_dup(0, buffer.get_size()) if buffer and buffer.get_size() else None
    except GLib.Error:
        return None
    finally:
        pipeline.set_state(Gst.State.NULL)


TEXT_CAPS = ('text/x-raw', 'application/x-ass', 'application/x-ssa')


def embedded_subtitles(path, timeout=90):
    """The subtitle tracks inside a video: [{'language', 'title', 'cues': [(start, seconds, text, is_markup)]}].
    The file is only taken apart, nothing is decoded — and it happens beside playback, not in it."""
    Gst.init(None)
    pipeline = Gst.Pipeline.new(None)
    source = Gst.ElementFactory.make('filesrc')
    parser = Gst.ElementFactory.make('parsebin')
    source.set_property('location', path)
    pipeline.add(source)
    pipeline.add(parser)
    source.link(parser)
    outputs = []

    def pad_added(_parser, pad):
        caps = pad.get_current_caps() or pad.query_caps(None)
        kind = caps.get_structure(0).get_name() if caps and caps.get_size() else ''
        chain = []
        if kind in TEXT_CAPS:
            if kind != 'text/x-raw':
                chain.append(Gst.ElementFactory.make('ssaparse'))
            sink = Gst.ElementFactory.make('appsink')
            sink.set_property('caps', Gst.Caps.from_string('text/x-raw'))
            outputs.append(sink)
        else:
            sink = Gst.ElementFactory.make('fakesink')
        sink.set_property('sync', False)
        sink.set_property('async', False)
        chain.append(sink)
        if any(element is None for element in chain):
            return
        for element in chain:
            pipeline.add(element)
        for first, second in zip(chain, chain[1:]):
            first.link(second)
        pad.link(chain[0].get_static_pad('sink'))
        for element in reversed(chain):
            element.sync_state_with_parent()
    parser.connect('pad-added', pad_added)
    found = {}   # appsink -> its cues

    def drain():
        for sink in list(outputs):
            cues = found.setdefault(sink, [])
            while (sample := sink.emit('try-pull-sample', 0)) is not None:
                buffer = sample.get_buffer()
                caps = sample.get_caps()
                if buffer is None or buffer.pts == Gst.CLOCK_TIME_NONE or not buffer.get_size():
                    continue
                text = buffer.extract_dup(0, buffer.get_size()).decode('utf-8', 'replace').strip('\0').strip()
                if not text:
                    continue
                seconds = buffer.duration / Gst.SECOND if buffer.duration != Gst.CLOCK_TIME_NONE and buffer.duration > 0 else 4.0
                is_markup = bool(caps and caps.get_size() and caps.get_structure(0).get_string('format') == 'pango-markup')
                cues.append((buffer.pts / Gst.SECOND, seconds, text, is_markup))
    tracks = []
    try:
        pipeline.set_state(Gst.State.PLAYING)
        bus = pipeline.get_bus()
        deadline = GLib.get_monotonic_time() + timeout * 1_000_000
        # The text has to be taken out as it comes: a sink holding lines never reaches its end.
        while GLib.get_monotonic_time() < deadline:
            message = bus.timed_pop_filtered(20 * Gst.MSECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
            drain()
            if message is not None:
                break
        drain()
        for sink in outputs:
            language = title = ''
            pad = sink.get_static_pad('sink')
            index = 0
            while (event := pad.get_sticky_event(Gst.EventType.TAG, index)) is not None:
                tags = event.parse_tag()
                ok, value = tags.get_string('language-code')
                language = (GstTag.tag_get_language_name(value) or value) if ok and value else language
                ok, value = tags.get_string('title')
                title = value if ok and value else title
                index += 1
            tracks.append({'language': language, 'title': title, 'cues': found.get(sink, [])})
    finally:
        pipeline.set_state(Gst.State.NULL)
    return tracks


def cover_for(store, file, found):
    """The cover in the file's tags — or, for a video without one, a frame of it."""
    if found['cover']:
        return store.add_art(*found['cover'])
    if file['video']:
        frame = video_frame(store.path(file['id']), found['duration'])
        if frame:
            return store.add_art(frame, '.jpg')
    return ''


def store_song(store, path, name=None, look_beside=True):
    """Copy one audio/video file into the store and describe it."""
    file = store.add_file(path, name)
    found = probe(store.path(file['id']))
    file['tags'] = found['tags']
    file['duration'] = found['duration']
    file['video'] = is_video(file['name']) if found['video'] is None else found['video']
    file['artworkId'] = cover_for(store, file, found)
    file['subtitles'] = []
    if file['video'] and look_beside:
        # A subtitle file lying next to a video, under the same name, comes along.
        stem = os.path.splitext(path)[0]
        for ext in SUBTITLE_EXT:
            for candidate in (stem + ext, stem + ext.upper()):
                if os.path.isfile(candidate):
                    extra = store.add_file(candidate)
                    file['subtitles'].append({'fileId': extra['id'], 'name': extra['name']})
                    break
    return file


def expand(paths):
    """Folders become the music inside them (whatever else lies there is left alone)."""
    files = []
    for path in paths:
        if os.path.isdir(path):
            for root, directories, names in os.walk(path):
                directories.sort()
                files.extend(os.path.join(root, name) for name in sorted(names) if not name.startswith('.') and is_audio(name))
        elif os.path.isfile(path):
            files.append(path)
    return files


def extract_archive(store, path, progress=None):
    """A playlist .zip → [{'path', 'text'} | {'path', 'file'}]. Only what the app keeps is stored."""
    entries = []
    with zipfile.ZipFile(path) as archive:
        members = [member for member in archive.infolist() if not member.is_dir()]
        wanted = [member for member in members if member.filename == 'playlist.json' or is_audio(member.filename) or is_image(member.filename)]
        for index, member in enumerate(wanted):
            if progress:
                progress(index, len(wanted))
            name = os.path.basename(member.filename)
            if member.filename == 'playlist.json':
                entries.append({'path': member.filename, 'text': archive.read(member).decode('utf-8', 'replace')})
                continue
            with archive.open(member) as stream:
                file = store.add_stream(stream, name)
            if is_audio(name):
                found = probe(store.path(file['id']))
                file['tags'] = found['tags']
                file['duration'] = found['duration']
                file['video'] = is_video(name) if found['video'] is None else found['video']
                file['artworkId'] = cover_for(store, file, found)
                file['subtitles'] = []
            entries.append({'path': member.filename, 'file': file})
    return entries


def create_archive(target, entries, progress=None):
    """entries: [{'path', 'text'} | {'path', 'source'}]. Audio is compressed already: stored as is."""
    temporary = target + '.part'
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_STORED, allowZip64=True) as archive:
            for index, entry in enumerate(entries):
                if progress:
                    progress(index, len(entries))
                if 'text' in entry:
                    archive.writestr(entry['path'], entry['text'], zipfile.ZIP_DEFLATED)
                elif os.path.isfile(entry['source']):
                    archive.write(entry['source'], entry['path'])
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def move_library(old, new, progress=None, no_space='Not enough free space.'):
    """Everything of the library in `old` — library.json and files/ — to `new`, then gone
    from `old`. On one disk the files are renamed; across disks they are copied, checked,
    and only then deleted. If anything fails, `old` is put back as it was."""
    old_files, new_files = os.path.join(old, 'files'), os.path.join(new, 'files')
    os.makedirs(new_files, exist_ok=True)
    names = sorted(entry.name for entry in os.scandir(old_files) if entry.is_file()) if os.path.isdir(old_files) else []
    same_disk = os.stat(old).st_dev == os.stat(new).st_dev
    if not same_disk:
        needed = sum(os.path.getsize(os.path.join(old_files, name)) for name in names)
        if shutil.disk_usage(new).free < needed + 64 * 1024 * 1024:
            raise OSError(no_space)
    document = os.path.join(old, 'library.json')
    done = []
    try:
        for index, name in enumerate(names):
            if progress:
                progress(index, len(names))
            source, target = os.path.join(old_files, name), os.path.join(new_files, name)
            if same_disk:
                os.replace(source, target)
            else:
                shutil.copy2(source, target)
                if os.path.getsize(target) != os.path.getsize(source):
                    raise OSError(f'{name}: copy is incomplete')
            done.append(name)
        if os.path.isfile(document):
            shutil.copy2(document, os.path.join(new, 'library.json'))
    except Exception:
        for name in done:
            if same_disk:
                os.replace(os.path.join(new_files, name), os.path.join(old_files, name))
            else:
                os.remove(os.path.join(new_files, name))
        for leftover in (os.path.join(new, 'library.json'),):
            if os.path.isfile(leftover):
                os.remove(leftover)
        raise
    # Everything has arrived: the old library goes.
    if not same_disk:
        for name in names:
            os.remove(os.path.join(old_files, name))
    for leftover in (document, document + '.tmp'):
        if os.path.isfile(leftover):
            os.remove(leftover)
    for folder in (old_files, old):
        try:
            os.rmdir(folder)   # only if nothing else lies there
        except OSError:
            pass


def copy_out(source, target):
    shutil.copyfile(source, target)


def manifest_of(text):
    try:
        data = json.loads(text or 'null')
    except ValueError:
        return None
    return data if isinstance(data, dict) else None
