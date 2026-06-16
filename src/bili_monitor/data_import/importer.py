from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from bili_monitor.db.database import Database
from bili_monitor.db.models import RecordData


@dataclass
class ImportResult:
    total: int = 0
    inserted: int = 0
    skipped: int = 0
    overwritten: int = 0
    errors: int = 0

    @property
    def summary(self) -> str:
        parts = [f"共 {self.total} 条"]
        if self.inserted:
            parts.append(f"新增 {self.inserted}")
        if self.overwritten:
            parts.append(f"覆盖 {self.overwritten}")
        if self.skipped:
            parts.append(f"跳过 {self.skipped}")
        if self.errors:
            parts.append(f"错误 {self.errors}")
        return ", ".join(parts)


def _detect_format(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".csv":
        return "csv"
    elif ext == ".json":
        return "json"
    raise ValueError(f"无法从扩展名 {ext} 推断格式，请使用 --format 指定")


def _parse_records_from_csv(
    path: Path, bvid: str
) -> list[tuple[str, RecordData]]:
    records: list[tuple[str, RecordData]] = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = row.get("timestamp", "").strip()
            if not ts:
                continue

            file_bvid = row.get("bvid", "").strip()
            if file_bvid and file_bvid != bvid:
                raise ValueError(
                    f"文件中的 BV 号 {file_bvid} 与指定的 {bvid} 不匹配"
                )

            try:
                data = RecordData(
                    views=_int_or(row.get("views"), 0),
                    likes=_int_or(row.get("likes"), 0),
                    coins=_int_or(row.get("coins"), 0),
                    favorites=_int_or(row.get("favorites"), 0),
                    danmaku=_int_or(row.get("danmaku")),
                    online=_int_or(row.get("online")),
                    shares=_int_or(row.get("shares")),
                    rank=_int_or(row.get("rank")),
                )
                records.append((ts, data))
            except (ValueError, TypeError):
                continue
    return records


def _parse_records_from_json(
    path: Path, bvid: str
) -> list[tuple[str, RecordData]]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    if not isinstance(raw, list):
        raise ValueError("JSON 文件应为数组格式")

    records: list[tuple[str, RecordData]] = []
    for item in raw:
        ts = str(item.get("timestamp", "")).strip()
        if not ts:
            continue

        file_bvid = str(item.get("bvid", "")).strip()
        if file_bvid and file_bvid != bvid:
            raise ValueError(
                f"文件中的 BV 号 {file_bvid} 与指定的 {bvid} 不匹配"
            )

        try:
            data = RecordData(
                views=_int_or(item.get("views"), 0),
                likes=_int_or(item.get("likes"), 0),
                coins=_int_or(item.get("coins"), 0),
                favorites=_int_or(item.get("favorites"), 0),
                danmaku=_int_or(item.get("danmaku")),
                online=_int_or(item.get("online")),
                shares=_int_or(item.get("shares")),
                rank=_int_or(item.get("rank")),
            )
            records.append((ts, data))
        except (ValueError, TypeError):
            continue

    return records


def _int_or(val, default=None) -> Optional[int]:
    if val is None or str(val).strip() == "":
        return default
    try:
        return int(float(str(val)))
    except (ValueError, TypeError):
        return default


async def import_records(
    path: Path,
    bvid: str,
    db: Database,
    format: Optional[str] = None,
    dry_run: bool = False,
    overwrite: bool = False,
) -> ImportResult:
    fmt = format or _detect_format(path)

    if fmt == "csv":
        parsed = _parse_records_from_csv(path, bvid)
    elif fmt == "json":
        parsed = _parse_records_from_json(path, bvid)
    else:
        raise ValueError(f"不支持的格式: {fmt}")

    if not parsed:
        return ImportResult()

    video = await db.find_video(bvid)
    if not video:
        raise ValueError(f"BV 号 {bvid} 不存在，请先 create")

    video_id = video["id"]
    result = ImportResult(total=len(parsed))

    for ts, data in parsed:
        exists = await db.record_exists(video_id, ts)

        if exists and not overwrite:
            result.skipped += 1
            continue

        if not dry_run:
            await db.upsert_record(video_id, ts, data)

        if exists and overwrite:
            result.overwritten += 1
        else:
            result.inserted += 1

    return result
