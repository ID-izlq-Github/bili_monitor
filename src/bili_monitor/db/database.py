from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

import asyncio

from bili_monitor.config import Settings
from bili_monitor.db.models import (
    RECORDS_INDEX,
    RECORDS_TABLE,
    VIDEOS_TABLE,
    TASK_INTERVALS_TABLE,
    CHECK_SIZE_SQL,
    DELETE_OLD_RECORDS,
    OLD_RECORDS_SQL,
    RecordData,
    TaskRow,
)


class Database:
    def __init__(self, db_path: Path | None = None) -> None:
        cfg = Settings.get_instance()
        self._db_path = db_path or cfg.db_path
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._db_path), check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        await self._init_tables()

    async def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    async def _init_tables(self) -> None:
        await self._execute(VIDEOS_TABLE)
        await self._execute(RECORDS_TABLE)
        await self._execute(RECORDS_INDEX)
        await self._execute(TASK_INTERVALS_TABLE)

    async def _execute(
        self, sql: str, params: tuple = ()
    ) -> sqlite3.Cursor:
        async with self._lock:
            return await asyncio.to_thread(
                self._conn.execute, sql, params
            )

    async def _fetchone(
        self, sql: str, params: tuple = ()
    ) -> Optional[sqlite3.Row]:
        async with self._lock:
            cur = await asyncio.to_thread(
                self._conn.execute, sql, params
            )
            return cur.fetchone()

    async def _fetchall(
        self, sql: str, params: tuple = ()
    ) -> list[sqlite3.Row]:
        async with self._lock:
            cur = await asyncio.to_thread(
                self._conn.execute, sql, params
            )
            return cur.fetchall()

    async def _commit(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._conn.commit)

    # ── Video CRUD ──────────────────────────────────────────────

    async def upsert_video(
        self, bvid: str, title: str, uploader: str
    ) -> int:
        await self._execute(
            "INSERT OR IGNORE INTO videos (bvid, title, uploader) "
            "VALUES (?, ?, ?)",
            (bvid, title, uploader),
        )
        row = await self._fetchone(
            "SELECT id FROM videos WHERE bvid = ?", (bvid,)
        )
        return row["id"] if row else 0

    async def update_video_title(
        self, video_id: int, title: str, uploader: str
    ) -> None:
        await self._execute(
            "UPDATE videos SET title = ?, uploader = ? WHERE id = ?",
            (title, uploader, video_id),
        )
        await self._commit()

    async def set_video_active(
        self, video_id: int, active: bool
    ) -> None:
        await self._execute(
            "UPDATE videos SET active = ? WHERE id = ?",
            (1 if active else 0, video_id),
        )
        await self._commit()

    async def delete_video(self, video_id: int) -> None:
        await self._execute(
            "DELETE FROM records WHERE video_id = ?", (video_id,)
        )
        await self._execute(
            "DELETE FROM videos WHERE id = ?", (video_id,)
        )
        await self._commit()

    async def get_all_tasks(self) -> list[TaskRow]:
        rows = await self._fetchall(
            "SELECT v.id, v.bvid, v.title, v.uploader, v.active, "
            "COALESCE(i.interval, ?) AS interval, "
            "v.created_at, "
            "COUNT(r.id) AS record_count, "
            "MAX(r.timestamp) AS last_record "
            "FROM videos v "
            "LEFT JOIN records r ON r.video_id = v.id "
            "LEFT JOIN task_intervals i ON i.video_id = v.id "
            "GROUP BY v.id "
            "ORDER BY v.created_at DESC",
            (Settings.get_instance().default_interval,),
        )
        return [
            TaskRow(
                video_id=row["id"],
                bvid=row["bvid"],
                title=row["title"],
                uploader=row["uploader"],
                active=bool(row["active"]),
                interval=row["interval"],
                created_at=row["created_at"],
                record_count=row["record_count"],
                last_record=row["last_record"],
            )
            for row in rows
        ]

    # ── Record CRUD ─────────────────────────────────────────────

    async def insert_record(
        self, video_id: int, timestamp: datetime, data: RecordData
    ) -> None:
        await self._execute(
            "INSERT INTO records (video_id, timestamp, views, likes, "
            "coins, favorites, danmaku, online, shares, rank) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                video_id,
                timestamp.isoformat(),
                data.views,
                data.likes,
                data.coins,
                data.favorites,
                data.danmaku,
                data.online,
                data.shares,
                data.rank,
            ),
        )
        await self._commit()

    async def get_records(
        self,
        video_id: int,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> list[sqlite3.Row]:
        sql = "SELECT * FROM records WHERE video_id = ? ORDER BY timestamp ASC"
        params: tuple = (video_id,)
        if limit is not None:
            sql += " LIMIT ?"
            params = (*params, limit)
        if offset is not None:
            sql += " OFFSET ?"
            params = (*params, offset)
        return await self._fetchall(sql, params)

    async def get_record_count(self, video_id: int) -> int:
        row = await self._fetchone(
            "SELECT COUNT(*) AS cnt FROM records WHERE video_id = ?",
            (video_id,),
        )
        return row["cnt"] if row else 0

    # ── Task interval persistence ───────────────────────────────

    async def save_interval(
        self, video_id: int, interval: int
    ) -> None:
        await self._execute(
            "INSERT OR REPLACE INTO task_intervals (video_id, interval) "
            "VALUES (?, ?)",
            (video_id, interval),
        )
        await self._commit()

    async def delete_interval(self, video_id: int) -> None:
        await self._execute(
            "DELETE FROM task_intervals WHERE video_id = ?",
            (video_id,),
        )

    # ── Cleanup ─────────────────────────────────────────────────

    async def get_db_size_bytes(self) -> int:
        row = await self._fetchone(CHECK_SIZE_SQL)
        return row["size_bytes"] if row else 0

    async def count_old_records(self, days: int) -> int:
        row = await self._fetchone(OLD_RECORDS_SQL, (str(days),))
        return row[0] if row else 0

    async def delete_old_records(self, days: int) -> int:
        await self._execute(DELETE_OLD_RECORDS, (str(days),))
        await self._commit()
        return self._conn.total_changes if self._conn else 0
