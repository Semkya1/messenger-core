"""Ядро мессенджера: пользователи, профили, чаты, сообщения, файлы, HTTP API и realtime.

Работает только на стандартной библиотеке Python — ничего устанавливать не нужно.
"""
import hashlib
import hmac
import json
import mimetypes
import os
import queue
import re
import secrets
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import i18n
from .db import DB
from .events import Hub
from .plugins import PluginError, PluginManager

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")
PLUGINS_DIR = os.path.join(BASE_DIR, "plugins")

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,32}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{10,64}$")
IMAGE_MIME = re.compile(r"^image/(png|jpeg|gif|webp)$")
VIDEO_MIME = re.compile(r"^video/(mp4|webm|ogg|quicktime)$")
INLINE_MIME = re.compile(r"^(image/(png|jpeg|gif|webp)|audio/[\w.+-]+|video/(mp4|webm|ogg|quicktime))$")
MAX_TEXT = 4000
MAX_JSON = 1_000_000
MAX_UPLOAD = int(os.environ.get("MAX_UPLOAD_MB", "25")) * 1024 * 1024

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".json": "application/json; charset=utf-8",
}


class HttpError(Exception):
    """Ошибка для клиента. `key` — ключ перевода (err.*) или готовый текст."""

    def __init__(self, status, key, **params):
        super().__init__(key)
        self.status = status
        self.key = key
        self.params = params

    @property
    def message(self):
        return i18n.t(self.key, **self.params)


class Request:
    def __init__(self, method, path, query, body, user, params, headers, raw=None, token=None):
        self.method = method
        self.path = path
        self.query = query
        self.body = body          # dict из JSON
        self.raw = raw            # bytes (для эндпоинтов с raw=True, например загрузки файлов)
        self.user = user
        self.params = params
        self.headers = headers
        self.token = token


class Messenger:
    """Вся бизнес-логика. Плагины обращаются к ней через PluginAPI."""

    def __init__(self, db_path, upload_dir):
        self.upload_dir = upload_dir
        os.makedirs(upload_dir, exist_ok=True)
        self.db = DB(db_path)
        self.hub = Hub()
        self.plugins = PluginManager(self)
        self.routes = []
        self._register_core_routes()

    # ================================================================ роутинг
    def add_route(self, method, path, fn, auth=True, raw=False):
        pattern = re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", path)
        self.routes.append((method.upper(), re.compile(pattern), fn, auth, raw))

    def route(self, method, path, auth=True, raw=False):
        def deco(fn):
            self.add_route(method, path, fn, auth, raw)
            return fn
        return deco

    # ================================================================ пользователи
    @staticmethod
    def hash_password(password):
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
        return salt.hex() + "$" + digest.hex()

    @staticmethod
    def check_password(password, stored):
        salt_hex, digest_hex = stored.split("$")
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
        return hmac.compare_digest(digest.hex(), digest_hex)

    def create_user(self, username, password, display_name, is_bot=False):
        cur = self.db.execute(
            "INSERT INTO users (username, display_name, password_hash, is_bot, created_at) VALUES (?,?,?,?,?)",
            (username, display_name, self.hash_password(password), int(is_bot), time.time()),
        )
        return self.get_user(cur.lastrowid)

    def get_user(self, user_id):
        return self.db.one("SELECT * FROM users WHERE id = ?", (user_id,))

    def user_public(self, user):
        if not user:
            return None
        return {
            "id": user["id"],
            "username": user["username"],
            "display_name": user["display_name"],
            "bio": user.get("bio") or "",
            "avatar": f"/files/{user['avatar']}" if user.get("avatar") else None,
            "is_bot": bool(user["is_bot"]),
            "online": bool(user["is_bot"]) or self.hub.is_online(user["id"]),
            "last_seen": user.get("last_seen"),
        }

    def user_by_token(self, token):
        if not token:
            return None
        return self.db.one(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ?", (token,)
        )

    def new_session(self, user_id):
        token = secrets.token_urlsafe(32)
        self.db.execute("INSERT INTO sessions (token, user_id, created_at) VALUES (?,?,?)",
                        (token, user_id, time.time()))
        return token

    def contacts_of(self, user_id):
        rows = self.db.all(
            """SELECT DISTINCT m2.user_id FROM chat_members m1
               JOIN chat_members m2 ON m1.chat_id = m2.chat_id
               WHERE m1.user_id = ? AND m2.user_id != ?""",
            (user_id, user_id),
        )
        return [r["user_id"] for r in rows]

    # ================================================================ файлы
    def save_upload(self, owner_id, name, mime, data):
        token = secrets.token_urlsafe(24)
        with open(os.path.join(self.upload_dir, token), "wb") as f:
            f.write(data)
        cur = self.db.execute(
            "INSERT INTO files (token, owner_id, name, mime, size, created_at) VALUES (?,?,?,?,?,?)",
            (token, owner_id, name, mime, len(data), time.time()),
        )
        return self.file_public(self.db.one("SELECT * FROM files WHERE id = ?", (cur.lastrowid,)))

    @staticmethod
    def file_public(f):
        return {"id": f["id"], "url": f"/files/{f['token']}", "name": f["name"], "mime": f["mime"], "size": f["size"]}

    def own_file(self, file_id, user_id):
        f = self.db.one("SELECT * FROM files WHERE id = ?", (int(file_id),))
        if not f or f["owner_id"] != user_id:
            raise HttpError(404, "err.file_not_found")
        return f

    # ================================================================ чаты
    def get_chat(self, chat_id):
        return self.db.one("SELECT * FROM chats WHERE id = ?", (chat_id,))

    def member_ids(self, chat_id):
        return [r["user_id"] for r in self.db.all("SELECT user_id FROM chat_members WHERE chat_id = ?", (chat_id,))]

    def is_member(self, chat_id, user_id):
        return bool(self.db.one("SELECT 1 AS x FROM chat_members WHERE chat_id = ? AND user_id = ?",
                                (chat_id, user_id)))

    def require_member(self, chat_id, user_id):
        chat = self.get_chat(chat_id)
        if not chat or not self.is_member(chat_id, user_id):
            raise HttpError(404, "err.chat_not_found")
        return chat

    def require_owner(self, chat, user_id):
        if chat["type"] != "group":
            raise HttpError(400, "err.group_only")
        if chat["created_by"] != user_id:
            raise HttpError(403, "err.owner_only")

    def get_or_create_private(self, user_a, user_b):
        """Личный чат двух пользователей. Если user_a == user_b — это «Избранное»."""
        if user_a == user_b:
            row = self.db.one(
                """SELECT c.id FROM chats c JOIN chat_members m ON m.chat_id = c.id AND m.user_id = ?
                   WHERE c.type = 'private' AND (SELECT COUNT(*) FROM chat_members WHERE chat_id = c.id) = 1""",
                (user_a,),
            )
            members = (user_a,)
        else:
            row = self.db.one(
                """SELECT c.id FROM chats c
                   JOIN chat_members a ON a.chat_id = c.id AND a.user_id = ?
                   JOIN chat_members b ON b.chat_id = c.id AND b.user_id = ?
                   WHERE c.type = 'private'""",
                (user_a, user_b),
            )
            members = (user_a, user_b)
        if row:
            return row["id"]
        cur = self.db.execute("INSERT INTO chats (type, created_by, created_at) VALUES ('private', ?, ?)",
                              (user_a, time.time()))
        chat_id = cur.lastrowid
        for uid in members:
            self.db.execute("INSERT INTO chat_members (chat_id, user_id) VALUES (?, ?)", (chat_id, uid))
        self.plugins.emit("chat:created", self.get_chat(chat_id))
        return chat_id

    def create_group(self, title, creator, member_ids):
        cur = self.db.execute("INSERT INTO chats (type, title, created_by, created_at) VALUES ('group', ?, ?, ?)",
                              (title, creator["id"], time.time()))
        chat_id = cur.lastrowid
        for uid in {creator["id"], *member_ids}:
            if self.get_user(uid):
                self.db.execute("INSERT OR IGNORE INTO chat_members (chat_id, user_id) VALUES (?, ?)", (chat_id, uid))
        self.system_message(chat_id, "sys.group_created", name=creator["display_name"], title=title)
        self.hub.send_many(self.member_ids(chat_id), "chat:new", {"chat_id": chat_id})
        self.plugins.emit("chat:created", self.get_chat(chat_id))
        return chat_id

    def serialize_chat(self, chat, for_user_id):
        members = self.db.all(
            """SELECT u.*, m.last_read_id, m.muted FROM users u
               JOIN chat_members m ON m.user_id = u.id WHERE m.chat_id = ?""",
            (chat["id"],),
        )
        me = next((m for m in members if m["id"] == for_user_id), None)
        is_saved = chat["type"] == "private" and len(members) == 1
        peer = None
        if chat["type"] == "private":
            peer = next((m for m in members if m["id"] != for_user_id), me)
            title = i18n.t("chat.saved") if is_saved else (peer["display_name"] if peer else "")
            avatar = None if is_saved else (f"/files/{peer['avatar']}" if peer and peer.get("avatar") else None)
        else:
            title = chat["title"]
            avatar = f"/files/{chat['avatar']}" if chat.get("avatar") else None
        last = self.db.one("SELECT * FROM messages WHERE chat_id = ? AND deleted = 0 ORDER BY id DESC LIMIT 1",
                           (chat["id"],))
        unread = self.db.one(
            """SELECT COUNT(*) AS n FROM messages WHERE chat_id = ? AND deleted = 0 AND id > ?
               AND (user_id IS NULL OR user_id != ?)""",
            (chat["id"], me["last_read_id"] if me else 0, for_user_id),
        )["n"]
        pinned = None
        if chat.get("pinned_message_id"):
            row = self.db.one("SELECT * FROM messages WHERE id = ? AND deleted = 0", (chat["pinned_message_id"],))
            pinned = self.serialize_message(row) if row else None
        return {
            "id": chat["id"],
            "type": chat["type"],
            "title": title,
            "avatar": avatar,
            "is_saved": is_saved,
            "peer": None if is_saved else self.user_public(peer),
            "owner_id": chat["created_by"],
            "members": [self.user_public(m) for m in members],
            "read_state": {str(m["id"]): m["last_read_id"] for m in members},
            "unread": unread,
            "muted": bool(me and me["muted"]),
            "pinned": pinned,
            "last_message": self.serialize_message(last) if last else None,
        }

    def notify_chat_update(self, chat_id, extra_user_ids=()):
        self.hub.send_many([*self.member_ids(chat_id), *extra_user_ids], "chat:update", {"chat_id": chat_id})

    # ================================================================ сообщения
    def reply_preview(self, message_id):
        row = self.db.one("SELECT * FROM messages WHERE id = ?", (message_id,))
        if not row or row["deleted"]:
            return {"id": message_id, "deleted": True, "text": i18n.t("msg.deleted"), "type": "text", "sender_name": ""}
        sender = self.get_user(row["user_id"]) if row["user_id"] else None
        meta = json.loads(row["meta"] or "{}")
        return {
            "id": row["id"],
            "text": row["text"][:200],
            "type": row["type"],
            "file_name": (meta.get("file") or {}).get("name"),
            "sender_name": sender["display_name"] if sender else "",
        }

    def serialize_message(self, m):
        msg = {
            "id": m["id"],
            "chat_id": m["chat_id"],
            "text": m["text"],
            "type": m["type"],
            "created_at": m["created_at"],
            "edited": bool(m.get("edited_at")),
            "forwarded_from": m.get("forwarded_from"),
            "reply": self.reply_preview(m["reply_to"]) if m.get("reply_to") else None,
            "sender": self.user_public(self.get_user(m["user_id"])) if m["user_id"] else None,
            "meta": json.loads(m["meta"] or "{}"),
        }
        return self.plugins.filter("message:render", msg)

    def system_message(self, chat_id, key, **params):
        """Системное сообщение. Хранит ключ перевода, клиент показывает его на языке пользователя."""
        params = {k: str(v) for k, v in params.items()}
        text = i18n.t(key, lang=i18n.DEFAULT_LANG, **params)
        return self.post_message(chat_id, None, text, type="system", run_hooks=False,
                                 meta={"i18n": key, "params": params})

    def post_message(self, chat_id, user_id, text, type="text", meta=None, run_hooks=True,
                     reply_to=None, forwarded_from=None):
        text = (text or "").strip()
        meta = meta or {}
        if not text and not meta.get("file"):
            raise HttpError(400, "err.empty_message")
        if len(text) > MAX_TEXT:
            raise HttpError(400, "err.too_long", max=MAX_TEXT)
        if reply_to:
            target = self.db.one("SELECT chat_id FROM messages WHERE id = ?", (int(reply_to),))
            reply_to = int(reply_to) if target and target["chat_id"] == chat_id else None
        draft = {"chat_id": chat_id, "user_id": user_id, "text": text, "type": type, "meta": meta,
                 "reply_to": reply_to, "forwarded_from": forwarded_from}
        if run_hooks:
            draft = self.plugins.filter("message:before", draft, blockable=True)
            if draft is None:
                raise HttpError(400, "err.blocked")
        cur = self.db.execute(
            """INSERT INTO messages (chat_id, user_id, text, type, meta, reply_to, forwarded_from, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (chat_id, draft["user_id"], draft["text"], draft["type"],
             json.dumps(draft["meta"], ensure_ascii=False), draft.get("reply_to"),
             draft.get("forwarded_from"), time.time()),
        )
        message_id = cur.lastrowid
        if user_id:  # своё сообщение сразу прочитано отправителем
            self.db.execute("UPDATE chat_members SET last_read_id = ? WHERE chat_id = ? AND user_id = ?",
                            (message_id, chat_id, user_id))
        msg = self.serialize_message(self.db.one("SELECT * FROM messages WHERE id = ?", (message_id,)))
        self.emit_to_chat(chat_id, "message:new", msg)
        if run_hooks:
            self.plugins.emit("message:after", msg)
            if msg["type"] == "text" and not forwarded_from:
                self.plugins.run_command(msg)
        return msg

    def refresh_message(self, message_id):
        row = self.db.one("SELECT * FROM messages WHERE id = ? AND deleted = 0", (message_id,))
        if row:
            self.emit_to_chat(row["chat_id"], "message:update", self.serialize_message(row))

    def emit_to_chat(self, chat_id, event, data):
        self.hub.send_many(self.member_ids(chat_id), event, data)

    def set_presence(self, user_id, online):
        last_seen = None
        if not online:
            last_seen = time.time()
            self.db.execute("UPDATE users SET last_seen = ? WHERE id = ?", (last_seen, user_id))
        self.hub.send_many(self.contacts_of(user_id), "presence",
                           {"user_id": user_id, "online": online, "last_seen": last_seen})

    # ================================================================ API ядра
    def _register_core_routes(self):
        r = self.route

        # ---------------------------------------------------- аккаунт
        @r("GET", "/api/config", auth=False)
        def config(req):
            return {
                "plugins": self.plugins.loaded,
                "client_scripts": self.plugins.client_scripts,
                "client_styles": self.plugins.client_styles,
                "max_upload": MAX_UPLOAD,
                "commands": [{"name": n, "description": c["description"]}
                             for n, c in sorted(self.plugins.commands.items())],
            }

        @r("GET", "/api/locales", auth=False)
        def locales(req):
            return {"languages": i18n.languages(), "default": i18n.DEFAULT_LANG}

        @r("GET", "/api/locales/{code}", auth=False)
        def locale(req, code):
            return i18n.bundle(code)

        @r("POST", "/api/register", auth=False)
        def register(req):
            username = str(req.body.get("username", "")).strip()
            password = str(req.body.get("password", ""))
            display_name = str(req.body.get("display_name", "")).strip()[:64] or username
            if not USERNAME_RE.match(username):
                raise HttpError(400, "err.username_invalid")
            if len(password) < 4:
                raise HttpError(400, "err.password_short")
            if self.db.one("SELECT 1 AS x FROM users WHERE username = ?", (username,)):
                raise HttpError(409, "err.username_taken")
            user = self.create_user(username, password, display_name)
            token = self.new_session(user["id"])
            self.get_or_create_private(user["id"], user["id"])  # «Избранное»
            self.plugins.emit("user:registered", user)
            return {"token": token, "user": self.user_public(user)}

        @r("POST", "/api/login", auth=False)
        def login(req):
            user = self.db.one("SELECT * FROM users WHERE username = ? AND is_bot = 0",
                               (str(req.body.get("username", "")).strip(),))
            if not user or not self.check_password(str(req.body.get("password", "")), user["password_hash"]):
                raise HttpError(401, "err.bad_credentials")
            return {"token": self.new_session(user["id"]), "user": self.user_public(user)}

        @r("POST", "/api/logout")
        def logout(req):
            self.db.execute("DELETE FROM sessions WHERE token = ?", (req.token,))
            return {"ok": True}

        @r("GET", "/api/me")
        def me(req):
            return self.user_public(req.user)

        @r("PATCH", "/api/me")
        def update_me(req):
            b, sets = req.body, {}
            if "display_name" in b:
                name = str(b["display_name"]).strip()[:64]
                if not name:
                    raise HttpError(400, "err.name_empty")
                sets["display_name"] = name
            if "bio" in b:
                sets["bio"] = str(b["bio"] or "").strip()[:200]
            if "username" in b:
                username = str(b["username"]).strip()
                if not USERNAME_RE.match(username):
                    raise HttpError(400, "err.username_invalid")
                taken = self.db.one("SELECT id FROM users WHERE username = ? AND id != ?", (username, req.user["id"]))
                if taken:
                    raise HttpError(409, "err.username_taken")
                sets["username"] = username
            if "avatar_file_id" in b:
                if b["avatar_file_id"] is None:
                    sets["avatar"] = None
                else:
                    f = self.own_file(b["avatar_file_id"], req.user["id"])
                    if not IMAGE_MIME.match(f["mime"]):
                        raise HttpError(400, "err.image_required")
                    sets["avatar"] = f["token"]
            if sets:
                cols = ", ".join(f"{k} = ?" for k in sets)
                self.db.execute(f"UPDATE users SET {cols} WHERE id = ?", (*sets.values(), req.user["id"]))
            user = self.user_public(self.get_user(req.user["id"]))
            self.hub.send_many([req.user["id"], *self.contacts_of(req.user["id"])], "user:update", user)
            self.plugins.emit("user:updated", user)
            return user

        @r("POST", "/api/me/password")
        def change_password(req):
            if not self.check_password(str(req.body.get("old_password", "")), req.user["password_hash"]):
                raise HttpError(400, "err.wrong_password")
            new = str(req.body.get("new_password", ""))
            if len(new) < 4:
                raise HttpError(400, "err.password_short")
            self.db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (self.hash_password(new), req.user["id"]))
            # завершаем все остальные сессии
            self.db.execute("DELETE FROM sessions WHERE user_id = ? AND token != ?", (req.user["id"], req.token))
            return {"ok": True}

        # ---------------------------------------------------- пользователи
        @r("GET", "/api/users")
        def users(req):
            q = "%" + req.query.get("q", "").strip() + "%"
            rows = self.db.all(
                """SELECT * FROM users WHERE id != ? AND (username LIKE ? OR display_name LIKE ?)
                   ORDER BY is_bot, display_name LIMIT 50""",
                (req.user["id"], q, q),
            )
            return [self.user_public(u) for u in rows]

        @r("GET", "/api/users/{user_id}")
        def user_profile(req, user_id):
            user = self.get_user(int(user_id))
            if not user:
                raise HttpError(404, "err.user_not_found")
            return self.user_public(user)

        # ---------------------------------------------------- файлы
        @r("POST", "/api/upload", raw=True)
        def upload(req):
            if not req.raw:
                raise HttpError(400, "err.empty_file")
            name = os.path.basename(unquote(req.headers.get("X-File-Name") or "file"))[:200] or "file"
            mime = (req.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if not mime or mime == "application/octet-stream":
                mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
            return self.save_upload(req.user["id"], name, mime, req.raw)

        # ---------------------------------------------------- чаты
        @r("GET", "/api/chats")
        def chats(req):
            uid = req.user["id"]
            rows = self.db.all(
                "SELECT c.* FROM chats c JOIN chat_members m ON m.chat_id = c.id WHERE m.user_id = ?", (uid,)
            )
            result = []
            for chat in rows:
                data = self.serialize_chat(chat, uid)
                # пустые личные чаты показываем только тому, кто их создал
                if (chat["type"] == "private" and not data["is_saved"] and not data["last_message"]
                        and chat["created_by"] != uid):
                    continue
                result.append(data)
            result.sort(key=lambda c: (c["last_message"]["id"] if c["last_message"] else 0, c["id"]), reverse=True)
            return result

        @r("GET", "/api/chats/{chat_id}")
        def chat_info(req, chat_id):
            return self.serialize_chat(self.require_member(int(chat_id), req.user["id"]), req.user["id"])

        @r("POST", "/api/chats")
        def create_chat(req):
            kind = req.body.get("type")
            if kind == "group":
                title = str(req.body.get("title", "")).strip()[:64]
                if not title:
                    raise HttpError(400, "err.title_required")
                ids = [int(x) for x in req.body.get("member_ids", [])]
                chat_id = self.create_group(title, req.user, ids)
            elif kind == "saved":
                chat_id = self.get_or_create_private(req.user["id"], req.user["id"])
            else:
                other = self.get_user(int(req.body.get("user_id", 0)))
                if not other:
                    raise HttpError(404, "err.user_not_found")
                chat_id = self.get_or_create_private(req.user["id"], other["id"])
            return self.serialize_chat(self.get_chat(chat_id), req.user["id"])

        @r("PATCH", "/api/chats/{chat_id}")
        def update_chat(req, chat_id):
            chat = self.require_member(int(chat_id), req.user["id"])
            self.require_owner(chat, req.user["id"])
            if "title" in req.body:
                title = str(req.body["title"]).strip()[:64]
                if not title:
                    raise HttpError(400, "err.title_required")
                if title != chat["title"]:
                    self.db.execute("UPDATE chats SET title = ? WHERE id = ?", (title, chat["id"]))
                    self.system_message(chat["id"], "sys.title_changed", name=req.user["display_name"], title=title)
            if "avatar_file_id" in req.body:
                token = None
                if req.body["avatar_file_id"] is not None:
                    f = self.own_file(req.body["avatar_file_id"], req.user["id"])
                    if not IMAGE_MIME.match(f["mime"]):
                        raise HttpError(400, "err.image_required")
                    token = f["token"]
                self.db.execute("UPDATE chats SET avatar = ? WHERE id = ?", (token, chat["id"]))
                self.system_message(chat["id"], "sys.photo_changed", name=req.user["display_name"])
            self.notify_chat_update(chat["id"])
            return self.serialize_chat(self.get_chat(chat["id"]), req.user["id"])

        @r("POST", "/api/chats/{chat_id}/members")
        def add_member(req, chat_id):
            chat = self.require_member(int(chat_id), req.user["id"])
            if chat["type"] != "group":
                raise HttpError(400, "err.group_only")
            user = self.get_user(int(req.body.get("user_id", 0)))
            if not user:
                raise HttpError(404, "err.user_not_found")
            if self.is_member(chat["id"], user["id"]):
                raise HttpError(409, "err.already_member")
            last = self.db.one("SELECT MAX(id) AS id FROM messages WHERE chat_id = ?", (chat["id"],))["id"] or 0
            self.db.execute("INSERT INTO chat_members (chat_id, user_id, last_read_id) VALUES (?, ?, ?)",
                            (chat["id"], user["id"], last))
            self.system_message(chat["id"], "sys.member_added", name=req.user["display_name"], target=user["display_name"])
            self.hub.send(user["id"], "chat:new", {"chat_id": chat["id"]})
            self.notify_chat_update(chat["id"])
            self.plugins.emit("chat:member_added", chat["id"], user)
            return {"ok": True}

        @r("DELETE", "/api/chats/{chat_id}/members/{user_id}")
        def remove_member(req, chat_id, user_id):
            chat = self.require_member(int(chat_id), req.user["id"])
            self.require_owner(chat, req.user["id"])
            user = self.get_user(int(user_id))
            if not user or not self.is_member(chat["id"], user["id"]) or user["id"] == req.user["id"]:
                raise HttpError(400, "err.cannot_remove")
            self.db.execute("DELETE FROM chat_members WHERE chat_id = ? AND user_id = ?", (chat["id"], user["id"]))
            self.system_message(chat["id"], "sys.member_removed", name=req.user["display_name"], target=user["display_name"])
            self.hub.send(user["id"], "chat:removed", {"chat_id": chat["id"]})
            self.notify_chat_update(chat["id"])
            return {"ok": True}

        @r("POST", "/api/chats/{chat_id}/leave")
        def leave(req, chat_id):
            chat = self.require_member(int(chat_id), req.user["id"])
            if chat["type"] != "group":
                raise HttpError(400, "err.cannot_leave_private")
            self.db.execute("DELETE FROM chat_members WHERE chat_id = ? AND user_id = ?", (chat["id"], req.user["id"]))
            self.system_message(chat["id"], "sys.member_left", name=req.user["display_name"])
            self.hub.send(req.user["id"], "chat:removed", {"chat_id": chat["id"]})
            self.notify_chat_update(chat["id"])
            return {"ok": True}

        @r("POST", "/api/chats/{chat_id}/mute")
        def mute(req, chat_id):
            chat = self.require_member(int(chat_id), req.user["id"])
            self.db.execute("UPDATE chat_members SET muted = ? WHERE chat_id = ? AND user_id = ?",
                            (int(bool(req.body.get("muted"))), chat["id"], req.user["id"]))
            return {"ok": True}

        @r("POST", "/api/chats/{chat_id}/read")
        def read(req, chat_id):
            chat = self.require_member(int(chat_id), req.user["id"])
            message_id = int(req.body.get("message_id", 0))
            self.db.execute(
                "UPDATE chat_members SET last_read_id = MAX(last_read_id, ?) WHERE chat_id = ? AND user_id = ?",
                (message_id, chat["id"], req.user["id"]),
            )
            self.emit_to_chat(chat["id"], "read",
                              {"chat_id": chat["id"], "user_id": req.user["id"], "message_id": message_id})
            return {"ok": True}

        @r("POST", "/api/chats/{chat_id}/pin")
        def pin(req, chat_id):
            chat = self.require_member(int(chat_id), req.user["id"])
            message_id = req.body.get("message_id")
            if message_id is not None:
                row = self.db.one("SELECT * FROM messages WHERE id = ? AND chat_id = ? AND deleted = 0",
                                  (int(message_id), chat["id"]))
                if not row:
                    raise HttpError(404, "err.message_not_found")
                self.db.execute("UPDATE chats SET pinned_message_id = ? WHERE id = ?", (row["id"], chat["id"]))
                if row["text"]:
                    self.system_message(chat["id"], "sys.pinned", name=req.user["display_name"], snippet=row["text"][:40])
                else:
                    self.system_message(chat["id"], "sys.pinned_media", name=req.user["display_name"])
            else:
                self.db.execute("UPDATE chats SET pinned_message_id = NULL WHERE id = ?", (chat["id"],))
            self.notify_chat_update(chat["id"])
            return {"ok": True}

        @r("POST", "/api/chats/{chat_id}/typing")
        def typing(req, chat_id):
            self.require_member(int(chat_id), req.user["id"])
            action = "recording" if req.body.get("action") == "recording" else "typing"
            others = [u for u in self.member_ids(int(chat_id)) if u != req.user["id"]]
            self.hub.send_many(others, "typing",
                               {"chat_id": int(chat_id), "user": self.user_public(req.user), "action": action})
            return {"ok": True}

        # ---------------------------------------------------- сообщения
        @r("GET", "/api/chats/{chat_id}/messages")
        def history(req, chat_id):
            cid = int(chat_id)
            self.require_member(cid, req.user["id"])
            limit = min(int(req.query.get("limit", 50)), 200)
            base = "SELECT * FROM messages WHERE chat_id = ? AND deleted = 0"
            if "around" in req.query:
                a = int(req.query["around"])
                older = self.db.all(base + " AND id <= ? ORDER BY id DESC LIMIT 30", (cid, a))
                newer = self.db.all(base + " AND id > ? ORDER BY id ASC LIMIT 30", (cid, a))
                rows = list(reversed(older)) + newer
            elif "after" in req.query:
                rows = self.db.all(base + " AND id > ? ORDER BY id ASC LIMIT ?", (cid, int(req.query["after"]), limit))
            else:
                before = int(req.query.get("before", 2**62))
                rows = list(reversed(self.db.all(base + " AND id < ? ORDER BY id DESC LIMIT ?", (cid, before, limit))))
            return [self.serialize_message(m) for m in rows]

        @r("GET", "/api/chats/{chat_id}/search")
        def search(req, chat_id):
            self.require_member(int(chat_id), req.user["id"])
            q = req.query.get("q", "").strip()
            if not q:
                return []
            rows = self.db.all(
                """SELECT * FROM messages WHERE chat_id = ? AND deleted = 0 AND type != 'system'
                   AND text LIKE ? ORDER BY id DESC LIMIT 50""",
                (int(chat_id), f"%{q}%"),
            )
            return [self.serialize_message(m) for m in rows]

        @r("POST", "/api/chats/{chat_id}/messages")
        def send(req, chat_id):
            cid = int(chat_id)
            self.require_member(cid, req.user["id"])
            b = req.body
            kind, meta = "text", {}
            if b.get("file_id"):
                f = self.own_file(b["file_id"], req.user["id"])
                meta["file"] = self.file_public(f)
                if b.get("type") == "voice":
                    if not f["mime"].startswith("audio/"):
                        raise HttpError(400, "err.voice_audio")
                    kind = "voice"
                    meta["file"]["duration"] = max(0.0, min(float(b.get("duration") or 0), 3600.0))
                elif IMAGE_MIME.match(f["mime"]):
                    kind = "image"
                elif VIDEO_MIME.match(f["mime"]):
                    kind = "video"
                else:
                    kind = "file"
            return self.post_message(cid, req.user["id"], str(b.get("text", "")), type=kind, meta=meta,
                                     reply_to=b.get("reply_to"))

        @r("PATCH", "/api/messages/{message_id}")
        def edit(req, message_id):
            row = self.db.one("SELECT * FROM messages WHERE id = ? AND deleted = 0", (int(message_id),))
            if not row or row["user_id"] != req.user["id"] or row["type"] in ("system", "voice"):
                raise HttpError(404, "err.cannot_edit")
            text = str(req.body.get("text", "")).strip()
            if not text and row["type"] == "text":
                raise HttpError(400, "err.empty_message")
            if len(text) > MAX_TEXT:
                raise HttpError(400, "err.too_long", max=MAX_TEXT)
            draft = {"chat_id": row["chat_id"], "user_id": row["user_id"], "text": text, "type": row["type"],
                     "meta": json.loads(row["meta"] or "{}"), "editing": True}
            draft = self.plugins.filter("message:before", draft, blockable=True)
            if draft is None:
                raise HttpError(400, "err.blocked")
            self.db.execute("UPDATE messages SET text = ?, edited_at = ? WHERE id = ?",
                            (draft["text"], time.time(), row["id"]))
            self.refresh_message(row["id"])
            msg = self.serialize_message(self.db.one("SELECT * FROM messages WHERE id = ?", (row["id"],)))
            self.plugins.emit("message:edited", msg)
            return msg

        @r("DELETE", "/api/messages/{message_id}")
        def delete(req, message_id):
            row = self.db.one("SELECT * FROM messages WHERE id = ? AND deleted = 0", (int(message_id),))
            if not row or not self.is_member(row["chat_id"], req.user["id"]):
                raise HttpError(404, "err.message_not_found")
            chat = self.get_chat(row["chat_id"])
            is_group_owner = chat["type"] == "group" and chat["created_by"] == req.user["id"]
            if row["user_id"] != req.user["id"] and not is_group_owner:
                raise HttpError(403, "err.delete_own")
            self.db.execute("UPDATE messages SET deleted = 1 WHERE id = ?", (row["id"],))
            if chat.get("pinned_message_id") == row["id"]:
                self.db.execute("UPDATE chats SET pinned_message_id = NULL WHERE id = ?", (chat["id"],))
                self.notify_chat_update(chat["id"])
            self.emit_to_chat(row["chat_id"], "message:deleted", {"id": row["id"], "chat_id": row["chat_id"]})
            self.plugins.emit("message:deleted", row)
            return {"ok": True}

        @r("POST", "/api/messages/{message_id}/forward")
        def forward(req, message_id):
            row = self.db.one("SELECT * FROM messages WHERE id = ? AND deleted = 0", (int(message_id),))
            if not row or row["type"] == "system" or not self.is_member(row["chat_id"], req.user["id"]):
                raise HttpError(404, "err.message_not_found")
            target = int(req.body.get("chat_id", 0))
            self.require_member(target, req.user["id"])
            sender = self.get_user(row["user_id"]) if row["user_id"] else None
            origin = row["forwarded_from"] or (sender["display_name"] if sender else "")
            return self.post_message(target, req.user["id"], row["text"], type=row["type"],
                                     meta=json.loads(row["meta"] or "{}"), forwarded_from=origin)


class Handler(BaseHTTPRequestHandler):
    app: Messenger = None
    server_version = "BaseMessenger/2.0"

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")

    def do_PATCH(self):
        self.dispatch("PATCH")

    def do_DELETE(self):
        self.dispatch("DELETE")

    # ------------------------------------------------------------
    def send_json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_raw(self, limit):
        length = int(self.headers.get("Content-Length") or 0)
        if length > limit:
            # дочитываем, чтобы клиент получил ответ, а не обрыв соединения
            remaining = length
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 65536))
                if not chunk:
                    break
                remaining -= len(chunk)
            raise HttpError(413, "err.file_too_big", mb=limit // 1024 // 1024)
        return self.rfile.read(length) if length else b""

    def read_json(self):
        data = self.read_raw(MAX_JSON)
        if not data:
            return {}
        try:
            parsed = json.loads(data.decode("utf-8"))
        except ValueError:
            raise HttpError(400, "err.bad_json")
        return parsed if isinstance(parsed, dict) else {}

    def dispatch(self, method):
        url = urlparse(self.path)
        path = url.path
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        i18n.set_lang(i18n.pick(self.headers.get("X-Lang") or self.headers.get("Accept-Language")))

        if method == "GET" and path == "/api/events":
            return self.sse(query.get("token"))
        if method == "GET" and path.startswith("/files/"):
            return self.serve_file(path[len("/files/"):])

        for m, rx, fn, auth, raw in self.app.routes:
            if m != method:
                continue
            match = rx.fullmatch(path)
            if not match:
                continue
            try:
                token = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
                user = self.app.user_by_token(token)
                if auth and not user:
                    raise HttpError(401, "err.auth_required")
                body, raw_data = ({}, self.read_raw(MAX_UPLOAD)) if raw else (self.read_json(), None)
                req = Request(method, path, query, body, user, match.groupdict(), self.headers, raw_data, token)
                result = fn(req, **match.groupdict())
                status, data = result if isinstance(result, tuple) else (200, result)
                self.send_json(status, data)
            except HttpError as e:
                self.send_json(e.status, {"error": e.message})
            except PluginError as e:
                self.send_json(400, {"error": i18n.t(str(e))})
            except (ValueError, TypeError) as e:
                self.send_json(400, {"error": i18n.t("err.bad_data", detail=e)})
            except Exception:
                traceback.print_exc()
                self.send_json(500, {"error": i18n.t("err.internal")})
            return

        if method == "GET" and not path.startswith("/api/"):
            return self.serve_static(path)
        self.send_json(404, {"error": i18n.t("err.not_found")})

    def serve_static(self, path):
        if path.startswith("/plugins/"):
            name = path[len("/plugins/"):]
            allowed = self.app.plugins.client_scripts + self.app.plugins.client_styles
            if name not in allowed:
                return self.send_json(404, {"error": i18n.t("err.not_found")})
            file_path = os.path.join(PLUGINS_DIR, name)
        else:
            rel = "index.html" if path in ("", "/") else path.lstrip("/")
            file_path = os.path.realpath(os.path.join(STATIC_DIR, rel))
            if not file_path.startswith(os.path.realpath(STATIC_DIR) + os.sep):
                return self.send_json(404, {"error": i18n.t("err.not_found")})
        if not os.path.isfile(file_path):
            return self.send_json(404, {"error": i18n.t("err.not_found")})
        with open(file_path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(os.path.splitext(file_path)[1], "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def serve_file(self, token):
        """Отдать загруженный файл. Ссылка содержит случайный токен; поддерживается Range (перемотка аудио/видео)."""
        f = self.app.db.one("SELECT * FROM files WHERE token = ?", (token,)) if TOKEN_RE.match(token) else None
        path = os.path.join(self.app.upload_dir, token) if f else None
        if not f or not os.path.isfile(path):
            return self.send_json(404, {"error": i18n.t("err.file_not_found")})
        size = os.path.getsize(path)
        start, end, status = 0, size - 1, 200
        rng = self.headers.get("Range")
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)$", rng.strip())
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                    end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
                else:
                    start = max(0, size - int(m.group(2)))
                status = 206
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        inline = bool(INLINE_MIME.match(f["mime"]))
        self.send_response(status)
        self.send_header("Content-Type", f["mime"] if inline else "application/octet-stream")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Disposition",
                         f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(f['name'])}")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "private, max-age=31536000, immutable")
        self.end_headers()
        try:
            with open(path, "rb") as fh:
                fh.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = fh.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except OSError:
            pass

    def sse(self, token):
        user = self.app.user_by_token(token)
        if not user:
            return self.send_json(401, {"error": i18n.t("err.auth_required")})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        q, became_online = self.app.hub.subscribe(user["id"])
        if became_online:
            self.app.set_presence(user["id"], True)
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                try:
                    event, data = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                payload = json.dumps({"event": event, "data": data}, ensure_ascii=False)
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
        except OSError:
            pass
        finally:
            if self.app.hub.unsubscribe(user["id"], q):
                self.app.set_presence(user["id"], False)


def create_server(host, port, data_dir):
    i18n.load()
    os.makedirs(data_dir, exist_ok=True)
    app = Messenger(os.path.join(data_dir, "messenger.db"), os.path.join(data_dir, "uploads"))

    # встроенная команда /help — список всех команд, включая команды плагинов
    def help_cmd(ctx):
        lines = [i18n.t("sys.help_title")]
        for name, c in sorted(app.plugins.commands.items()):
            desc = i18n.t(f"cmd.{name}")
            desc = c["description"] if desc == f"cmd.{name}" else desc
            lines.append(f"/{name} — {desc}" if desc else f"/{name}")
        return "\n".join(lines)

    app.plugins.commands["help"] = {"fn": help_cmd, "description": "Commands list", "plugin": "core"}
    app.plugins.load_all(PLUGINS_DIR)

    Handler.app = app
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return app, server
