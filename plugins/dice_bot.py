"""Example: a bot with commands, a welcome message and its own translations.

Пример: бот с командами, приветствием новичков и своими переводами.
"""
import random

NAME = "dice_bot"

TRANSLATIONS = {
    "en": {
        "cmd.dice": "Roll a die",
        "cmd.coin": "Flip a coin",
        "cmd.roll": "Random number: /roll 100",
        "dice_bot.dice": "{name} rolls a die: 🎲 {value}",
        "dice_bot.heads": "🦅 Heads",
        "dice_bot.tails": "🪙 Tails",
        "dice_bot.coin": "{name}: {side}",
        "dice_bot.roll": "{name} gets {value} (1–{max})",
        "dice_bot.roll_usage": "Usage: /roll 100",
        "dice_bot.welcome": "Hi, {name}! I'm an example plugin bot.\nTry /dice, /coin, /roll 100 or /help",
        "dice_bot.only_commands": "I only understand commands: /dice, /coin, /roll, /help",
    },
    "ru": {
        "cmd.dice": "Бросить кубик",
        "cmd.coin": "Подбросить монетку",
        "cmd.roll": "Случайное число: /roll 100",
        "dice_bot.dice": "{name} бросает кубик: 🎲 {value}",
        "dice_bot.heads": "🦅 Орёл",
        "dice_bot.tails": "🪙 Решка",
        "dice_bot.coin": "{name}: {side}",
        "dice_bot.roll": "{name} получает {value} (1–{max})",
        "dice_bot.roll_usage": "Использование: /roll 100",
        "dice_bot.welcome": "Привет, {name}! Я пример бота-плагина.\nПопробуй /dice, /coin, /roll 100 или /help",
        "dice_bot.only_commands": "Я понимаю только команды: /dice, /coin, /roll, /help",
    },
}


def setup(api):
    api.add_translations(TRANSLATIONS)
    bot = api.create_bot("dicebot", "Dice Bot 🎲")

    # Ответы бота пишутся на языке того, кто вызвал команду
    @api.command("dice", "Roll a die")
    def dice(ctx):
        ctx.reply(api.t("dice_bot.dice", name=ctx.user["display_name"], value=random.randint(1, 6)), bot=bot)

    @api.command("coin", "Flip a coin")
    def coin(ctx):
        side = api.t(random.choice(["dice_bot.heads", "dice_bot.tails"]))
        ctx.reply(api.t("dice_bot.coin", name=ctx.user["display_name"], side=side), bot=bot)

    @api.command("roll", "Random number: /roll 100")
    def roll(ctx):
        try:
            limit = max(1, int(ctx.args or 100))
        except ValueError:
            return api.t("dice_bot.roll_usage")
        ctx.reply(api.t("dice_bot.roll", name=ctx.user["display_name"], value=random.randint(1, limit), max=limit),
                  bot=bot)

    # Приветствие каждому новому пользователю в личке от бота
    @api.on("user:registered")
    def welcome(user):
        chat_id = api.open_private_chat(bot["id"], user["id"])
        api.send_message(chat_id, api.t("dice_bot.welcome", name=user["display_name"]), bot=bot)

    # Отвечаем на обычные сообщения в личке с ботом
    @api.on("message:after")
    def answer(msg):
        sender = msg["sender"]
        if not sender or sender["id"] == bot["id"] or msg["text"].startswith("/"):
            return
        chat = api.db.one("SELECT type FROM chats WHERE id = ?", (msg["chat_id"],))
        if chat["type"] == "private" and api.is_member(msg["chat_id"], bot["id"]):
            api.send_message(msg["chat_id"], api.t("dice_bot.only_commands"), bot=bot)
