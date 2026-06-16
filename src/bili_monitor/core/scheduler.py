from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from bili_monitor.api.client import BiliAPIClient
from bili_monitor.config import Settings
from bili_monitor.db.database import Database

logger = logging.getLogger("bili_monitor.scheduler")

_reload_requested = False


def request_reload() -> None:
    global _reload_requested
    _reload_requested = True


@dataclass
class TaskInfo:
    bvid: str
    name: str
    title: str
    uploader: str
    video_id: int
    interval: int
    next_run: datetime
    last_run: Optional[datetime] = None
    error_count: int = 0
    active: bool = True


class Scheduler:
    def __init__(
        self,
        db: Database,
        api: BiliAPIClient,
    ) -> None:
        self._db = db
        self._api = api
        self._tasks: dict[str, TaskInfo] = {}
        self._running = False
        self._tick_interval = Settings.get_instance().tick_interval

    async def load_tasks(self) -> None:
        rows = await self._db.get_all_tasks()
        for row in rows:
            if not row.active:
                continue
            self._tasks[row.bvid] = TaskInfo(
                bvid=row.bvid,
                name=row.name,
                title=row.title,
                uploader=row.uploader,
                video_id=row.video_id,
                interval=row.interval,
                next_run=datetime.now(),
            )
        if self._tasks:
            logger.info("已加载 %d 个活跃监控任务", len(self._tasks))

    async def activate_task(self, bvid: str, interval: int) -> TaskInfo:
        meta = await self._api.fetch_video_meta(bvid)
        row = await self._db.find_video(bvid)
        if not row:
            raise ValueError(f"视频 {bvid} 不存在，请先 create")
        await self._db.save_interval(row["id"], interval)
        await self._db.set_video_active(row["id"], True)
        task = TaskInfo(
            bvid=bvid,
            name=row["name"],
            title=meta.title,
            uploader=meta.uploader,
            video_id=row["id"],
            interval=interval,
            next_run=datetime.now(),
        )
        self._tasks[bvid] = task
        logger.info("已激活 [%s] %s (间隔 %ds)", bvid, meta.title, interval)
        return task

    async def deactivate_task(self, bvid: str) -> Optional[TaskInfo]:
        task = self._tasks.pop(bvid, None)
        row = await self._db.find_video(bvid)
        if row:
            await self._db.set_video_active(row["id"], False)
            await self._db.delete_interval(row["id"])
        return task

    async def update_task(
        self, bvid: str, interval: Optional[int], name: Optional[str]
    ) -> Optional[TaskInfo]:
        row = await self._db.find_video(bvid)
        if not row:
            return None
        if interval is not None:
            await self._db.save_interval(row["id"], interval)
        if name is not None:
            await self._db.update_name(row["id"], name)
        task = self._tasks.get(bvid)
        if task:
            if interval is not None:
                task.interval = interval
            if name is not None:
                task.name = name
        return task

    async def _check_external_changes(self) -> None:
        global _reload_requested
        if not _reload_requested:
            return
        _reload_requested = False
        rows = await self._db.get_all_tasks()
        db_map = {r.bvid: r for r in rows}
        for bvid, task in list(self._tasks.items()):
            db_row = db_map.get(bvid)
            if db_row is None:
                self._tasks.pop(bvid, None)
                logger.info("[同步] %s 已从 DB 删除", bvid)
            elif db_row.active != task.active:
                task.active = db_row.active
                if task.active:
                    task.next_run = datetime.now()
                    logger.info("[同步] %s 已激活", bvid)
                else:
                    logger.info("[同步] %s 已停用", bvid)
            else:
                changed = False
                if db_row.interval != task.interval:
                    task.interval = db_row.interval
                    task.next_run = datetime.now()
                    changed = True
                if db_row.name != task.name:
                    task.name = db_row.name
                    changed = True
                if db_row.title != task.title:
                    task.title = db_row.title
                    changed = True
                if db_row.uploader != task.uploader:
                    task.uploader = db_row.uploader
                    changed = True
                if changed:
                    logger.info("[同步] %s 参数已更新", bvid)

    async def run(self) -> None:
        self._running = True
        await self.load_tasks()
        logger.info("调度器已启动 (tick=%gs)", self._tick_interval)
        tick_count = 0
        try:
            while self._running:
                await self._tick()
                tick_count += 1
                if tick_count % 15 == 0 or _reload_requested:
                    await self._check_external_changes()
                await asyncio.sleep(self._tick_interval)
        except asyncio.CancelledError:
            pass
        finally:
            logger.info("调度器已停止")

    def stop(self) -> None:
        self._running = False

    async def _tick(self) -> None:
        now = datetime.now()
        due = [
            t for t in self._tasks.values()
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
            task.bvid, task.title,
            _fmt(data.views), _fmt(data.likes),
            _fmt(data.coins), _fmt(data.favorites),
        )


def _fmt(n: int) -> str:
    if n >= 10_000:
        return f"{n / 10_000:.1f}万"
    return str(n)
