import asyncio
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from bili_monitor.data_import.importer import import_records
from bili_monitor.db.database import Database
from bili_monitor.db.models import RecordData


async def _test_roundtrip_csv():
    db_path = Path(tempfile.mktemp(suffix=".db"))
    csv_path = Path(tempfile.mktemp(suffix=".csv"))
    try:
        db = Database(db_path)
        await db.connect()

        bvid = "BV1xxTEST"
        video_id = await db.create_video(bvid, "test_video", "Test Title", "Test Uploader")
        assert video_id > 0

        data1 = RecordData(views=100, likes=10, coins=5, favorites=20, danmaku=30, online=50, shares=2, rank=1, reply=3, his_rank=10)
        data2 = RecordData(views=200, likes=20, coins=10, favorites=40, danmaku=60, online=100, shares=4, rank=2, reply=8, his_rank=15)

        ts1 = datetime(2026, 1, 1, 12, 0, 0)
        ts2 = datetime(2026, 1, 2, 12, 0, 0)

        await db.insert_record(video_id, ts1, data1)
        await db.insert_record(video_id, ts2, data2)

        from bili_monitor.export.exporter import export_records
        exported = await export_records(bvid, video_id, "csv", db, output=csv_path)
        assert exported.exists()

        content = csv_path.read_text(encoding="utf-8")
        assert "bvid" in content
        assert bvid in content

        video_id2 = await db.create_video("BV2xxIMPORT", "import_target", "Target", "Tester")
        csv_path2 = Path(tempfile.mktemp(suffix=".csv"))
        csv_path2.write_text(
            "bvid,timestamp,views,likes,coins,favorites,danmaku,online,shares,rank\n"
            "BV2xxIMPORT,2026-01-01T12:00:00,100,10,5,20,30,50,2,1\n"
            "BV2xxIMPORT,2026-01-02T12:00:00,200,20,10,40,60,100,4,2\n",
            encoding="utf-8",
        )

        result = await import_records(csv_path2, "BV2xxIMPORT", db)
        assert result.total == 2
        assert result.inserted == 2
        assert result.skipped == 0

        records = await db.get_records(video_id2)
        assert len(records) == 2

        await db.close()
    finally:
        db_path.unlink(missing_ok=True)
        csv_path.unlink(missing_ok=True)


async def _test_roundtrip_json():
    db_path = Path(tempfile.mktemp(suffix=".db"))
    json_path = Path(tempfile.mktemp(suffix=".json"))
    try:
        db = Database(db_path)
        await db.connect()

        bvid = "BV1xxJSON"
        video_id = await db.create_video(bvid, "json_test", "JSON Title", "Uploader")
        data = RecordData(views=500, likes=50, coins=25, favorites=100, danmaku=150, online=200, shares=10, rank=3, reply=12, his_rank=50)
        await db.insert_record(video_id, datetime(2026, 6, 1, 0, 0, 0), data)

        from bili_monitor.export.exporter import export_records
        await export_records(bvid, video_id, "json", db, output=json_path)

        raw = json.loads(json_path.read_text(encoding="utf-8"))
        assert len(raw) == 1
        assert raw[0]["bvid"] == bvid
        assert raw[0]["views"] == 500

        video_id2 = await db.create_video("BV2xxJSON", "json_target", "Target", "Tester")
        result = await import_records(json_path, bvid="BV2xxJSON", db=db)
        assert result.inserted == 1

        await db.close()
    finally:
        db_path.unlink(missing_ok=True)
        json_path.unlink(missing_ok=True)


def test_roundtrip_csv():
    asyncio.run(_test_roundtrip_csv())


def test_roundtrip_json():
    asyncio.run(_test_roundtrip_json())
