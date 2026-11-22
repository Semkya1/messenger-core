"""Хаб событий реального времени (Server-Sent Events).

У каждого открытого окна клиента своя очередь. Отправка события пользователю
кладёт его во все очереди этого пользователя.
"""
import queue
import threading


class Hub:
    def __init__(self):
        self._subs = {}  # user_id -> set[Queue]
        self._lock = threading.Lock()

    def subscribe(self, user_id):
        """Возвращает (очередь, стал_ли_пользователь_онлайн)."""
        q = queue.Queue()
        with self._lock:
            subs = self._subs.setdefault(user_id, set())
            became_online = not subs
            subs.add(q)
        return q, became_online

    def unsubscribe(self, user_id, q):
        """Возвращает True, если это было последнее окно (пользователь ушёл в офлайн)."""
        with self._lock:
            subs = self._subs.get(user_id)
            if not subs:
                return False
            subs.discard(q)
            if not subs:
                del self._subs[user_id]
                return True
            return False

    def is_online(self, user_id):
        with self._lock:
            return bool(self._subs.get(user_id))

    def send(self, user_id, event, data):
        with self._lock:
            targets = list(self._subs.get(user_id, ()))
        for q in targets:
            q.put((event, data))

    def send_many(self, user_ids, event, data):
        for uid in set(user_ids):
            self.send(uid, event, data)
