"""Пример: фильтр сообщений (модерация). Меняет текст или блокирует сообщение."""
import re

NAME = "word_filter"

BAD_WORDS = ["дурак", "идиот"]          # дополните своим списком
SPAM_PATTERN = re.compile(r"(https?://\S+.*){4,}", re.I)   # 4+ ссылки в одном сообщении


def setup(api):
    @api.on("message:before")
    def moderate(draft):
        if SPAM_PATTERN.search(draft["text"]):
            raise api.reject("Похоже на спам: слишком много ссылок")
        text = draft["text"]
        for word in BAD_WORDS:
            text = re.sub(re.escape(word), lambda m: "*" * len(m.group()), text, flags=re.I)
        draft["text"] = text
        return draft
