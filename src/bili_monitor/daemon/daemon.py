from __future__ import annotations

import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Optional

from bili_monitor.api.client import BiliAPIClient
from bili_monitor.config import Settings
from bili_monitor.core.scheduler import MonitorState, Scheduler
from bili_monitor.db.database import Database

logger = logging.getLogger("bili_monitor.daemon")

PID_FILE = Path("/tmp") / "bili_monitor.pid"
LOG_FILE = Path("/tmp") / "bili_monitor.log"


def _read_pid() -> Optional[int]:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text().strip())
    except (ValueError, OSError):
        return None


def _write_pid(pid: int) -> None:
    PID_FILE.write_text(str(pid))


def _remove_pid() -> None:
    PID_FILE.unlink(missing_ok=True)


def _is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class DaemonManager:
    def status(self) -> Optional[int]:
        pid = _read_pid()
        if pid and _is_running(pid):
            return pid
        return None

    def start(self) -> Optional[int]:
        if self.status():
            return None
        pid = os.fork()
        if pid > 0:
            _write_pid(pid)
            return pid
        os.setsid()
        pid2 = os.fork()
        if pid2 > 0:
            sys.exit(0)
        _write_pid(os.getpid())
        _run_daemon()
        sys.exit(0)

    def stop(self) -> bool:
        pid = self.status()
        if not pid:
            return False
        os.kill(pid, signal.SIGTERM)
        for _ in range(20):
            time.sleep(0.1)
            if not _is_running(pid):
                break
        _remove_pid()
        return True


def _run_daemon() -> None:
    log_fh = open(LOG_FILE, "a", buffering=1)
    sys.stdout = log_fh
    sys.stderr = log_fh

    logging.basicConfig(
        stream=log_fh,
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    loop = None
    try:
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_daemon_main())
    finally:
        if loop:
            loop.close()
        log_fh.close()
        _remove_pid()


async def _daemon_main() -> None:
    cfg = Settings.get_instance()
    db = Database(cfg.db_path)
    await db.connect()
    api = BiliAPIClient.get_instance()
    await api.start()
    state = MonitorState()
    sched = Scheduler(db, api, state)
    try:
        await sched.run()
    finally:
        await api.close()
        await db.close()
