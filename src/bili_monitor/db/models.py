from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

VIDEOS_TABLE = """
CREATE TABLE IF NOT EXISTS videos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bvid        TEXT UNIQUE NOT NULL,
    name        TEXT UNIQUE NOT NULL,
    title       TEXT NOT NULL,
    uploader    TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    active      INTEGER NOT NULL DEFAULT 0,
    pubdate     TEXT
)
"""

ADD_NAME_COL = "ALTER TABLE videos ADD COLUMN name TEXT"
BACKFILL_NAME = "UPDATE videos SET name = bvid WHERE name IS NULL"
ADD_PUBDATE_COL = "ALTER TABLE videos ADD COLUMN pubdate TEXT"

RECORDS_TABLE = """
CREATE TABLE IF NOT EXISTS records (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id    INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    timestamp   TEXT    NOT NULL,
    views       INTEGER,
    likes       INTEGER,
    coins       INTEGER,
    favorites   INTEGER,
    danmaku     INTEGER,
    online      INTEGER,
    shares      INTEGER,
    rank        INTEGER
)
"""

RECORDS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_records_video_time
ON records(video_id, timestamp)
"""

TASK_INTERVALS_TABLE = """
CREATE TABLE IF NOT EXISTS task_intervals (
    video_id  INTEGER PRIMARY KEY,
    interval  INTEGER NOT NULL
)
"""

CHECK_SIZE_SQL = """
SELECT page_count * page_size AS size_bytes
FROM pragma_page_count, pragma_page_size
"""

OLD_RECORDS_SQL = """
SELECT COUNT(*) FROM records
WHERE timestamp < datetime('now', ? || ' days', 'localtime')
"""

DELETE_OLD_RECORDS = """
DELETE FROM records
WHERE timestamp < datetime('now', ? || ' days', 'localtime')
"""


@dataclass
class RecordData:
    views: int = 0
    likes: int = 0
    coins: int = 0
    favorites: int = 0
    danmaku: Optional[int] = None
    online: Optional[int] = None
    shares: Optional[int] = None
    rank: Optional[int] = None


@dataclass
class TaskRow:
    video_id: int
    bvid: str
    name: str
    title: str
    uploader: str
    active: bool
    interval: int
    created_at: str
    record_count: int
    last_record: Optional[str]
    pubdate: Optional[str] = None


@dataclass
class RecordRow:
    timestamp: str
    views: int
    likes: int
    coins: int
    favorites: int
    danmaku: Optional[int]
    online: Optional[int]
    shares: Optional[int]
    rank: Optional[int]
