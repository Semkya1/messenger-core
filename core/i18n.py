"""Переводы (i18n).

Все строки лежат в static/locales/<код>.json — один файл на язык, общий для сервера
и клиента. Чтобы добавить язык, скопируйте en.json, переведите значения и перезапустите сервер.

Ключи вида "err.*" — ошибки сервера, "sys.*" — системные сообщения в чатах,
остальные — интерфейс. Параметры подставляются так: "Привет, {name}".
"""
import json
import os
import re
import threading

LOCALES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "locales")
DEFAULT_LANG = os.environ.get("DEFAULT_LANG", "en")

_strings = {}            # код языка -> {ключ: строка}
_local = threading.local()
_PARAM = re.compile(r"\{(\w+)\}")


def load():
    _strings.clear()
    if not os.path.isdir(LOCALES_DIR):
        return
    for filename in sorted(os.listdir(LOCALES_DIR)):
        if filename.endswith(".json"):
            with open(os.path.join(LOCALES_DIR, filename), encoding="utf-8") as f:
                _strings[filename[:-5]] = json.load(f)


def add(translations):
    """Добавить строки (например, из плагина): {"en": {...}, "ru": {...}}."""
    for lang, mapping in translations.items():
        _strings.setdefault(lang, {}).update(mapping)


def languages():
    """[{code, name}] — языки, для которых есть файл с ключом "_name"."""
    return [{"code": code, "name": s.get("_name", code)} for code, s in sorted(_strings.items()) if "_name" in s]


def pick(header):
    """Выбрать язык по заголовку вроде 'ru-RU,ru;q=0.9,en;q=0.8'."""
    for part in (header or "").split(","):
        code = part.split(";")[0].strip().lower()
        for candidate in (code, code.split("-")[0]):
            if candidate in _strings and "_name" in _strings[candidate]:
                return candidate
    return DEFAULT_LANG


def set_lang(lang):
    _local.lang = lang


def current_lang():
    return getattr(_local, "lang", DEFAULT_LANG)


def t(key, lang=None, **params):
    """Перевести ключ. Если перевода нет — язык по умолчанию, английский, затем сам ключ."""
    lang = lang or current_lang()
    for source in (lang, DEFAULT_LANG, "en"):
        text = _strings.get(source, {}).get(key)
        if text is not None:
            break
    else:
        text = key
    return _PARAM.sub(lambda m: str(params.get(m.group(1), m.group(0))), text)


def bundle(lang):
    """Все строки языка с подстановкой недостающих из английского и языка по умолчанию."""
    merged = {}
    for source in ("en", DEFAULT_LANG, lang):
        merged.update(_strings.get(source, {}))
    return merged
