# -*- coding: utf-8 -*-
"""Настройки gunicorn для хостинга.

Один воркер с несколькими потоками: SQLite не рассчитан на параллельную запись
из разных процессов, а фоновый планировщик сброса в 19:55 должен быть один.
Для 26 человек этого с большим запасом достаточно.
"""

import os

bind = "0.0.0.0:%s" % os.environ.get("PORT", "10000")
workers = 1
threads = int(os.environ.get("GUNICORN_THREADS", "8"))
worker_class = "gthread"
timeout = 120
graceful_timeout = 30
keepalive = 5
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info")
