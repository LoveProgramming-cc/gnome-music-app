// Music — the top-bar item: what is playing, with its cover, the transport controls and
// the place in the song, in a panel under the item. It talks to the app over MPRIS and is
// only there while Music runs (and while "Show in the top bar" is on in its Settings).
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import * as Slider from 'resource:///org/gnome/shell/ui/slider.js';

const APP = 'local.koen.Music';
const MPRIS = `org.mpris.MediaPlayer2.${APP}`;
const MPRIS_PATH = '/org/mpris/MediaPlayer2';
const ROOT = 'org.mpris.MediaPlayer2';
const PLAYER = 'org.mpris.MediaPlayer2.Player';
const PROPERTIES = 'org.freedesktop.DBus.Properties';
const TOPBAR = `${APP}.TopBar`;
const TOPBAR_PATH = '/local/koen/Music/TopBar';
const PANEL_NAME = `${APP}.ShellPanel`;
const NOT_PLAYING = {zh: '未在播放', ja: '再生停止中', ko: '재생 중이 아님', ar: 'لا يوجد تشغيل'};

const clock = seconds => {
    const total = Math.max(0, Math.round(seconds || 0));
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
};

const MusicIndicator = GObject.registerClass(
class MusicIndicator extends PanelMenu.Button {
    _init(extension) {
        super._init(0.5, 'Music');
        this._bus = Gio.DBus.session;
        this._state = {};
        this._enabled = true;
        this._running = false;
        this._position = 0;
        this._dragging = false;
        this._ticker = 0;
        this._subscriptions = [];
        const language = GLib.get_language_names()[0]?.slice(0, 2) ?? 'en';
        this._notPlaying = NOT_PLAYING[language] ?? 'Not Playing';

        this.add_child(new St.Icon({gicon: Gio.icon_new_for_string(`${extension.path}/music-symbolic.svg`), style_class: 'system-status-icon'}));

        this._cover = new St.Button({style_class: 'music-panel-cover', y_align: Clutter.ActorAlign.CENTER});
        this._cover.connect('clicked', () => { this._call(ROOT, 'Raise'); this.menu.close(); });
        this._title = new St.Label({style_class: 'music-panel-title'});
        this._artist = new St.Label({style_class: 'music-panel-artist', text: ' '});
        for (const label of [this._title, this._artist])
            label.clutter_text.ellipsize = 3;   // Pango.EllipsizeMode.END
        const button = (icon, size, method) => {
            const control = new St.Button({style_class: 'music-panel-button', can_focus: true, child: new St.Icon({icon_name: icon, icon_size: size})});
            control.connect('clicked', () => this._call(PLAYER, method));
            return control;
        };
        this._previous = button('media-skip-backward-symbolic', 18, 'Previous');
        this._toggle = button('media-playback-start-symbolic', 24, 'PlayPause');
        this._next = button('media-skip-forward-symbolic', 18, 'Next');
        const controls = new St.BoxLayout({style_class: 'music-panel-controls', x_align: Clutter.ActorAlign.CENTER});
        for (const control of [this._previous, this._toggle, this._next])
            controls.add_child(control);
        // The slider: the time follows the thumb, the song follows when it is let go.
        this._slider = new Slider.Slider(0);
        this._slider.connect('drag-begin', () => { this._dragging = true; });
        this._slider.connect('drag-end', () => { this._dragging = false; this._seek(); });
        this._slider.connect('notify::value', () => { if (this._dragging) this._showTime(this._slider.value * this._duration()); });
        this._elapsed = new St.Label({style_class: 'music-panel-time', x_expand: true});
        this._left = new St.Label({style_class: 'music-panel-time'});
        const times = new St.BoxLayout();
        times.add_child(this._elapsed);
        times.add_child(this._left);
        const side = new St.BoxLayout({vertical: true, x_expand: true, style_class: 'music-panel-side', y_align: Clutter.ActorAlign.CENTER});
        for (const child of [this._title, this._artist, controls, this._slider, times])
            side.add_child(child);
        const box = new St.BoxLayout({style_class: 'music-panel', x_expand: true});
        box.add_child(this._cover);
        box.add_child(side);
        const item = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
        item.add_child(box);
        this.menu.addMenuItem(item);
        this.menu.connect('open-state-changed', (_menu, open) => { if (open) this._readPosition(); this._tick(); });

        this._watch = Gio.bus_watch_name(Gio.BusType.SESSION, MPRIS, Gio.BusNameWatcherFlags.NONE, () => this._appeared(), () => this._vanished());
        this._render();
    }

    _call(iface, method, parameters = null) {
        this._bus.call(MPRIS, MPRIS_PATH, iface, method, parameters, null, Gio.DBusCallFlags.NO_AUTO_START, -1, null, (bus, result) => {
            try { bus.call_finish(result); } catch (_error) { /* the app has gone */ }
        });
    }

    _get(name, path, iface, done) {
        this._bus.call(name, path, PROPERTIES, 'GetAll', new GLib.Variant('(s)', [iface]), null, Gio.DBusCallFlags.NO_AUTO_START, -1, null, (bus, result) => {
            try { done(bus.call_finish(result).recursiveUnpack()[0]); } catch (_error) { /* not there */ }
        });
    }

    _appeared() {
        this._vanished();
        this._running = true;
        const changed = (path, apply) => this._bus.signal_subscribe(null, PROPERTIES, 'PropertiesChanged', path, null, Gio.DBusSignalFlags.NONE,
            (_bus, _sender, _path, _iface, _signal, parameters) => { const [iface, values] = parameters.recursiveUnpack(); apply(iface, values); });
        this._subscriptions = [
            changed(MPRIS_PATH, (iface, values) => { if (iface === PLAYER) { Object.assign(this._state, values); this._render(); this._readPosition(); } }),
            changed(TOPBAR_PATH, (iface, values) => { if (iface === TOPBAR && 'Enabled' in values) { this._enabled = values.Enabled; this._render(); } }),
            this._bus.signal_subscribe(null, PLAYER, 'Seeked', MPRIS_PATH, null, Gio.DBusSignalFlags.NONE,
                (_bus, _sender, _path, _iface, _signal, parameters) => { this._position = Number(parameters.recursiveUnpack()[0]) / 1e6; this._showPosition(); }),
        ];
        this._get(MPRIS, MPRIS_PATH, PLAYER, values => { this._state = values; this._position = Number(values.Position ?? 0) / 1e6; this._render(); });
        this._get(APP, TOPBAR_PATH, TOPBAR, values => { this._enabled = values.Enabled !== false; this._render(); });
        this._render();
    }

    _vanished() {
        for (const id of this._subscriptions)
            this._bus.signal_unsubscribe(id);
        this._subscriptions = [];
        this._running = false;
        this._state = {};
        this._position = 0;
        this._render();
    }

    _duration() {
        return Number(this._state.Metadata?.['mpris:length'] ?? 0) / 1e6;
    }

    _playing() {
        return this._state.PlaybackStatus === 'Playing';
    }

    _render() {
        this.visible = this._running && this._enabled;
        if (!this.visible && this.menu.isOpen)
            this.menu.close();
        const metadata = this._state.Metadata ?? {};
        const title = metadata['xesam:title'];
        this._title.text = title || this._notPlaying;
        this._artist.text = [(metadata['xesam:artist'] ?? []).join(', '), metadata['xesam:album']].filter(Boolean).join(' — ') || ' ';
        const art = metadata['mpris:artUrl'];
        this._cover.style = art ? `background-image: url("${art}");` : '';
        this._toggle.child.icon_name = this._playing() ? 'media-playback-pause-symbolic' : 'media-playback-start-symbolic';
        this._previous.reactive = this._next.reactive = Boolean(title);
        this._slider.reactive = Boolean(title) && this._duration() > 0;
        this._showPosition();
        this._tick();
    }

    _showTime(seconds) {
        const duration = this._duration();
        this._elapsed.text = clock(seconds);
        this._left.text = duration ? `-${clock(Math.max(0, duration - seconds))}` : '';
    }

    _showPosition() {
        if (this._dragging)
            return;
        const duration = this._duration();
        this._slider.value = duration ? Math.min(1, this._position / duration) : 0;
        this._showTime(this._position);
    }

    _seek() {
        const id = this._state.Metadata?.['mpris:trackid'];
        if (!id)
            return;
        this._position = this._slider.value * this._duration();
        this._call(PLAYER, 'SetPosition', new GLib.Variant('(ox)', [id, Math.round(this._position * 1e6)]));
        this._showPosition();
    }

    _readPosition() {
        if (!this._running)
            return;
        this._bus.call(MPRIS, MPRIS_PATH, PROPERTIES, 'Get', new GLib.Variant('(ss)', [PLAYER, 'Position']), null, Gio.DBusCallFlags.NO_AUTO_START, -1, null, (bus, result) => {
            try {
                this._position = Number(bus.call_finish(result).recursiveUnpack()[0]) / 1e6;
                this._showPosition();
            } catch (_error) { /* the app has gone */ }
        });
    }

    // The place in the song, once a second — only while the panel is open and music plays.
    _tick() {
        const wanted = this.menu.isOpen && this._running && this._playing();
        if (wanted && !this._ticker) {
            this._ticker = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 1, () => { this._readPosition(); return GLib.SOURCE_CONTINUE; });
        } else if (!wanted && this._ticker) {
            GLib.source_remove(this._ticker);
            this._ticker = 0;
        }
    }

    destroy() {
        if (this._ticker)
            GLib.source_remove(this._ticker);
        this._ticker = 0;
        Gio.bus_unwatch_name(this._watch);
        for (const id of this._subscriptions)
            this._bus.signal_unsubscribe(id);
        this._subscriptions = [];
        super.destroy();
    }
});

export default class MusicTopBarExtension extends Extension {
    enable() {
        this._indicator = new MusicIndicator(this);
        Main.panel.addToStatusArea(this.uuid, this._indicator);
        // Tells Music that the panel is here, so its plain top-bar item steps aside.
        this._name = Gio.bus_own_name(Gio.BusType.SESSION, PANEL_NAME, Gio.BusNameOwnerFlags.NONE, null, null, null);
    }

    disable() {
        Gio.bus_unown_name(this._name);
        this._name = 0;
        this._indicator?.destroy();
        this._indicator = null;
    }
}
