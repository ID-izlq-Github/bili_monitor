from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns
from matplotlib.dates import DateFormatter
from matplotlib.font_manager import FontProperties

from bili_monitor.config import Settings
from bili_monitor.db.database import Database

sns.set_style("whitegrid")

_FONT = FontProperties(
    family=["HarmonyOS Sans SC", "Noto Sans CJK SC", "WenQuanYi Micro Hei", "DejaVu Sans"]
)
plt.rcParams["font.family"] = _FONT.get_name()
plt.rcParams["axes.unicode_minus"] = False

_FIELD_LABELS = {
    "views": "播放量",
    "likes": "点赞",
    "coins": "投币",
    "favorites": "收藏",
    "danmaku": "弹幕",
    "online": "在线观看",
    "shares": "转发",
    "rank": "排名",
}

_COLORS = [
    "#2196F3", "#FF5722", "#4CAF50", "#FF9800",
    "#9C27B0", "#00BCD4", "#E91E63", "#607D8B",
]


async def generate_plot(
    bvid: str,
    video_id: int,
    db: Database,
    metrics: list[str],
    plot_type: str,
    output: Optional[Path] = None,
) -> Path:
    cfg = Settings.get_instance()
    out = output or (
        cfg.image_dir / f"{bvid}_{plot_type}_{datetime.now():%Y%m%d_%H%M%S}.png"
    )
    out.parent.mkdir(parents=True, exist_ok=True)

    rows = await db.get_records(video_id)
    if not rows:
        raise ValueError(f"[{bvid}] 没有记录数据")

    valid = [m for m in metrics if m in _FIELD_LABELS]
    if not valid:
        valid = ["views", "likes", "coins"]

    fig, ax = plt.subplots(figsize=(12, 6))

    if plot_type == "trend":
        _plot_trend(ax, rows, valid)
    elif plot_type == "ratio":
        _plot_ratio(ax, rows, valid)
    else:
        _plot_trend(ax, rows, valid)

    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return out


def _parse_time(row) -> datetime:
    return datetime.fromisoformat(row["timestamp"])


def _plot_trend(ax, rows, metrics: list[str]) -> None:
    timestamps = [_parse_time(r) for r in rows]
    for i, m in enumerate(metrics):
        vals = [r[m] or 0 for r in rows]
        ax.plot(
            timestamps, vals,
            label=_FIELD_LABELS.get(m, m),
            color=_COLORS[i % len(_COLORS)],
            linewidth=1.5,
            alpha=0.85,
        )
    ax.set_xlabel("时间")
    ax.set_ylabel("数值")
    ax.legend()
    ax.xaxis.set_major_formatter(DateFormatter("%m-%d %H:%M"))


def _plot_ratio(ax, rows, metrics: list[str]) -> None:
    timestamps = [_parse_time(r) for r in rows]
    if "views" in metrics and len(metrics) >= 2:
        base = [r["views"] or 1 for r in rows]
        others = [m for m in metrics if m != "views"]
        for i, m in enumerate(others[:4]):
            vals = [(r[m] or 0) / max(b, 1) for r, b in zip(rows, base)]
            ax.plot(
                timestamps, vals,
                label=f"{_FIELD_LABELS.get(m, m)}/播放",
                color=_COLORS[i % len(_COLORS)],
                linewidth=1.5,
                alpha=0.85,
            )
    ax.set_xlabel("时间")
    ax.set_ylabel("比值")
    ax.legend()
    ax.xaxis.set_major_formatter(DateFormatter("%m-%d %H:%M"))
