"""Пример: бот с командами + приветствие новых пользователей."""
import random

NAME = "dice_bot"


def setup(api):
    bot = api.create_bot("dicebot", "Кубик-бот")

    @api.command("dice", "Бросить кубик")
    def dice(ctx):
        ctx.reply(f"{ctx.user['display_name']} бросает кубик: 🎲 {random.randint(1, 6)}", bot=bot)

    @api.command("coin", "Подбросить монетку")
    def coin(ctx):
        ctx.reply(f"{ctx.user['display_name']}: {random.choice(['🦅 Орёл', '🪙 Решка'])}", bot=bot)

    @api.command("roll", "Случайное число: /roll 100")
    def roll(ctx):
        try:
            limit = max(1, int(ctx.args or 100))
        except ValueError:
            return "Использование: /roll 100"
        ctx.reply(f"{ctx.user['display_name']} получает {random.randint(1, limit)} (1–{limit})", bot=bot)

    # Приветствие каждому новому пользователю в личке от бота
    @api.on("user:registered")
    def welcome(user):
        chat_id = api.open_private_chat(bot["id"], user["id"])
        api.send_message(chat_id, f"Привет, {user['display_name']}! Я пример бота-плагина.\n"
                                  "Попробуй /dice, /coin, /roll 100 или /help", bot=bot)

    # Отвечаем на обычные сообщения в личке с ботом
    @api.on("message:after")
    def answer(msg):
        sender = msg["sender"]
        if not sender or sender["id"] == bot["id"] or msg["text"].startswith("/"):
            return
        chat = api.db.one("SELECT type FROM chats WHERE id = ?", (msg["chat_id"],))
        if chat["type"] == "private" and api.is_member(msg["chat_id"], bot["id"]):
            api.send_message(msg["chat_id"], "Я понимаю только команды: /dice, /coin, /roll, /help", bot=bot)
