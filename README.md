**English** | **[Русский](README.ru.md)**

# Messenger Core: a messenger you can extend with plugins

An open-source, Telegram-style messenger core. Take it and build **your own messenger**.
New features are added as **plugins**, so you never have to touch the core.

- Only **Python 3.10+** is required, with no dependencies.
- Server and web client in one project, started with a single command.
- Multilingual interface (English and Russian so far), and you can add your own language.

## Quick start

```bash
git clone https://github.com/Semkya1/messenger-core.git
cd messenger-core
python server.py        # on Windows: py server.py
```

Open http://localhost:8000 and sign up. To try a conversation, create a second account in
a private (incognito) window.

## Features

**Account and profile**
- sign up and log in (passwords are stored as scrypt hashes)
- profile: photo, name, @username, bio; password change (logs out other devices)
- "online" and "last seen at 12:30"
- dark theme, browser notifications, language switcher

**Chats**
- private chats, groups, Saved Messages (notes to yourself)
- groups: title, photo, members, adding and removing members (group owner 👑), leaving
- pinned message
- mute any chat
- people search and message search within a chat
- unread counters, unread total in the browser tab title

**Messages**
- real-time delivery, "typing…" and "recording a voice message…"
- **voice messages**: microphone recording and a player with seeking
- photos (full-screen viewer), videos, any files; drag them into the window or paste with Ctrl+V
- replies with quotes, editing (shown as "edited"), forwarding, delete for everyone
- read receipts: ✓ sent, ✓✓ read
- clickable links, date separators, history loading on scroll
- /commands with autocomplete, bots

**Shortcuts:** Enter sends, Shift+Enter adds a new line, ↑ in an empty field edits your last
message, Esc cancels, double-click a message to reply, right-click opens the message menu.

## Languages

The language is picked automatically from the browser and can be changed on the login page
or in Settings. All interface texts, server errors and system messages ("Anna created the
group…") live in one file per language: `static/locales/<code>.json`. Every chat member sees
system messages in their own language.

**Adding a language:**
1. Copy `static/locales/en.json` to a file named after the language code, such as `es.json` or `de.json`.
2. Set the language name in `"_name"` (for example, `"Español"`) and translate the values. Do not
   change the keys, and keep placeholders such as `{name}` as they are.
3. Restart the server and the language appears in the list. Any missing string falls back to English.

Send your translation as a pull request so everyone can use it.

The default language (for browsers in languages the project doesn't have) is set with the
`DEFAULT_LANG` variable and is `en` by default.

## Building your own feature

1. Copy `plugins/_template.py` to `plugins/my_feature.py`.
2. Write your logic. The template shows everything a plugin can do.
3. Restart the server. The plugin is picked up automatically.

Example plugins:

| Plugin | What it shows |
|---|---|
| `dice_bot.py` | a bot, /commands, a welcome message for newcomers, private replies, its own translations |
| `word_filter.py` | moderation: word masking and spam blocking (also applied when editing) |
| `reactions.py` + `reactions.js` | a complete feature: its own table, its own API, extra message data, a menu item, live updates for everyone |

### Server side (Python): what a plugin gets through `api`

| Capability | How |
|---|---|
| Command | `@api.command("name", "description")` |
| Change or block a message | `@api.on("message:before")` → return the draft or `raise api.reject(...)` |
| Add data to a message | `@api.on("message:render")` |
| Events | `message:after`, `message:edited`, `message:deleted`, `user:registered`, `user:updated`, `chat:created`, `chat:member_added` |
| Custom endpoint | `@api.route("POST", "/api/plugins/x/{id}")`, use `raw=True` for files |
| Database | `api.db.execute / one / all`, `api.db.ensure_column(...)` |
| Bot | `api.create_bot(...)`, `api.send_message(chat_id, text, bot=bot)` |
| Real time | `api.emit_to_chat(...)`, `api.emit_to_user(...)`, `api.update_message(id)` |
| Translations | `api.add_translations({"en": {...}, "ru": {...}})`, `api.t("key", name=...)` |
| Client JS and CSS | `api.client_script("x.js")`, `api.client_style("x.css")` |

### Client side (JS): the `window.Messenger` object

`on(event, fn)`, `addHeaderButton`, `addSidebarButton`, `addMessageAction` (message menu
item), `addMessageDecorator`, `addChatDecorator`, `api`, `upload`, `sendMessage`,
`openChat`, `openProfile`, `t` (translate), `toast`, `modal`, `me`, `currentChat`, `chats`, `messages`.

### Your own brand

Light and dark theme colors are CSS variables at the top of `static/style.css`. The app
name is the `app.name` key in `static/locales/*.json`.

## Configuration

| Variable | Default | What it does |
|---|---|---|
| `PORT` | `8000` | port |
| `HOST` | `0.0.0.0` | bind address; by default the server is reachable on your local network |
| `DATA_DIR` | `./data` | folder for the database and uploaded files |
| `MAX_UPLOAD_MB` | `25` | maximum file size |
| `DEFAULT_LANG` | `en` | default language |

## Project structure

```
server.py            entry point
core/app.py          core: users, profiles, chats, messages, files, HTTP API, real time (SSE)
core/plugins.py      plugin system (PluginAPI)
core/i18n.py         translations
core/db.py           SQLite and automatic migrations
core/events.py       event delivery to online clients
static/              web client (HTML/CSS/JS, no build step)
static/locales/      translations, one JSON file per language
plugins/             features
data/                database and files (created automatically, not tracked by git)
```

## Before going live

- **HTTPS is required**: without it browsers block the microphone, and passwords travel in
  plain text. Put Caddy or nginx in front of the server.
- Add rate limiting at the proxy level.
- Back up the `data/` folder.
- For heavy load: PostgreSQL instead of SQLite and Redis for event delivery.
- Not included yet: calls (WebRTC), mobile push notifications (Web Push or FCM), end-to-end
  encryption, mobile apps. All of these can be built on top of the current API.

## License

[MIT](LICENSE): you are free to use, modify and distribute the code, including in commercial
projects. Just keep the copyright line and the license text.
