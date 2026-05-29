"""
Gunicorn configuration tuned for IL9Cast on Railway.

Uses preload_app so chart-cache prewarm and JSONL line cache run once in the
master process before workers fork (matches legacy --preload behavior).
"""

import multiprocessing
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
preload_app = True

# Thread workers: good for I/O-bound Flask (JSON APIs, static, proxies).
worker_class = "gthread"
workers = int(os.environ.get("WEB_CONCURRENCY", min(4, multiprocessing.cpu_count() + 1)))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))

timeout = int(os.environ.get("GUNICORN_TIMEOUT", "120"))
graceful_timeout = 30
keepalive = 5

# Recycle workers periodically to contain slow memory growth from large caches.
max_requests = int(os.environ.get("GUNICORN_MAX_REQUESTS", "2000"))
max_requests_jitter = 100

accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info")

def post_fork(server, worker):
    """Reinitialize threading locks after fork (preload_app safety)."""
    import app as app_module
    import threading
    app_module._chart_cache_lock = threading.Lock()
    app_module._chart_compute_locks_lock = threading.Lock()
    app_module._jsonl_lines_lock = threading.Lock()
    app_module._chart_compute_locks = {}
