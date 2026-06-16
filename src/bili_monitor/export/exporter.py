from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from bili_monitor.config import Settings
from bili_monitor.db.database import Database


def _auto_path(bvid: str, fmt: str, base_dir: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return base_dir / f"{bvid}_{ts}.{fmt}"


async def export_records(
    bvid: str,
    video_id: int,
    fmt: str,
    db: Database,
    output: Optional[Path] = None,
) -> Path:
    cfg = Settings.get_instance()
    out = output or _auto_path(bvid, fmt, cfg.export_dir)
    out.parent.mkdir(parents=True, exist_ok=True)

    rows = await db.get_records(video_id)
    field_names = [
        "timestamp", "views", "likes", "coins", "favorites",
        "danmaku", "online", "shares", "rank",
    ]

    if fmt == "csv":
        await _export_csv(out, rows, field_names)
    elif fmt == "json":
        await _export_json(out, rows, field_names)
    else:
        raise ValueError(f"不支持的导出格式: {fmt}")

    return out


async def _export_csv(
    path: Path, rows, field_names: list[str]
) -> None:
    def _write():
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(field_names)
            for r in rows:
                w.writerow([r[f] for f in field_names])
    import asyncio
    await asyncio.to_thread(_write)


async def _export_json(
    path: Path, rows, field_names: list[str]
) -> None:
    def _write():
        data = [
            {f: r[f] for f in field_names}
            for r in rows
        ]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    import asyncio
    await asyncio.to_thread(_write)
