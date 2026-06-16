from __future__ import annotations

import asyncio
import collections
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional, Union

from bili_monitor.api.client import BiliAPIClient
from bili_monitor.config import Settings
from bili_monitor.db.database import Database

logger = logging.getLogger("bili_monitor.scheduler")


@dataclass
class TaskInfo:
    bvid: str
    title: str
    uploader: str
    video_id: int
    interval: int
    next_run: datetime
    last_run: Optional[datetime] = None
    error_count: int = 0
    active: bool = True


@dataclass
class TaskStatus:
    bvid: str
    title: str
    uploader: str
    active: bool
    interval: int
    last_run: Optional[str]
    record_count: int
    error_count: int


class MonitorState:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tasks: list[TaskStatus] = []
        self._running = True

    def snapshot(self) -> list[TaskStatus]:
        with self._lock:
            return list(self._tasks)

    def update(self, tasks: list[TaskStatus]) -> None:
        with self._lock:
            self._tasks = tasks

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    @running.setter
    def running(self, value: bool) -> None:
        with self._lock:
            self._running = value


Command = tuple[str, tuple, dict]


class CommandQueue:
    def __init__(self) -> None:
        self._queue: collections.deque[Command] = collections.deque()
        self._lock = threading.Lock()

    def put(self, action: str, args: tuple = (), kwargs: dict = None) -> None:
        with self._lock:
            self._queue.append((action, args, kwargs or {}))

    def drain(self) -> list[Command]:
        with self._lock:
            items = list(self._queue)
            self._queue.clear()
            return items


class Scheduler:
    def __init__(
        self,
        db: Database,
        api: BiliAPIClient,
        state: MonitorState,
        cmd_queue: CommandQueue | None = None,
    ) -> None:
        self._db = db
        self._api = api
        self._state = state
        self._cmd_queue = cmd_queue or CommandQueue()
        self._tasks: dict[str, TaskInfo] = {}
        self._running = False
        self._tick_interval = Settings.get_instance().tick_interval

    async def load_tasks(self) -> None:
        rows = await self._db.get_all_tasks()
        for row in rows:
            if not row.active:
                continue
            next_run = datetime.now()
            if row.last_record:
                last = datetime.fromisoformat(row.last_record)
                next_run = max(last + timedelta(seconds=row.interval), next_run)
            self._tasks[row.bvid] = TaskInfo(
                bvid=row.bvid,
                title=row.title,
                uploader=row.uploader,
                video_id=row.video_id,
                interval=row.interval,
                next_run=next_run,
            )
        if self._tasks:
            logger.info("已加载 %d 个活跃监控任务", len(self._tasks))

    async def add_task(self, bvid: str, interval: int) -> TaskInfo:
        meta = await self._api.fetch_video_meta(bvid)
        video_id = await self._db.upsert_video(bvid, meta.title, meta.uploader)
        await self._db.save_interval(video_id, interval)
        await self._db.set_video_active(video_id, True)
        task = TaskInfo(
            bvid=bvid,
            title=meta.title,
            uploader=meta.uploader,
            video_id=video_id,
            interval=interval,
            next_run=datetime.now(),
        )
        self._tasks[bvid] = task
        logger.info("已添加任务 [%s] %s (间隔 %ds)", bvid, meta.title, interval)
        return task

    async def remove_task(self, bvid: str) -> Optional[TaskInfo]:
        task = self._tasks.pop(bvid, None)
        if task:
            await self._db.set_video_active(task.video_id, False)
            await self._db.delete_interval(task.video_id)
            logger.info("已移除任务 [%s] %s", bvid, task.title)
        return task

    def get_task(self, bvid: str) -> Optional[TaskInfo]:
        return self._tasks.get(bvid)

    def list_tasks(self) -> list[TaskInfo]:
        return list(self._tasks.values())

    async def run(self) -> None:
        self._running = True
        await self.load_tasks()
        logger.info("调度器已启动 (tick=%gs)", self._tick_interval)
        try:
            while self._running:
                await self._process_commands()
                await self._tick()
                await self._sync_state()
                await asyncio.sleep(self._tick_interval)
        except asyncio.CancelledError:
            pass
        finally:
            await self._cleanup()

    def stop(self) -> None:
        self._running = False

    async def _tick(self) -> None:
        now = datetime.now()
        due = [
            t
            for t in self._tasks.values()
            if t.active and now >= t.next_run
        ]
        for task in due:
            try:
                await self._execute_task(task)
            except Exception:
                logger.exception("执行任务 %s 时异常", task.bvid)

    async def _execute_task(self, task: TaskInfo) -> None:
        data = await self._api.fetch_record_data(task.bvid)
        now = datetime.now()
        await self._db.insert_record(task.video_id, now, data)
        task.last_run = now
        task.next_run = now + timedelta(seconds=task.interval)
        task.error_count = 0
        logger.info(
            "[%s] %s ✓ %s播放 %s赞 %s币 %s收藏",
            task.bvid,
            task.title,
            _fmt(data.views),
            _fmt(data.likes),
            _fmt(data.coins),
            _fmt(data.favorites),
        )

    async def _sync_state(self) -> None:
        rows = await self._db.get_all_tasks()
        status_list = []
        for row in rows:
            task = self._tasks.get(row.bvid)
            err = task.error_count if task else 0
            status_list.append(
                TaskStatus(
                    bvid=row.bvid,
                    title=row.title,
                    uploader=row.uploader,
                    active=row.active,
                    interval=row.interval,
                    last_run=row.last_record,
                    record_count=row.record_count,
                    error_count=err,
                )
            )
        self._state.update(status_list)

    async def _process_commands(self) -> None:
        for action, args, kwargs in self._cmd_queue.drain():
            if action == "add":
                bvid, interval = args
                try:
                    await self.add_task(bvid, interval)
                    logger.info("[面板] 已添加 %s (间隔 %ds)", bvid, interval)
                except Exception as e:
                    logger.warning("[面板] 添加 %s 失败: %s", bvid, e)
            elif action == "remove":
                (bvid,) = args
                task = await self.remove_task(bvid)
                if task:
                    logger.info("[面板] 已移除 %s", bvid)
                else:
                    logger.warning("[面板] 未找到 %s", bvid)
            elif action == "resume":
                (bvid,) = args
                task = self._tasks.get(bvid)
                if task:
                    task.active = True
                    await self._db.set_video_active(task.video_id, True)
                    logger.info("[面板] 已恢复 %s", bvid)
            elif action == "pause":
                (bvid,) = args
                task = self._tasks.get(bvid)
                if task:
                    task.active = False
                    await self._db.set_video_active(task.video_id, False)

    async def _cleanup(self) -> None:
        self._state.running = False
        logger.info("调度器已停止")


def _fmt(n: int) -> str:
    if n >= 10_000:
        return f"{n / 10_000:.1f}万"
    return str(n)
