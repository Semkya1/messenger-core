"""ШАБЛОН ПЛАГИНА. Скопируйте в my_feature.py (без "_" в начале) и перезапустите сервер.

Всё, что может плагин, — ниже. Удалите ненужное.
"""
NAME = "my_feature"


def setup(api):
    # 1) Своя таблица в базе
    api.db.execute("CREATE TABLE IF NOT EXISTS my_table (id INTEGER PRIMARY KEY, user_id INTEGER, value TEXT)")

    # 2) Переводы. Ключ "cmd.<команда>" переводит описание команды в подсказках.
    #    В коде: api.t("my_feature.hello", name=...) на сервере, Messenger.t(...) в браузере.
    api.add_translations({
        "en": {"cmd.hello": "Say hello", "my_feature.hello": "Hi, {name}!"},
        "ru": {"cmd.hello": "Поздороваться", "my_feature.hello": "Привет, {name}!"},
    })

    # Свой бот
    bot = api.create_bot("mybot", "My bot")

    # 3) /команда
    @api.command("hello", "Поздороваться")
    def hello(ctx):
        # ctx.user, ctx.chat_id, ctx.args, ctx.message
        ctx.reply(api.t("my_feature.hello", name=ctx.user["display_name"]), bot=bot)
        # или просто: return "текст"  -> системное сообщение

    # 4) Изменить / заблокировать сообщение ДО сохранения
    @api.on("message:before")
    def before(draft):
        # draft = {chat_id, user_id, text, type, meta}
        # raise api.reject("причина")  — отклонить
        # draft["meta"]["что-то"] = ...  — сохранить свои данные вместе с сообщением
        return draft

    # 5) Добавить поля к сообщению, которое увидит клиент
    @api.on("message:render")
    def render(msg):
        # msg["my_field"] = ...
        return msg

    # 6) Реакция на события
    @api.on("message:after")
    def after(msg):
        pass

    @api.on("message:edited")
    def edited(msg):
        pass

    @api.on("user:registered")
    def registered(user):
        pass

    @api.on("chat:created")
    def chat_created(chat):
        pass

    # 7) Свой HTTP-эндпоинт (клиент вызывает Messenger.api('POST', '/api/plugins/my_feature/do', {...}))
    @api.route("POST", "/api/plugins/my_feature/do")
    def do(req):
        # req.user, req.body, req.query
        # raise api.error(400, "ошибка")
        api.emit_to_user(req.user["id"], "my_feature:done", {"ok": True})  # своё realtime-событие
        return {"ok": True}

    # 8) Код для клиента: файл plugins/my_feature.js
    # api.client_script("my_feature.js")
    # api.client_style("my_feature.css")


# --- Пример my_feature.js ----------------------------------------------------
# Messenger.on('ready', me => ...);                        // после входа
# Messenger.on('chat:open', chat => ...);                  // открыт чат
# Messenger.on('my_feature:done', data => ...);            // своё событие с сервера
# Messenger.addHeaderButton('⭐', 'Подсказка', chat => ...);  // кнопка в шапке чата
# Messenger.addSidebarButton('⚙', 'Подсказка', () => ...);    // кнопка над списком чатов
# Messenger.addMessageAction('📌 Моё', (msg, item) => ...);  // пункт в меню сообщения (правый клик)
# Messenger.upload(file).then(f => ...)                     // загрузить файл, f.id можно отправить
# Messenger.addMessageDecorator((msg, row) => ...);         // изменить вид сообщения
# Messenger.addChatDecorator((chat, item) => ...);          // изменить вид чата в списке
# Messenger.api('POST', '/api/plugins/my_feature/do', {})   // запрос к своему эндпоинту
# Messenger.sendMessage(chatId, 'текст'); Messenger.toast('...'); Messenger.modal(title, el, onOk)
