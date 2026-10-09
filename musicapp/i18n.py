# Music — translations: one JSON file per language, looked up by key.
import json
import os

LANGUAGES = {'en': 'en-US', 'zh': 'zh-CN', 'ja': 'ja-JP', 'ko': 'ko-KR', 'ar': 'ar'}
NAMES = {'en-US': 'English', 'zh-CN': '简体中文', 'ja-JP': '日本語', 'ko-KR': '한국어', 'ar': 'العربية'}
language = 'en-US'
_directory = ''
_strings = {}
_fallback = {}


def _detect():
    # MUSIC_LANG overrides the session's language (e.g. MUSIC_LANG=zh ./music).
    for name in ('MUSIC_LANG', 'LANGUAGE', 'LC_ALL', 'LC_MESSAGES', 'LANG'):
        for part in os.environ.get(name, '').split(':'):
            code = part.strip().lower()[:2]
            if code in LANGUAGES:
                return LANGUAGES[code]
    return 'en-US'


def _read(directory, name):
    try:
        with open(os.path.join(directory, f'{name}.json'), encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def init(directory):
    global _directory, _fallback
    _directory = directory
    _fallback = _read(directory, 'en-US')
    set_language('')


def set_language(chosen):
    """One of NAMES, or '' to follow the session. Takes effect for everything drawn from now on."""
    global language, _strings
    language = chosen if chosen in NAMES else _detect()
    _strings = _read(_directory, language) if language != 'en-US' else _fallback


def is_rtl():
    return language == 'ar'


def T(key, fallback=None):
    return _strings.get(key) or _fallback.get(key) or fallback or key
