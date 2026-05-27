# gunicorn_conf.py — AutoDataFlow v2.3 工业级部署配置
# ============================================================
"""
用法:
  gunicorn app:app -c gunicorn_conf.py

说明:
  workers = 4  (CPU-bound: 2*CPU + 1，推荐多核服务器)
  threads = 2  (每个 worker 2 线程，处理并发请求)
  timeout = 60 (单个请求超过 60s 则 SIGKILL)
  worker_class = 'uvicorn.workers.UvicornWorker'
                (ASGI 异步 worker，兼容 FastAPI/starlette)
  keepalive = 65 (HTTP keep-alive，略低于 LB 超时防冲突)

  日志:
    accesslog = '-'      → stdout（由 docker/systemd 收集）
    errorlog = '-'       → stderr
    loglevel = 'info'    → info 级别以上

  预加载:
    preload_app = True  → 启动时加载 app，主进程 fork() 子进程共享内存
                          (注意：多 worker 共享 db 连接池需加锁)

  钩子:
    on_starting  → 启动时打印版本信息
    post_fork     → 每个 worker fork 后重新初始化（避免 SQLite 连接复用问题）
    worker_abort  → worker 异常退出时记录日志
"""

import os
import sys

# ---- 环境变量 ----
bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8080")
workers = int(os.environ.get("GUNICORN_WORKERS", 4))
threads = int(os.environ.get("GUNICORN_THREADS", 2))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", 60))
keepalive = int(os.environ.get("GUNICORN_KEEPALIVE", 65))

# ---- Worker 配置 ----
worker_class = "uvicorn.workers.UvicornWorker"
# 每个 worker 最多处理 1000 个请求后重启（防止内存泄漏）
max_requests = 1000
max_requests_jitter = 50  # 随机抖动，避免所有 worker 同时重启

# ---- 超时 ----
graceful_timeout = 30  # graceful shutdown 等待时间
request_timeout = timeout

# ---- 日志 ----
accesslog = "-"
errorlog = "-"
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" request_id=%({X-Request-ID}i)s'

# ---- 进程名 ----
proc_name = "autodataflow"

# ---- 预加载（共享 app state，SQLite 连接池在 post_fork 重新初始化）----
preload_app = True

# ---- 钩子函数 ----

def on_starting(server):
    """gunicorn 启动时"""
    sys.stderr.write("[gunicorn] AutoDataFlow v2.3 starting...\n")
    sys.stderr.write(f"[gunicorn] bind={bind} workers={workers} threads={threads}\n")


def post_fork(server, worker):
    """
    每个 worker fork 完成后调用。
    SQLite 连接池在主进程预热，子进程共享（Linux fork 复制），
    但为安全起见，我们在 worker 进程关闭并重建连接。
    """
    import threading
    import sqlite3
    from pathlib import Path

    # 重建本进程的 SQLite 连接池
    # app.py 中的 _db_pool 会随着 fork 复制，
    # 但文件锁/连接状态可能已失效，在这里我们简单 reset
    try:
        from app import _db_pool, _db_pool_lock, _return_db_pool_conn, _get_db_pool_conn, DATA_DIR
        with _db_pool_lock:
            for conn in list(_db_pool):
                try:
                    conn.close()
                except Exception:
                    pass
            _db_pool.clear()

        # 重新预热池
        from app import _DB_POOL_SIZE
        for _ in range(_DB_POOL_SIZE):
            conn = _get_db_pool_conn()
            _return_db_pool_conn(conn)

        worker.log.info(f"[worker-{worker.pid}] SQLite connection pool re-initialized")
    except Exception as e:
        worker.log.warning(f"[worker-{worker.pid}] pool re-init skip: {e}")

    worker.log.info(f"[worker-{worker.pid}] forked, pid={os.getpid()}")


def worker_abort(worker):
    """worker 被 SIGKILL 前记录日志"""
    import logging
    logging.error(
        f"[worker-{worker.pid}] worker aborted (memory/timeout issue). "
        f"Check DB connections and subprocess timeouts."
    )
