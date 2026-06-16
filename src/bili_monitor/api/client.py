from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

from bilibili_api.exceptions import (
    ApiException,
    NetworkException,
    ResponseCodeException,
)
from bilibili_api.video import Video as BiliVideo

from bili_monitor.db.models import RecordData

logger = logging.getLogger("bili_monitor.api")

BV_PATTERN = re.compile(r"BV[a-zA-Z0-9]{10,12}")

RECORD_FIELDS = {
    "view": "views",
    "like": "likes",
    "coin": "coins",
    "favorite": "favorites",
    "danmaku": "danmaku",
    "share": "shares",
    "now_rank": "rank",
    "reply": "reply",
    "his_rank": "his_rank",
}


def _safe_int(val: Any, default: int = 0) -> int:
    if val is None:
        return default
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _parse_count(val: Any) -> int:
    if val is None:
        return 0
    if isinstance(val, (int, float)):
        return int(val)
    s = str(val).strip().lower().rstrip("+")
    for suffix, mul in [("w", 10_000), ("万", 10_000), ("k", 1_000)]:
        if s.endswith(suffix):
            try:
                return int(float(s[: -len(suffix)]) * mul)
            except ValueError:
                return 0
    try:
        return int(s)
    except ValueError:
        return 0


@dataclass
class VideoMeta:
    bvid: str
    title: str
    uploader: str
    pubdate: Optional[int] = None
    duration: int = 0
    tname: str = ""


_global_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _global_semaphore
    if _global_semaphore is None:
        _global_semaphore = asyncio.Semaphore(1)
    return _global_semaphore


class BiliAPIClient:
    _instance: BiliAPIClient | None = None

    def __init__(self) -> None:
        self._started = False

    @classmethod
    def get_instance(cls) -> BiliAPIClient:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def start(self) -> None:
        self._started = True

    async def close(self) -> None:
        self._started = False
        BiliAPIClient._instance = None

    @staticmethod
    async def _request(coro):
        async with _get_semaphore():
            return await coro

    @staticmethod
    def resolve_bvid(text: str) -> str:
        clean = text.strip()
        match = BV_PATTERN.search(clean)
        if match:
            return match.group(0)
        raise ValueError(f"无法解析BV号: {text}")

    async def fetch_video_meta(self, bvid: str) -> VideoMeta:
        info = await self._fetch_info(bvid)
        owner = info.get("owner", {})
        return VideoMeta(
            bvid=bvid,
            title=info.get("title", ""),
            uploader=owner.get("name", ""),
            pubdate=info.get("pubdate"),
            duration=info.get("duration", 0),
            tname=info.get("tname", ""),
        )

    async def fetch_record_data(self, bvid: str) -> RecordData:
        info_task = self._fetch_info(bvid)
        online_task = self._fetch_online(bvid)
        results = await asyncio.gather(
            info_task, online_task, return_exceptions=True
        )

        info, online = results[0], results[1]
        data = RecordData()

        if isinstance(info, dict):
            stat = info.get("stat") or {}
            for api_key, record_key in RECORD_FIELDS.items():
                val = stat.get(api_key)
                if val is not None:
                    setattr(data, record_key, _safe_int(val))
        elif isinstance(info, BaseException):
            logger.warning("[%s] 获取视频信息失败: %s", bvid, info)

        if isinstance(online, dict):
            data.online = _parse_count(online.get("total"))
        elif isinstance(online, BaseException):
            logger.debug("[%s] 获取在线人数失败: %s", bvid, online)

        return data

    async def _fetch_info(self, bvid: str) -> dict:
        video = BiliVideo(bvid=bvid)
        try:
            return await self._request(video.get_info())
        except (NetworkException, ApiException, ResponseCodeException) as e:
            raise ValueError(f"获取视频信息失败 [{bvid}]: {e}") from e

    async def _fetch_online(self, bvid: str) -> Optional[dict]:
        video = BiliVideo(bvid=bvid)
        try:
            return await self._request(video.get_online())
        except (NetworkException, ApiException, ResponseCodeException):
            return None
