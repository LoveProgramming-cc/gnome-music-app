# Music — subtitles: reading subtitle files and choosing what is on screen.
#
# Nothing here leans on the media framework: SRT, WebVTT, ASS/SSA and LRC files are
# parsed into cues (start, end, text as Pango markup), and Tracks answers "what is shown
# at this second" for whichever track is selected. Tracks inside a video are read out
# as cues too when the video is opened (importer.embedded_subtitles), so both kinds are
# drawn the same and the player itself never touches a subtitle.
import bisect
import html
import re

MAX_BYTES = 8 * 1024 * 1024
_ENCODINGS = ('utf-8', 'gb18030', 'big5', 'shift_jis', 'euc_kr', 'cp1252')
_TAG = re.compile(r'<[^>]*>')
_KEPT = {'i', 'b', 'u'}


class Cue:
    __slots__ = ('start', 'end', 'markup')

    def __init__(self, start, end, markup):
        self.start, self.end, self.markup = start, end, markup


def decode(data):
    if data.startswith(b'\xef\xbb\xbf'):
        return data[3:].decode('utf-8', 'replace')
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        return data.decode('utf-16', 'replace')
    for encoding in _ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode('latin-1')


def to_markup(text):
    """Subtitle text with its <i>/<b>/<u> kept and everything else (fonts, classes, karaoke
    stamps) dropped: always well-formed Pango markup."""
    parts = []
    stack = []
    position = 0
    for match in _TAG.finditer(text):
        parts.append(html.escape(html.unescape(text[position:match.start()]), quote=False))
        position = match.end()
        body = match.group()[1:-1].strip().lower()
        closing = body.startswith('/')
        name = re.split(r'[\s.]', body.lstrip('/'), maxsplit=1)[0]
        if name not in _KEPT:
            continue
        if not closing:
            stack.append(name)
            parts.append(f'<{name}>')
        elif name in stack:
            while stack:
                last = stack.pop()
                parts.append(f'</{last}>')
                if last == name:
                    break
    parts.append(html.escape(html.unescape(text[position:]), quote=False))
    parts.extend(f'</{name}>' for name in reversed(stack))
    lines = [line.strip() for line in ''.join(parts).split('\n')]
    return '\n'.join(line for line in lines if line)


def _seconds(text):
    """'01:02:03,450', '02:03.45' or '3.5' as seconds."""
    parts = text.strip().replace(',', '.').split(':')
    try:
        value = 0.0
        for part in parts:
            value = value * 60 + float(part)
        return value
    except ValueError:
        return None


_ARROW = re.compile(r'((?:\d+:)?\d+:\d+(?:[.,]\d+)?)\s*-->\s*((?:\d+:)?\d+:\d+(?:[.,]\d+)?)')


def _parse_blocks(text):
    """SRT and WebVTT: blocks of lines, one of which is 'start --> end'."""
    cues = []
    for block in re.split(r'\n[ \t]*\n', text):
        lines = block.strip('\n').split('\n')
        for index, line in enumerate(lines):
            match = _ARROW.search(line)
            if not match:
                continue
            start, end = _seconds(match.group(1)), _seconds(match.group(2))
            body = to_markup('\n'.join(lines[index + 1:]))
            if start is not None and end is not None and end > start and body:
                cues.append(Cue(start, end, body))
            break
    return cues


_ASS_OVERRIDE = re.compile(r'\{[^}]*\}')


def _parse_ass(text):
    cues = []
    fields = ['layer', 'start', 'end', 'style', 'name', 'marginl', 'marginr', 'marginv', 'effect', 'text']
    in_events = False
    for line in text.split('\n'):
        line = line.strip()
        if line.startswith('['):
            in_events = line.lower() == '[events]'
            continue
        if not in_events:
            continue
        kind, _, rest = line.partition(':')
        if kind.lower() == 'format':
            fields = [name.strip().lower() for name in rest.split(',')]
        elif kind.lower() == 'dialogue' and 'text' in fields:
            values = rest.split(',', len(fields) - 1)
            if len(values) < len(fields):
                continue
            row = dict(zip(fields, values))
            start, end = _seconds(row.get('start', '')), _seconds(row.get('end', ''))
            raw = row['text']
            if re.search(r'\\p[1-9]', raw):
                continue   # a vector drawing, not words
            raw = raw.replace('{\\i1}', '<i>').replace('{\\i0}', '</i>').replace('{\\b1}', '<b>').replace('{\\b0}', '</b>')
            raw = _ASS_OVERRIDE.sub('', raw).replace('\\N', '\n').replace('\\n', '\n').replace('\\h', ' ')
            body = to_markup(raw)
            if start is not None and end is not None and end > start and body:
                cues.append(Cue(start, end, body))
    return cues


_LRC_STAMP = re.compile(r'\[(\d+):(\d+(?:[.:]\d+)?)\]')
_LRC_OFFSET = re.compile(r'\[offset:\s*([+-]?\d+)\s*\]', re.I)
_LRC_WORD = re.compile(r'<\d+:\d+(?:[.:]\d+)?>')


def _parse_lrc(text):
    offset = 0.0
    found = _LRC_OFFSET.search(text)
    if found:
        offset = int(found.group(1)) / 1000
    lines = []
    for line in text.split('\n'):
        stamps = []
        position = 0
        while True:
            match = _LRC_STAMP.match(line, position)
            if not match:
                break
            stamps.append(int(match.group(1)) * 60 + float(match.group(2).replace(':', '.')))
            position = match.end()
        if not stamps:
            continue
        words = _LRC_WORD.sub('', line[position:]).strip()
        for stamp in stamps:
            lines.append((max(0.0, stamp - offset), words))
    lines.sort(key=lambda entry: entry[0])
    cues = []
    for index, (start, words) in enumerate(lines):
        # A line stays until the next one; an empty line clears the screen.
        end = lines[index + 1][0] if index + 1 < len(lines) else start + 10
        body = to_markup(words)
        if body and end > start:
            cues.append(Cue(start, end, body))
    return cues


def parse(text):
    """The cues of a subtitle file, whatever its format; [] when there are none."""
    text = text.replace('\r\n', '\n').replace('\r', '\n').lstrip('﻿')
    head = text.lstrip()[:2000].lower()
    if '[script info]' in head or re.search(r'^\s*dialogue:', text, re.I | re.M):
        cues = _parse_ass(text)
    elif _ARROW.search(text):
        cues = _parse_blocks(text)
    else:
        cues = _parse_lrc(text)
    cues.sort(key=lambda cue: cue.start)
    return cues


def load(path):
    with open(path, 'rb') as handle:
        data = handle.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES or b'\x00\x00\x00' in data[:4096]:
        return []
    return parse(decode(data))


class Tracks:
    """The subtitle tracks of the video that is playing, and the one that is shown.

    `items` is what the menu lists: the tracks inside the video first, then the files
    added for this song — each a label and its cues. `selected` is an index, or -1 for off."""

    def __init__(self):
        self.items = []
        self.selected = -1
        self._starts = []

    def reset(self):
        self.items = []
        self.selected = -1
        self._starts = []

    def set_items(self, tracks):
        self.items = [{'label': label, 'cues': cues} for label, cues in tracks]

    def add_file(self, label, cues):
        self.items.append({'label': label, 'cues': cues})
        return len(self.items) - 1

    def select(self, index):
        self.selected = index if 0 <= index < len(self.items) else -1
        track = self.current()
        self._starts = [cue.start for cue in track['cues']] if track else []

    def current(self):
        return self.items[self.selected] if 0 <= self.selected < len(self.items) else None

    def markup_at(self, seconds):
        track = self.current()
        if not track:
            return ''
        # Cues may overlap (two speakers): everything that spans this moment, in order.
        last = bisect.bisect_right(self._starts, seconds)
        return '\n'.join(cue.markup for cue in track['cues'][max(0, last - 6):last] if cue.end > seconds)
