"""Запуск мессенджера:  py server.py   (потом открыть http://localhost:8000)

Переменные окружения:
  HOST          адрес (по умолчанию 0.0.0.0 — доступен в локальной сети)
  PORT          порт (8000)
  DATA_DIR      папка для базы и загруженных файлов (./data)
  MAX_UPLOAD_MB максимальный размер файла (25)
"""
import os
import sys

from core.app import create_server

if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    data_dir = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))

    app, server = create_server(host, port, data_dir)
    print(f"Мессенджер запущен: http://localhost:{port}  (Ctrl+C — остановить)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлен")
