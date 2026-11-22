"""Пример полноценной фишки (сервер + клиент): реакции на сообщения.

Показывает: своя таблица в БД, свой эндпоинт, добавление данных в сообщение,
обновление сообщения у всех в реальном времени, JS-часть в reactions.js.
"""
NAME = "reactions"
EMOJIS = ["👍", "❤️", "😂", "😮", "😢", "🔥"]


def setup(api):
    api.db.execute("""CREATE TABLE IF NOT EXISTS reactions (
        message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        emoji      TEXT NOT NULL,
        PRIMARY KEY (message_id, user_id, emoji))""")
    api.client_script("reactions.js")
    api.add_translations({
        "en": {"reactions.action": "😊 React", "reactions.unknown": "Unknown reaction"},
        "ru": {"reactions.action": "😊 Реакция", "reactions.unknown": "Такой реакции нет"},
    })

    @api.route("POST", "/api/plugins/reactions/toggle")
    def toggle(req):
        message_id = int(req.body.get("message_id", 0))
        emoji = req.body.get("emoji")
        if emoji not in EMOJIS:
            raise api.error(400, "reactions.unknown")
        msg = api.db.one("SELECT chat_id FROM messages WHERE id = ? AND deleted = 0", (message_id,))
        if not msg or not api.is_member(msg["chat_id"], req.user["id"]):
            raise api.error(404, "err.message_not_found")
        key = (message_id, req.user["id"], emoji)
        if api.db.one("SELECT 1 AS x FROM reactions WHERE message_id=? AND user_id=? AND emoji=?", key):
            api.db.execute("DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?", key)
        else:
            api.db.execute("INSERT INTO reactions (message_id, user_id, emoji) VALUES (?,?,?)", key)
        api.update_message(message_id)   # перерисует сообщение у всех участников
        return {"ok": True}

    @api.route("GET", "/api/plugins/reactions/emojis")
    def emojis(req):
        return EMOJIS

    @api.on("message:render")
    def attach(msg):
        rows = api.db.all(
            "SELECT emoji, GROUP_CONCAT(user_id) AS users FROM reactions WHERE message_id = ? GROUP BY emoji",
            (msg["id"],),
        )
        msg["reactions"] = [{"emoji": r["emoji"], "users": [int(u) for u in r["users"].split(",")]} for r in rows]
        return msg
