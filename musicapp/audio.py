# Music — the sound (and picture): two GStreamer players, so one song can fade into the next.
import math
import os

import gi
gi.require_version('Gst', '1.0')
from gi.repository import GLib, GObject, Gst  # noqa: E402

FLAG_VIDEO, FLAG_AUDIO, FLAG_TEXT, FLAG_SOFT_VOLUME, FLAG_DEINTERLACE, FLAG_SOFT_COLORBALANCE = 0x1, 0x2, 0x4, 0x10, 0x200, 0x400


class Engine:
    def __init__(self, audio, index):
        self.audio = audio
        self.playbin = Gst.ElementFactory.make('playbin', f'player{index}')
        self.gain = 1.0            # 0…1, where a fade currently is
        self.video = False
        self.loaded = False
        self.ended = False
        self.held = False          # the pipeline itself is paused
        self.duration = 0.0
        self.tracks_known = False
        self._fade = 0
        sink = os.environ.get('MUSIC_AUDIO_SINK')
        if sink:
            self.playbin.set_property('audio-sink', Gst.parse_bin_from_description(sink, True))
        self.paintable = None
        picture = Gst.ElementFactory.make('gtk4paintablesink')
        if picture:
            self.paintable = picture.get_property('paintable')
            video_sink = picture
            if self.paintable.get_property('gl-context'):
                wrapper = Gst.ElementFactory.make('glsinkbin')
                if wrapper:
                    wrapper.set_property('sink', picture)
                    video_sink = wrapper
            self.playbin.set_property('video-sink', video_sink)
        bus = self.playbin.get_bus()
        bus.add_signal_watch()
        bus.connect('message', self._on_message)

    def apply_volume(self):
        # Equal power: two songs crossing keep their loudness in the middle.
        self.playbin.set_property('volume', max(0.0, min(1.0, self.audio.volume)) * math.sin(self.gain * math.pi / 2))

    def cancel_fade(self):
        if self._fade:
            GLib.source_remove(self._fade)
            self._fade = 0

    def fade_to(self, target, seconds, done=None):
        self.cancel_fade()
        if seconds <= 0:
            self.gain = target
            self.apply_volume()
            if done:
                done()
            return
        start = self.gain
        began = GLib.get_monotonic_time()

        def step():
            progress = min(1.0, (GLib.get_monotonic_time() - began) / 1e6 / seconds)
            self.gain = start + (target - start) * progress
            self.apply_volume()
            if progress < 1:
                return True
            self._fade = 0
            if done:
                done()
            return False
        self._fade = GLib.timeout_add(25, step)

    def load(self, path, video):
        self.cancel_fade()
        self.playbin.set_state(Gst.State.NULL)
        self.video = video
        self.duration = 0.0
        self.ended = False
        self.held = False
        self.tracks_known = False
        self.playbin.set_property('uri', Gst.filename_to_uri(path))
        flags = FLAG_AUDIO | FLAG_SOFT_VOLUME
        if video:
            flags |= FLAG_VIDEO | FLAG_DEINTERLACE | FLAG_SOFT_COLORBALANCE   # no text: subtitles are the app's own business
        self.playbin.set_property('flags', flags)
        self.apply_volume()
        self.loaded = True
        self.playbin.set_state(Gst.State.PLAYING)

    def stop(self):
        self.cancel_fade()
        if self.loaded:
            self.playbin.set_state(Gst.State.NULL)
        self.loaded = False
        self.ended = False

    def position(self):
        ok, value = self.playbin.query_position(Gst.Format.TIME)
        return value / Gst.SECOND if ok else None

    def seek(self, seconds):
        self.ended = False
        return self.playbin.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE, max(0, int(seconds * Gst.SECOND)))

    def _on_message(self, _bus, message):
        kind = message.type
        if kind == Gst.MessageType.EOS:
            self.ended = True
            self.audio._ended(self)
        elif kind == Gst.MessageType.ERROR:
            error, _debug = message.parse_error()
            self.stop()
            self.audio._failed(self, error.message)
        elif kind in (Gst.MessageType.ASYNC_DONE, Gst.MessageType.DURATION_CHANGED):
            ok, value = self.playbin.query_duration(Gst.Format.TIME)
            duration = value / Gst.SECOND if ok and value > 0 else 0.0
            first = kind == Gst.MessageType.ASYNC_DONE and not self.tracks_known
            if first:
                self.tracks_known = True
            if duration and abs(duration - self.duration) > 0.05 or first:
                self.duration = duration or self.duration
                self.audio._ready(self, first)


class Audio(GObject.Object):
    """What the rest of the app plays through. `event` says what happened: play, pause,
    timeupdate, loadedmetadata, seeked, ended, error (with the message)."""

    __gsignals__ = {
        'event': (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        'paintable': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self):
        super().__init__()
        Gst.init(None)
        self.volume = 0.8
        self.paused = True
        self.time = 0.0
        self.duration = 0.0
        self.engines = [Engine(self, 0), Engine(self, 1)]
        self.active = self.engines[0]
        self._timer = 0

    @property
    def paintable(self):
        return self.active.paintable

    def _other(self, engine):
        return self.engines[1 - self.engines.index(engine)]

    def _tick(self):
        if self.paused or not self.active.loaded:
            self._timer = 0
            return False
        position = self.active.position()
        if position is not None:
            self.time = position
            self.emit('event', 'timeupdate', '')
        return True

    def _run_timer(self):
        if not self._timer:
            self._timer = GLib.timeout_add(200, self._tick)

    def load(self, path, crossfade=0, video=False):
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        old = self.active
        new = old
        if crossfade > 0 and old.loaded and not self.paused:
            new = self._other(old)
            new.stop()
            old.fade_to(0, crossfade, old.stop)
            new.gain = 0.0
            new.load(path, video)
            new.fade_to(1, crossfade)
        else:
            self._other(old).stop()
            old.gain = 1.0
            old.load(path, video)
        self.active = new
        self.paused = False
        self.time = 0.0
        self.duration = 0.0
        self._run_timer()
        if new is not old:
            self.emit('paintable')
        self.emit('event', 'play', '')

    def play(self, fade=0):
        engine = self.active
        if not engine.loaded:
            return
        self.paused = False
        engine.cancel_fade()
        if engine.ended:
            engine.seek(0)
            self.time = 0.0
        if fade > 0:
            if engine.held:
                engine.gain = 0.0
            engine.apply_volume()
            engine.playbin.set_state(Gst.State.PLAYING)
            engine.fade_to(1, fade)
        else:
            engine.gain = 1.0
            engine.apply_volume()
            engine.playbin.set_state(Gst.State.PLAYING)
        engine.held = False
        self._run_timer()
        self.emit('event', 'play', '')

    def pause(self, fade=0):
        engine = self.active
        if not engine.loaded:
            return
        self.paused = True
        self._other(engine).stop()

        def hold():
            engine.playbin.set_state(Gst.State.PAUSED)
            engine.held = True
        if fade > 0:
            engine.fade_to(0, fade, hold)
        else:
            engine.cancel_fade()
            hold()
        self.emit('event', 'pause', '')

    def stop(self):
        for engine in self.engines:
            engine.stop()
        self.paused = True
        self.time = 0.0
        self.duration = 0.0

    def seek(self, seconds):
        if not self.active.loaded:
            return
        self.active.seek(seconds)
        self.time = max(0.0, float(seconds))
        self.emit('event', 'seeked', '')

    def set_volume(self, volume):
        self.volume = max(0.0, min(1.0, float(volume)))
        for engine in self.engines:
            engine.apply_volume()

    # -- from the engines
    def _ended(self, engine):
        if engine is not self.active:
            return
        self.paused = True
        self.emit('event', 'ended', '')

    def _failed(self, engine, message):
        if engine is not self.active:
            return
        self.paused = True
        self.emit('event', 'error', message or '')

    def _ready(self, engine, first):
        if engine is not self.active:
            return
        self.duration = engine.duration
        self.emit('event', 'loadedmetadata', '')
