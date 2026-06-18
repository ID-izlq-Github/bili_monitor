from __future__ import annotations

import asyncio
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from bili_monitor.config import Settings
from bili_monitor.db.database import Database

META_FIELDS = ["title", "uploader", "name", "pubdate", "duration", "tname"]
RECORD_FIELDS = [
    "bvid", "timestamp", "views", "likes", "coins", "favorites",
    "danmaku", "online", "shares", "rank", "reply", "his_rank",
]


def _auto_path(bvid: str, fmt: str, base_dir: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return base_dir / f"{bvid}_{ts}.{fmt}"


def _extract_meta(row: dict[str, Any]) -> dict[str, Any]:
    return {k: row[k] for k in META_FIELDS if k in row}


async def export_records(
    bvid: str,
    video_id: int,
    fmt: str,
    db: Database,
    output: Optional[Path] = None,
    meta: Optional[dict[str, Any]] = None,
) -> Path:
    cfg = Settings.get_instance()
    out = output or _auto_path(bvid, fmt, cfg.export_dir)
    out.parent.mkdir(parents=True, exist_ok=True)

    rows = await db.get_records(video_id)

    if fmt == "csv":
        await _export_csv(out, rows, bvid, meta or {})
    elif fmt == "json":
        await _export_json(out, rows, bvid, meta or {})
    else:
        raise ValueError(f"不支持的导出格式: {fmt}")

    return out


async def _export_csv(
    path: Path, rows, bvid: str, meta: dict[str, Any]
) -> None:
    def _write():
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(RECORD_FIELDS + META_FIELDS)
            for r in rows:
                w.writerow(
                    [bvid] + [r[f] for f in RECORD_FIELDS[1:]] +
                    [meta.get(k, "") for k in META_FIELDS]
                )
    await asyncio.to_thread(_write)


async def _export_json(
    path: Path, rows, bvid: str, meta: dict[str, Any]
) -> None:
    def _write():
        records = [
            {"bvid": bvid, **{f: r[f] for f in RECORD_FIELDS[1:]}}
            for r in rows
        ]
        payload: dict[str, Any] = {"meta": meta, "records": records}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    await asyncio.to_thread(_write)
