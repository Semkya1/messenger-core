"""Пример: фильтр сообщений (модерация). Меняет текст или блокирует сообщение."""
import re

NAME = "word_filter"

BAD_WORDS = ["дурак", "идиот"]          # дополните своим списком
SPAM_PATTERN = re.compile(r"(https?://\S+.*){4,}", re.I)   # 4+ ссылки в одном сообщении


def setup(api):
    api.add_translations({
        "en": {"word_filter.spam": "Looks like spam: too many links"},
        "ru": {"word_filter.spam": "Похоже на спам: слишком много ссылок"},
    })

    @api.on("message:before")
    def moderate(draft):
        if SPAM_PATTERN.search(draft["text"]):
            raise api.reject("word_filter.spam")   # ключ перевода — пользователь увидит текст на своём языке
        text = draft["text"]
        for word in BAD_WORDS:
            text = re.sub(re.escape(word), lambda m: "*" * len(m.group()), text, flags=re.I)
        draft["text"] = text
        return draft
