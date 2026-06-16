from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.dates import DateFormatter
from matplotlib.font_manager import FontProperties

from bili_monitor.config import Settings
from bili_monitor.db.database import Database

_PLOT_TYPES = ["trend", "subplot", "delta", "ratio"]

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
    "#4E79A7", "#F28E2B", "#E15759", "#76B7B2",
    "#59A14F", "#EDC948", "#B07AA1", "#FF9DA7",
]

_FONT = FontProperties(
    family=["HarmonyOS Sans SC", "Noto Sans CJK SC", "WenQuanYi Micro Hei", "DejaVu Sans"]
)
plt.rcParams["font.family"] = _FONT.get_name()
plt.rcParams["axes.unicode_minus"] = False


def _safe_name(name: str) -> str:
    return re.sub(r'[/\\:*?"<>|]', "_", name)


def _fmt_ts(ts_str: str) -> str:
    try:
        return datetime.fromisoformat(ts_str).strftime("%Y%m%d_%H%M%S")
    except (ValueError, TypeError):
        return datetime.now().strftime("%Y%m%d_%H%M%S")


def _build_output_path(
    cfg: Settings, bvid: str, name: str, plot_type: str, rows
) -> Path:
    last_ts = _fmt_ts(rows[0]["timestamp"]) if rows else datetime.now().strftime("%Y%m%d_%H%M%S")
    dir_path = cfg.image_dir / f"{bvid}-{_safe_name(name)}" / last_ts
    dir_path.mkdir(parents=True, exist_ok=True)
    return dir_path / f"{plot_type}.png"


def _decorate_ax(
    ax, title: str, subtitle: str, xlabel: str = "时间",
) -> None:
    ax.set_title(f"{title}\n{subtitle}", fontsize=11, loc="left", pad=12)
    ax.set_xlabel(xlabel)
    ax.xaxis.set_major_formatter(DateFormatter("%m-%d %H:%M"))
    ax.grid(True, alpha=0.25)


async def generate_plot(
    bvid: str,
    video_id: int,
    db: Database,
    metrics: list[str],
    plot_type: str,
    output: Optional[Path] = None,
    name: str = "",
) -> Path:
    cfg = Settings.get_instance()
    rows = await db.get_records(video_id)
    if not rows:
        raise ValueError(f"[{bvid}] 没有记录数据")

    valid = [m for m in metrics if m in _FIELD_LABELS]
    if not valid:
        valid = ["views", "likes", "coins"]

    out = output or _build_output_path(cfg, bvid, name, plot_type, rows)
    out.parent.mkdir(parents=True, exist_ok=True)

    ts_range = f"{_parse_time(rows[-1]).strftime('%m-%d %H:%M')} ~ {_parse_time(rows[0]).strftime('%m-%d %H:%M')}"
    plot_title = f"{name or bvid} — {len(rows)} 条记录"

    if plot_type == "trend":
        fig, ax = plt.subplots(figsize=(12, 6))
        _plot_trend(ax, rows, valid, plot_title, ts_range)
    elif plot_type == "subplot":
        fig = _plot_subplots(rows, valid, plot_title, ts_range)
    elif plot_type == "delta":
        fig, ax = plt.subplots(figsize=(12, 6))
        _plot_delta(ax, rows, valid, plot_title, ts_range)
    elif plot_type == "ratio":
        fig, ax = plt.subplots(figsize=(12, 6))
        _plot_ratio(ax, rows, valid, plot_title, ts_range)
    else:
        fig, ax = plt.subplots(figsize=(12, 6))
        _plot_trend(ax, rows, valid, plot_title, ts_range)

    _add_footer(fig)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return out


def _add_footer(fig) -> None:
    fig.text(
        0.99, 0.005,
        f"BiliMonitor · {datetime.now():%Y-%m-%d %H:%M}",
        ha="right", va="bottom",
        fontsize=8, color="#999999",
    )


def _parse_time(row) -> datetime:
    return datetime.fromisoformat(row["timestamp"])


def _plot_trend(ax, rows, metrics: list[str], title: str, subtitle: str) -> None:
    timestamps = [_parse_time(r) for r in rows]
    ax2 = None
    range0 = None

    for i, m in enumerate(metrics):
        vals = [r[m] or 0 for r in rows]
        target_ax = ax

        if i == 0:
            rng = max(vals) - min(vals) if max(vals) != min(vals) else 1
            range0 = rng
        elif range0 and range0 > 0:
            rng = max(vals) - min(vals) if max(vals) != min(vals) else 1
            if range0 / max(rng, 1) > 10:
                if ax2 is None:
                    ax2 = ax.twinx()
                target_ax = ax2

        target_ax.plot(
            timestamps, vals,
            label=_FIELD_LABELS.get(m, m),
            color=_COLORS[i % len(_COLORS)],
            linewidth=1.5, marker="o", markersize=3, alpha=0.85,
        )

    lines1, labels1 = ax.get_legend_handles_labels()
    if ax2:
        lines2, labels2 = ax2.get_legend_handles_labels()
        legend = ax.legend(lines1 + lines2, labels1 + labels2,
                           loc="upper left", framealpha=0.9)
        ax2.set_ylabel("数值（右轴）")
    else:
        legend = ax.legend(loc="upper left", framealpha=0.9)

    ax.set_ylabel("数值")
    _decorate_ax(ax, title, subtitle)


def _plot_subplots(rows, metrics: list[str], title: str, subtitle: str) -> plt.Figure:
    n = len(metrics)
    fig, axes = plt.subplots(n, 1, figsize=(12, 3 * n), sharex=True)
    if n == 1:
        axes = [axes]

    timestamps = [_parse_time(r) for r in rows]
    for i, (ax, m) in enumerate(zip(axes, metrics)):
        vals = [r[m] or 0 for r in rows]
        ax.plot(
            timestamps, vals,
            color=_COLORS[i % len(_COLORS)],
            linewidth=1.5, marker="o", markersize=3, alpha=0.85,
        )
        ax.set_ylabel(_FIELD_LABELS.get(m, m))
        ax.grid(True, alpha=0.25)
        if i < n - 1:
            ax.tick_params(labelbottom=False)

    axes[-1].xaxis.set_major_formatter(DateFormatter("%m-%d %H:%M"))
    fig.suptitle(f"{title}\n{subtitle}", fontsize=12, x=0.03, ha="left", y=0.995)
    fig.subplots_adjust(hspace=0.08)
    return fig


def _plot_delta(ax, rows, metrics: list[str], title: str, subtitle: str) -> None:
    timestamps = [_parse_time(r) for r in rows]
    for i, m in enumerate(metrics):
        vals = [r[m] or 0 for r in rows]
        deltas = [0.0] + [vals[j] - vals[j - 1] for j in range(1, len(vals))]
        ax.plot(
            timestamps, deltas,
            label=_FIELD_LABELS.get(m, m),
            color=_COLORS[i % len(_COLORS)],
            linewidth=1.5, marker="o", markersize=3, alpha=0.85,
        )
    ax.axhline(y=0, color="#333333", linewidth=0.8, linestyle="--", alpha=0.5)
    ax.set_ylabel("增量")
    legend = ax.legend(loc="upper left", framealpha=0.9)
    _decorate_ax(ax, title, subtitle)


def _plot_ratio(ax, rows, metrics: list[str], title: str, subtitle: str) -> None:
    base_metric = "views" if "views" in metrics else metrics[0]
    others = [m for m in metrics if m != base_metric]
    if not others:
        others = metrics[1:] if len(metrics) > 1 else ["likes"]

    timestamps = [_parse_time(r) for r in rows]
    base_vals = [r[base_metric] or 1 for r in rows]

    for i, m in enumerate(others[:6]):
        vals = [(r[m] or 0) / max(b, 1) for r, b in zip(rows, base_vals)]
        ax.plot(
            timestamps, vals,
            label=f"{_FIELD_LABELS.get(m, m)}/{_FIELD_LABELS.get(base_metric, base_metric)}",
            color=_COLORS[i % len(_COLORS)],
            linewidth=1.5, marker="o", markersize=3, alpha=0.85,
        )

    ax.set_ylabel("比值")
    legend = ax.legend(loc="upper left", framealpha=0.9)
    _decorate_ax(ax, title, subtitle)
