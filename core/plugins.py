"""Система плагинов.

Плагин — это файл plugins/<имя>.py с функцией setup(api). Через `api` плагин
может: подписываться на события ядра, менять/блокировать сообщения, добавлять
/команды, ботов, свои HTTP-эндпоинты, таблицы в БД и JS-код для клиента.

Файлы, начинающиеся с "_", не загружаются (например, _template.py).
"""
import importlib.util
import os
import secrets
import traceback


class PluginError(Exception):
    """Бросьте из хука `message:before`, чтобы отклонить сообщение с понятной причиной."""


class CommandContext:
    """Передаётся в обработчик /команды."""

    def __init__(self, app, message, name, args):
        self.app = app
        self.message = message          # уже отправленное сообщение с командой
        self.chat_id = message["chat_id"]
        self.user = message["sender"]   # кто вызвал команду
        self.command = name             # "dice"
        self.args = args                # строка после команды

    def reply(self, text, bot=None):
        """Ответить в тот же чат: от имени бота (bot=dict из api.create_bot) или системным сообщением."""
        if bot:
            return self.app.post_message(self.chat_id, bot["id"], text, run_hooks=False)
        return self.app.post_message(self.chat_id, None, text, type="system", run_hooks=False)


class PluginAPI:
    """Всё, что доступно плагину. Один экземпляр на плагин."""

    def __init__(self, manager, name):
        self._m = manager
        self.name = name

    # --- доступ к ядру -------------------------------------------------
    @property
    def app(self):
        """Ядро целиком (core.app.Messenger) — для продвинутых случаев."""
        return self._m.app

    @property
    def db(self):
        """База данных: api.db.execute / one / all. Можно создавать свои таблицы."""
        return self._m.app.db

    def log(self, *args):
        print(f"[{self.name}]", *args, flush=True)

    # --- события -------------------------------------------------------
    def on(self, event):
        """Декоратор подписки на событие ядра.

        Фильтры (должны вернуть значение):
          message:before(draft)  -> draft | None (None или PluginError = блокировка)
          message:render(msg)    -> msg   (добавить поля в сообщение для клиента)
        Уведомления (возвращаемое значение игнорируется):
          message:after(msg), message:edited(msg), message:deleted(row),
          user:registered(user), user:updated(user),
          chat:created(chat), chat:member_added(chat_id, user)
        """
        def deco(fn):
            self._m.hooks.setdefault(event, []).append((self.name, fn))
            return fn
        return deco

    def command(self, name, description=""):
        """Декоратор /команды. Обработчик получает CommandContext."""
        def deco(fn):
            self._m.commands[name.lower()] = {"fn": fn, "description": description, "plugin": self.name}
            return fn
        return deco

    def route(self, method, path, auth=True, raw=False):
        """Декоратор своего HTTP-эндпоинта. Путь может содержать {параметры}.

        Обработчик: fn(req, **params) -> dict | (status, dict).
        req.user, req.body (JSON), req.query, req.params, req.headers.
        raw=True — тело не парсится как JSON, а лежит в req.raw (bytes), например для файлов.
        """
        def deco(fn):
            self._m.app.add_route(method, path, fn, auth, raw)
            return fn
        return deco

    def client_script(self, filename):
        """Подключить JS-файл из папки plugins/ в клиент (там доступен window.Messenger)."""
        self._m.client_scripts.append(filename)

    def client_style(self, filename):
        """Подключить CSS-файл из папки plugins/."""
        self._m.client_styles.append(filename)

    # --- ошибки ---------------------------------------------------------
    def reject(self, message):
        """raise api.reject('...') внутри message:before — отклонить сообщение."""
        return PluginError(message)

    def error(self, status, message):
        """raise api.error(404, '...') внутри своего эндпоинта."""
        from .app import HttpError
        return HttpError(status, message)

    # --- действия ------------------------------------------------------
    def create_bot(self, username, display_name):
        """Создать (или найти) пользователя-бота. Возвращает dict пользователя."""
        user = self.db.one("SELECT * FROM users WHERE username = ?", (username,))
        if user:
            return user
        return self.app.create_user(username, secrets.token_hex(16), display_name, is_bot=True)

    def get_user(self, user_id):
        return self.app.get_user(user_id)

    def is_member(self, chat_id, user_id):
        return self.app.is_member(chat_id, user_id)

    def open_private_chat(self, user_a, user_b):
        """Найти или создать личный чат между двумя пользователями. Возвращает chat_id."""
        return self.app.get_or_create_private(user_a, user_b)

    def send_message(self, chat_id, text, bot=None):
        """Отправить сообщение от бота (или системное, если bot=None)."""
        if bot:
            return self.app.post_message(chat_id, bot["id"], text, run_hooks=False)
        return self.app.post_message(chat_id, None, text, type="system", run_hooks=False)

    def update_message(self, message_id):
        """Перерисовать сообщение у всех участников чата (после изменения ваших данных)."""
        self.app.refresh_message(message_id)

    def emit_to_chat(self, chat_id, event, data):
        """Отправить своё событие всем участникам чата. В клиенте: Messenger.on(event, fn)."""
        self.app.emit_to_chat(chat_id, event, data)

    def emit_to_user(self, user_id, event, data):
        self.app.hub.send(user_id, event, data)


class PluginManager:
    def __init__(self, app):
        self.app = app
        self.hooks = {}
        self.commands = {}
        self.client_scripts = []
        self.client_styles = []
        self.loaded = []

    def load_all(self, directory):
        if not os.path.isdir(directory):
            return
        for filename in sorted(os.listdir(directory)):
            if not filename.endswith(".py") or filename.startswith("_"):
                continue
            path = os.path.join(directory, filename)
            try:
                spec = importlib.util.spec_from_file_location(f"plugins.{filename[:-3]}", path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                name = getattr(module, "NAME", filename[:-3])
                if not hasattr(module, "setup"):
                    print(f"[plugins] {filename}: нет функции setup(api), пропускаю")
                    continue
                module.setup(PluginAPI(self, name))
                self.loaded.append(name)
                print(f"[plugins] загружен: {name}")
            except Exception:
                print(f"[plugins] ошибка при загрузке {filename}:")
                traceback.print_exc()

    def emit(self, event, *args):
        """Уведомить всех подписчиков. Ошибка одного плагина не ломает остальные."""
        for name, fn in self.hooks.get(event, []):
            try:
                fn(*args)
            except Exception:
                print(f"[plugins] ошибка в {name} ({event}):")
                traceback.print_exc()

    def filter(self, event, value, blockable=False):
        """Прогнать значение через цепочку фильтров."""
        for name, fn in self.hooks.get(event, []):
            try:
                result = fn(value)
            except PluginError:
                raise
            except Exception:
                print(f"[plugins] ошибка в {name} ({event}):")
                traceback.print_exc()
                continue
            if result is None:
                if blockable:
                    return None
                continue
            value = result
        return value

    def run_command(self, message):
        """Если сообщение — известная /команда, выполнить её."""
        text = message["text"].strip()
        if not text.startswith("/"):
            return
        head, _, args = text.partition(" ")
        name = head[1:].split("@")[0].lower()
        cmd = self.commands.get(name)
        if not cmd:
            return
        ctx = CommandContext(self.app, message, name, args.strip())
        try:
            result = cmd["fn"](ctx)
            if isinstance(result, str):
                ctx.reply(result)
        except Exception:
            print(f"[plugins] ошибка в команде /{name}:")
            traceback.print_exc()
            ctx.reply(f"Команда /{name} завершилась с ошибкой")
