# Music — pictures: covers decoded off the main thread, kept small, drawn rounded.
import collections
import concurrent.futures
import os

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('Graphene', '1.0')
from gi.repository import Adw, Gdk, GdkPixbuf, GLib, Graphene, Gsk, Gtk  # noqa: E402

# A cover is decoded at one of these sizes (pixels on the short side) and kept for reuse.
BUCKETS = ((20, 12), (128, 600), (384, 40), (800, 6))


class ArtCache:
    def __init__(self):
        self._textures = [collections.OrderedDict() for _ in BUCKETS]
        self._waiting = {}
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=3)

    @staticmethod
    def _bucket(pixels):
        for index, (size, _limit) in enumerate(BUCKETS):
            if pixels <= size:
                return index
        return len(BUCKETS) - 1

    @staticmethod
    def _decode(path, size):
        try:
            _format, width, height = GdkPixbuf.Pixbuf.get_file_info(path)
            if not width or not height:
                return None
            scale = min(1.0, size / min(width, height)) if not path.endswith('.svg') else size / min(width, height)
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, max(1, round(width * scale)), max(1, round(height * scale)), False)
            return pixbuf.apply_embedded_orientation() or pixbuf
        except GLib.Error:
            return None

    @staticmethod
    def _texture(pixbuf):
        layout = Gdk.MemoryFormat.R8G8B8A8 if pixbuf.get_has_alpha() else Gdk.MemoryFormat.R8G8B8
        return Gdk.MemoryTexture.new(pixbuf.get_width(), pixbuf.get_height(), layout, pixbuf.read_pixel_bytes(), pixbuf.get_rowstride())

    def get(self, path, pixels, ready):
        """The texture if it is known, else None — and `ready(texture)` is called once it is."""
        if not path:
            return None
        bucket = self._bucket(pixels)
        try:
            key = (path, os.path.getmtime(path))
        except OSError:
            return None
        cache = self._textures[bucket]
        if key in cache:
            cache.move_to_end(key)
            return cache[key]
        waiting = self._waiting.setdefault((bucket, key), [])
        waiting.append(ready)
        if len(waiting) > 1:
            return None

        def decoded(future):
            GLib.idle_add(finish, future.result())

        def finish(pixbuf):
            callbacks = self._waiting.pop((bucket, key), [])
            if pixbuf is None:
                return False
            texture = self._texture(pixbuf)
            cache[key] = texture
            while len(cache) > BUCKETS[bucket][1]:
                cache.popitem(last=False)
            for callback in callbacks:
                callback(texture)
            return False
        self._pool.submit(self._decode, path, BUCKETS[bucket][0]).add_done_callback(decoded)
        return None


CACHE = ArtCache()


class Art(Gtk.Widget):
    """A square picture with rounded corners; the image fills it (cropped, never stretched).
    With `minimum`, it gives way down to that size when there is no room for `size`."""

    def __init__(self, size, radius=6, minimum=None, shadow=False, **properties):
        super().__init__(**properties)
        self.size = size
        self.shadow = shadow
        self.minimum = minimum or size
        self.radius = radius
        self.path = None
        self.texture = None
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        self.add_css_class('art')

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.CONSTANT_SIZE

    def do_measure(self, _orientation, _for_size):
        return self.minimum, self.size, -1, -1

    def set_path(self, path):
        if path == self.path and self.texture:
            return
        self.path = path
        wanted = path

        def ready(texture):
            if self.path == wanted:
                self.texture = texture
                self.queue_draw()
        texture = CACHE.get(path, self.size * max(1, self.get_scale_factor()), ready)
        if texture is not None or not self.texture:
            self.texture = texture
        self.queue_draw()

    def do_snapshot(self, snapshot):
        side = min(self.get_width(), self.get_height())
        if side <= 0:
            return
        snapshot.translate(Graphene.Point().init((self.get_width() - side) / 2, (self.get_height() - side) / 2))
        width = height = side
        bounds = Graphene.Rect().init(0, 0, width, height)
        outline = Gsk.RoundedRect()
        outline.init_from_rect(bounds, self.radius)
        if self.shadow:
            shade = Gdk.RGBA()
            shade.parse('rgba(0,0,0,0.3)')
            snapshot.append_outset_shadow(outline, shade, 0, 8, 0, 26)
        snapshot.push_rounded_clip(outline)
        if self.texture:
            scale = max(width / self.texture.get_width(), height / self.texture.get_height())
            w, h = self.texture.get_width() * scale, self.texture.get_height() * scale
            snapshot.append_scaled_texture(self.texture, Gsk.ScalingFilter.TRILINEAR, Graphene.Rect().init((width - w) / 2, (height - h) / 2, w, h))
        else:
            grey = Gdk.RGBA()
            grey.parse('rgba(128,128,128,0.25)')
            snapshot.append_color(grey, bounds)
        snapshot.pop()


class Backdrop(Gtk.Widget):
    """The cover, blurred across the whole view, under a veil of the window colour."""

    def __init__(self):
        super().__init__(hexpand=True, vexpand=True)
        self.path = None
        self.texture = None
        Adw.StyleManager.get_default().connect('notify::dark', lambda *_: self.queue_draw())

    def do_measure(self, _orientation, _for_size):
        return 0, 0, -1, -1

    def set_path(self, path):
        if path == self.path:
            return
        self.path = path
        wanted = path

        def ready(texture):
            if self.path == wanted:
                self.texture = texture
                self.queue_draw()
        # A handful of pixels, stretched over the window: the colours of the cover, none of its detail.
        texture = CACHE.get(path, 20, ready)
        if texture is not None:
            self.texture = texture
        self.queue_draw()

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        if width <= 0 or height <= 0:
            return
        bounds = Graphene.Rect().init(0, 0, width, height)
        dark = Adw.StyleManager.get_default().get_dark()
        veil = Gdk.RGBA()
        veil.parse('rgba(30,30,32,0.74)' if dark else 'rgba(250,250,251,0.78)')
        if self.texture:
            scale = max(width / self.texture.get_width(), height / self.texture.get_height()) * 1.3
            w, h = self.texture.get_width() * scale, self.texture.get_height() * scale
            snapshot.push_clip(bounds)
            snapshot.append_scaled_texture(self.texture, Gsk.ScalingFilter.LINEAR, Graphene.Rect().init((width - w) / 2, (height - h) / 2, w, h))
            snapshot.pop()
        snapshot.append_color(veil, bounds)
