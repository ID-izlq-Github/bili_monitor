from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.font_manager import FontProperties
import numpy as np

from rich.progress import Progress

from bili_monitor.config import Settings
from bili_monitor.db.database import Database

logger = logging.getLogger("bili_monitor.viz")


# ── Color palette ──────────────────────────────────────────────

_COLORS: dict[str, str] = {
    "views": "#4C78A8",
    "likes": "#F58518",
    "coins": "#E45756",
    "favorites": "#72B7B2",
    "danmaku": "#54A24B",
    "online": "#B279A2",
    "shares": "#FF9DA6",
    "reply": "#9D7556",
    "rank": "#BAB0AC",
}

_CN: dict[str, str] = {
    "views": "播放量",
    "likes": "点赞",
    "coins": "投币",
    "favorites": "收藏",
    "danmaku": "弹幕",
    "online": "在线观看",
    "shares": "转发",
    "reply": "评论",
    "rank": "排名",
    "likes+coins+favorites": "三连",
}

_FONT = FontProperties(
    family=["HarmonyOS Sans SC", "Noto Sans CJK SC",
            "WenQuanYi Micro Hei", "DejaVu Sans"]
)
plt.rcParams["font.family"] = _FONT.get_name()
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["axes.edgecolor"] = "#cccccc"
plt.rcParams["axes.grid"] = True
plt.rcParams["grid.alpha"] = 0.3
plt.rcParams["grid.linestyle"] = "--"

# ── Weights ────────────────────────────────────────────────────

DEFAULT_WEIGHTS: dict[str, float] = {
    "coin": 0.4,
    "favorite": 0.3,
    "danmaku": 0.4,
    "reply": 0.4,
    "view": 0.25,
    "like": 0.4,
    "share": 0.6,
}

# HDS uses all weights EXCEPT view (to avoid self-referencing)
HDS_METRICS = ["like", "coin", "favorite", "danmaku", "reply", "share"]
# Mapping from weight key to DB field name (Δ-field)
HDS_TO_DELTA = {m: f"Δ{m}" for m in HDS_METRICS}
# Override: DB field "favorites" maps to weight key "favorite"
HDS_TO_DELTA["favorite"] = "Δfavorites"
HDS_TO_DELTA["coin"] = "Δcoins"


def load_weights(path: Optional[Path] = None) -> dict[str, float]:
    if path and path.exists():
        with open(path, encoding="utf-8") as f:
            user = json.load(f)
        weights = DEFAULT_WEIGHTS.copy()
        weights.update(user)
        return weights
    return DEFAULT_WEIGHTS.copy()


# ── Path helpers ───────────────────────────────────────────────

_ILLEGAL = re.compile(r'[/\\:*?"<>|]')


def _safe_name(name: str) -> str:
    return _ILLEGAL.sub("_", name)


def _fmt_ts(dt: datetime) -> str:
    return dt.strftime("%Y%m%d_%H%M%S")


def _build_ts(dt: datetime) -> str:
    return dt.strftime("%Y%m%d_%H%M%S")


def _report_dir(cfg: Settings, bvid: str, name: str, rows) -> Path:
    last = datetime.fromisoformat(rows[-1]["timestamp"])
    d = cfg.image_dir / f"{bvid}-{_safe_name(name)}" / _fmt_ts(last)
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Plot style helpers ─────────────────────────────────────────

_FIGSIZE = (14, 6)


def _style_ax(ax, title: str, xlabel: str = "时间") -> None:
    ax.set_title(title, fontsize=12, loc="left", pad=14, fontweight="bold")
    ax.set_xlabel(xlabel)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    ax.tick_params(labelsize=9)


def _footer(fig) -> None:
    fig.text(
        0.99, 0.005,
        f"BiliMonitor · {datetime.now():%Y-%m-%d %H:%M}",
        ha="right", va="bottom",
        fontsize=7, color="#aaaaaa",
    )


def _ts(rows) -> list[datetime]:
    return [datetime.fromisoformat(r["timestamp"]) for r in rows]


# ── Delta computation ─────────────────────────────────────────

def _deltas(rows):
    n = len(rows)
    deltas = []
    for i in range(1, n):
        t0 = datetime.fromisoformat(rows[i - 1]["timestamp"])
        t1 = datetime.fromisoformat(rows[i]["timestamp"])
        dt = max((t1 - t0).total_seconds(), 1)
        d = {"i": i, "timestamp": t1, "dt": dt}
        for field in ["views", "likes", "coins", "favorites",
                       "danmaku", "shares", "reply", "online"]:
            v0 = rows[i - 1][field] or 0
            v1 = rows[i][field] or 0
            d[f"Δ{field}"] = v1 - v0
        deltas.append(d)
    return deltas


def _sparse_xticks(ax, labels, max_labels=10):
    n = len(labels)
    step = max(1, (n + max_labels - 1) // max_labels)
    indices = list(range(0, n, step))
    ax.set_xticks(indices)
    ax.set_xticklabels([labels[i] for i in indices],
                       rotation=30, ha="right", fontsize=7)


def _aggregate_hourly(deltas):
    from collections import OrderedDict
    buckets: dict[str, dict] = {}
    for d in deltas:
        h = d["timestamp"].replace(minute=0, second=0, microsecond=0)
        key = h.isoformat()
        if key not in buckets:
            buckets[key] = {"timestamp": h, "Δviews": 0, "Δshares": 0, "count": 0}
        buckets[key]["Δviews"] += d["Δviews"]
        buckets[key]["Δshares"] += d["Δshares"]
        buckets[key]["count"] += 1
    return sorted(buckets.values(), key=lambda x: x["timestamp"])


# ── Chart 1: 播放与互动 ───────────────────────────────────────

def _chart_trend(ax, rows, timestamps, title):
    view_vals = [r["views"] or 0 for r in rows]
    ax.fill_between(timestamps, view_vals, alpha=0.08, color=_COLORS["views"])
    ax.plot(timestamps, view_vals, color=_COLORS["views"],
            linewidth=2, label=_CN["views"], zorder=3)
    ax.set_ylabel(_CN["views"], fontsize=10)
    ax.yaxis.label.set_color(_COLORS["views"])
    ax.tick_params(axis="y", colors=_COLORS["views"])

    ax2 = ax.twinx()
    for metric, key in [("likes", "likes"), ("coins", "coins")]:
        vals = [r[metric] or 0 for r in rows]
        ax2.plot(timestamps, vals, color=_COLORS[key],
                 linewidth=1.5, marker="o", markersize=2.5,
                 alpha=0.85, label=_CN[key])
    ax2.relim()
    ax2.autoscale()
    ax2.set_ylabel("点赞 · 投币", fontsize=10)

    l1, lb1 = ax.get_legend_handles_labels()
    l2, lb2 = ax2.get_legend_handles_labels()
    ax.legend(l1 + l2, lb1 + lb2, loc="upper left",
              framealpha=0.9, fontsize=9, zorder=5)
    _style_ax(ax, title)


# ── Chart 2: 互动增量 ─────────────────────────────────────────

def _chart_interaction_pulse(ax, deltas, title):
    metrics = ["Δlikes", "Δcoins", "Δfavorites", "Δdanmaku", "Δreply"]
    labels = [_CN[m.lstrip("Δ")] for m in metrics]
    colors = [_COLORS[m.lstrip("Δ")] for m in metrics]
    ts = [d["timestamp"] for d in deltas]

    vals = []
    for m in metrics:
        vals.append([max(d[m], 0) for d in deltas])

    ax.stackplot(ts, vals, labels=labels, colors=colors, alpha=0.8)
    _sparse_xticks(ax, [t.strftime("%m-%d %H:%M") for t in ts])
    ax.set_ylabel("增量", fontsize=10)
    ax.legend(loc="upper left", framealpha=0.9, fontsize=8)
    _style_ax(ax, title)
    ax.set_xlabel("")


# ── Chart 3: 加权质量指数 HDS ─────────────────────────────────

def _chart_hds(ax, deltas, weights, title):
    hds_vals = []
    ts = []
    for d in deltas:
        if d["Δviews"] <= 0:
            continue
        numer = 0.0
        for wkey, dfkey in HDS_TO_DELTA.items():
            w = weights.get(wkey, 0)
            if w:
                numer += w * max(d.get(dfkey, 0), 0)
        hds = numer / max(d["Δviews"], 1)
        hds_vals.append(hds)
        ts.append(d["timestamp"])

    if not hds_vals:
        ax.text(0.5, 0.5, "数据不足", ha="center", va="center", fontsize=14, color="#999")
        _style_ax(ax, title)
        return

    ax.plot(ts, hds_vals, color="#E45756", linewidth=1.8,
            marker="o", markersize=4, alpha=0.8, label="HDS", zorder=3)

    # Moving average
    window = max(3, min(7, len(hds_vals) // 3))
    ma = np.convolve(hds_vals, np.ones(window) / window, mode="valid")
    ma_ts = ts[window - 1:]
    ax.plot(ma_ts, ma, color="#4C78A8", linewidth=2.2,
            alpha=0.7, label=f"{window}期移动平均", zorder=4)

    # Cumulative mean
    cum_mean = np.cumsum(hds_vals) / np.arange(1, len(hds_vals) + 1)
    ax.plot(ts, cum_mean, color="#999999", linewidth=1.2,
            linestyle="--", alpha=0.6, label="累计均值", zorder=2)

    # Anomaly markers
    arr = np.array(hds_vals)
    q1, q3 = np.percentile(arr, 25), np.percentile(arr, 75)
    iqr = q3 - q1
    upper, lower = q3 + 1.5 * iqr, q1 - 1.5 * iqr
    anomalies = [i for i, v in enumerate(hds_vals) if v > upper or v < lower]
    if anomalies:
        ax.scatter([ts[i] for i in anomalies],
                   [hds_vals[i] for i in anomalies],
                   color="#E45756", s=60, marker="*",
                   zorder=5, label="异常点", edgecolors="white", linewidths=0.5)

    ax.axhline(y=0, color="#cccccc", linewidth=0.8, linestyle="--")
    ax.set_ylabel("HDS 互动深度", fontsize=10)
    ax.legend(loc="upper left", framealpha=0.9, fontsize=9)
    _style_ax(ax, title)


# ── Chart 4: 三连率 ───────────────────────────────────────────

def _chart_conversion(ax, deltas, title):
    metrics = ["Δlikes", "Δcoins", "Δfavorites"]
    labels = [_CN[m.lstrip("Δ")] + "/播放" for m in metrics]
    colors = [_COLORS[m.lstrip("Δ")] for m in metrics]

    ts, groups = [], []
    for d in deltas:
        if d["Δviews"] <= 0:
            continue
        vals = [max(d[m], 0) / max(d["Δviews"], 1) for m in metrics]
        groups.append(vals)
        ts.append(d["timestamp"])

    if not groups:
        ax.text(0.5, 0.5, "数据不足", ha="center", va="center", fontsize=14, color="#999")
        _style_ax(ax, title)
        return

    for i in range(len(metrics)):
        line_vals = [g[i] for g in groups]
        ax.plot(ts, line_vals, color=colors[i], linewidth=1.8,
                marker="o", markersize=3, alpha=0.8, label=labels[i])

    _sparse_xticks(ax, [t.strftime("%m-%d %H:%M") for t in ts])
    ax.set_ylabel("比值", fontsize=10)
    ax.legend(loc="upper left", framealpha=0.9, fontsize=9)
    ax.axhline(y=0, color="#cccccc", linewidth=0.8, linestyle="--")
    _style_ax(ax, title)
    ax.set_xlabel("")


# ── Chart 5: 观看留存率 ────────────────────────────────────────
# Uses raw rows for online, not deltas

def _chart_vdr_from_rows(ax, rows, deltas, duration, title):
    if not duration:
        ax.text(0.5, 0.5, "使用 `update xxx --refresh-meta`\n补全视频时长后即可生成",
                ha="center", va="center", fontsize=12, color="#999")
        _style_ax(ax, title)
        return

    has_online = any(r.get("online") is not None for r in rows)
    if not has_online:
        ax.text(0.5, 0.5, "暂无在线人数数据\n(新记录会自动采集)",
                ha="center", va="center", fontsize=12, color="#999")
        _style_ax(ax, title)
        return

    vdr_vals, ts = [], []
    for i, d in enumerate(deltas):
        if d["Δviews"] <= 0:
            continue
        idx = d["i"]
        online_prev = rows[idx - 1].get("online") or 0
        online_curr = rows[idx].get("online") or 0
        expected = (online_prev + online_curr) / 2 * d["dt"] / duration
        if expected <= 0:
            continue
        vdr = d["Δviews"] / expected
        vdr_vals.append(min(vdr, 10))
        ts.append(d["timestamp"])

    if not vdr_vals:
        ax.text(0.5, 0.5, "数据不足", ha="center", va="center",
                fontsize=13, color="#999")
        _style_ax(ax, title)
        return

    colors = ["#54A24B" if v >= 1 else "#E45756" for v in vdr_vals]
    x = np.arange(len(vdr_vals))
    bars = ax.bar(x, vdr_vals, color=colors, alpha=0.8, width=0.6,
                  edgecolor="white", linewidth=0.5)
    ax.axhline(y=1, color="#333333", linewidth=1.2, linestyle="--",
               alpha=0.7, label="基准 (VDR=1)")

    ax.set_xticks(x)
    ax.set_xticklabels([t.strftime("%m-%d %H:%M") for t in ts],
                       rotation=30, ha="right", fontsize=7)
    ax.set_ylabel("VDR", fontsize=10)
    ax.legend(loc="upper left", framealpha=0.9, fontsize=9)
    ax.axhline(y=0, color="#cccccc", linewidth=0.8)

    avg_vdr = np.mean(vdr_vals)
    ax.text(0.98, 0.95, f"均值 VDR={avg_vdr:.2f}",
            transform=ax.transAxes, ha="right", va="top",
            fontsize=9, color="#666",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="#f0f0f0",
                      edgecolor="#ddd", alpha=0.8))

    _style_ax(ax, title)
    ax.set_xlabel("")


# ── Chart 6: 平均观看时长 ──────────────────────────────────────

def _chart_avg_stay(ax, rows, deltas, duration, title):
    if not duration:
        ax.text(0.5, 0.5, "使用 `update xxx --refresh-meta`\n补全视频时长后即可生成",
                ha="center", va="center", fontsize=12, color="#999")
        _style_ax(ax, title)
        return

    has_online = any(r.get("online") is not None for r in rows)
    if not has_online:
        ax.text(0.5, 0.5, "暂无在线人数数据\n(新记录会自动采集)",
                ha="center", va="center", fontsize=12, color="#999")
        _style_ax(ax, title)
        return

    stay_vals, ts = [], []
    for d in deltas:
        if d["Δviews"] <= 0:
            continue
        idx = d["i"]
        online_prev = rows[idx - 1].get("online") or 0
        online_curr = rows[idx].get("online") or 0
        integral = (online_prev + online_curr) / 2 * d["dt"]
        stay = integral / max(d["Δviews"], 1)
        stay_vals.append(min(stay, duration * 3))
        ts.append(d["timestamp"])

    if not stay_vals:
        ax.text(0.5, 0.5, "数据不足", ha="center", va="center",
                fontsize=13, color="#999")
        _style_ax(ax, title)
        return

    ax.plot(ts, stay_vals, color="#4C78A8", linewidth=2,
            marker="o", markersize=4, alpha=0.85, label="平均停留", zorder=3)
    ax.fill_between(ts, stay_vals, alpha=0.08, color="#4C78A8")

    ax.axhline(y=duration, color="#E45756", linewidth=1.8,
               linestyle="--", alpha=0.7, label=f"视频全长 ({duration:.0f}s)")

    ax.set_ylabel("停留时长 (秒)", fontsize=10)
    ax.legend(loc="upper left", framealpha=0.9, fontsize=9)

    avg_stay = np.mean(stay_vals)
    ax.text(0.98, 0.05, f"平均 {avg_stay:.0f}s / 全长 {duration:.0f}s "
            f"({avg_stay / duration * 100:.1f}%)",
            transform=ax.transAxes, ha="right", va="bottom",
            fontsize=9, color="#666",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="#f0f0f0",
                      edgecolor="#ddd", alpha=0.8))

    _style_ax(ax, title)


# ── Chart 7: 传播效率 ─────────────────────────────────────────

def _chart_scatter(ax, rows, deltas, duration, title):
    if not duration:
        ax.text(0.5, 0.5, "使用 `update xxx --refresh-meta`\n补全视频时长后即可生成",
                ha="center", va="center", fontsize=12, color="#999")
        _style_ax(ax, title)
        return

    has_online = any(r.get("online") is not None for r in rows)
    if not has_online:
        ax.text(0.5, 0.5, "暂无在线人数数据\n(新记录会自动采集)",
                ha="center", va="center", fontsize=12, color="#999")
        _style_ax(ax, title)
        return

    xs, ys, sizes, colors, labels = [], [], [], [], []
    for d in deltas:
        if d["Δviews"] < 0:
            continue
        idx = d["i"]
        online_avg = ((rows[idx - 1].get("online") or 0) +
                      (rows[idx].get("online") or 0)) / 2
        x = online_avg / (2 * duration)
        y = d["Δviews"] / max(d["dt"], 1)
        xs.append(x)
        ys.append(y)
        sizes.append(max(abs(d.get("Δshares", 0)), 1))
        colors.append(d["timestamp"])
        labels.append(d["timestamp"].strftime("%m-%d %H:%M"))

    if len(xs) < 3:
        ax.text(0.5, 0.5, "数据不足", ha="center", va="center",
                fontsize=13, color="#999")
        _style_ax(ax, title)
        return

    sizes_norm = [max(s * 30 / max(sizes), 15) for s in sizes]
    sc = ax.scatter(xs, ys, s=sizes_norm, c=range(len(xs)),
                    cmap="viridis", alpha=0.7, edgecolors="white",
                    linewidths=0.5, zorder=3)

    max_val = max(max(xs), max(ys)) * 1.1
    line = np.linspace(0, max_val, 100)
    ax.plot(line, line, color="#999999", linewidth=1,
            linestyle="--", alpha=0.5, label="y=x (理想)")

    if len(xs) >= 4:
        A = np.vstack([xs, np.ones(len(xs))]).T
        m, c = np.linalg.lstsq(A, ys, rcond=None)[0]
        ax.plot(line, m * line + c, color="#E45756", linewidth=1.5,
                alpha=0.6, label=f"回归 y={m:.2f}x+{c:.0f}")

    ax.set_xlabel("等效完播密度 (次/秒)", fontsize=10)
    ax.set_ylabel("实际播放增速 (次/秒)", fontsize=10)
    ax.legend(loc="upper left", framealpha=0.9, fontsize=9)
    _style_ax(ax, title)


# ── Chart 8: 分享传播影响 ─────────────────────────────────────

def _chart_share_lag(ax, deltas, title):
    if len(deltas) < 4:
        ax.text(0.5, 0.5, "数据不足 (至少需要 4 个采样间隔)",
                ha="center", va="center", fontsize=13, color="#999")
        _style_ax(ax, title)
        return

    hourly = _aggregate_hourly(deltas)
    ts = [h["timestamp"] for h in hourly]
    dviews = [h["Δviews"] for h in hourly]
    dshares = [h["Δshares"] for h in hourly]

    ax.plot(ts, dviews, color=_COLORS["views"], linewidth=2,
            alpha=0.85, label="播放增量", zorder=3)

    ax2 = ax.twinx()
    shifted = [0] + dshares[:-1]
    ax2.plot(ts, shifted, color=_COLORS["shares"], linewidth=1.8,
             alpha=0.8, linestyle="--", label="转发增量 (前置1小时)", zorder=2)

    best_shift = 0
    best_r = 0
    for s in range(3):
        if s >= len(dviews):
            break
        a = np.array(dviews[s:])
        b = np.array(dshares[:len(dviews) - s])
        if len(a) < 3 or np.std(a) < 1e-9 or np.std(b) < 1e-9:
            continue
        r = np.corrcoef(a, b)[0, 1]
        if abs(r) > abs(best_r):
            best_r = r
            best_shift = s

    ax.text(0.98, 0.95, f"最佳时滞: {best_shift}小时 (r={best_r:.3f})",
            transform=ax.transAxes, ha="right", va="top",
            fontsize=10, color="#333",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="#f0f0f0",
                      edgecolor="#ddd", alpha=0.8))

    ax.set_ylabel("播放增量 (次/小时)", fontsize=10, color=_COLORS["views"])
    ax.tick_params(axis="y", colors=_COLORS["views"])
    ax2.set_ylabel("转发增量 (前置)", fontsize=10, color=_COLORS["shares"])
    ax2.tick_params(axis="y", colors=_COLORS["shares"])

    l1, lb1 = ax.get_legend_handles_labels()
    l2, lb2 = ax2.get_legend_handles_labels()
    ax.legend(l1 + l2, lb1 + lb2, loc="upper left", framealpha=0.9, fontsize=9,
              zorder=5)
    _style_ax(ax, title)


# ── Report generator ───────────────────────────────────────────

_CHART_REGISTRY: list[tuple[str, callable, int]] = [
    ("01_播放与互动", _chart_trend, 0),
    ("02_互动增量", _chart_interaction_pulse, 0),
    ("03_互动转化效率", _chart_hds, 0),
    ("04_三连率", _chart_conversion, 0),
    ("05_观看留存率", _chart_vdr_from_rows, 0),
    ("06_平均观看时长", _chart_avg_stay, 0),
    ("07_传播效率", _chart_scatter, 15),
    ("08_分享传播影响", _chart_share_lag, 20),
]


async def generate_report(
    bvid: str,
    video_id: int,
    db: Database,
    name: str = "",
    output: Optional[Path] = None,
    weights: Optional[dict] = None,
    duration: Optional[int] = None,
) -> list[Path]:
    cfg = Settings.get_instance()
    rows = await db.get_records(video_id)
    if not rows:
        raise ValueError(f"[{bvid}] 没有记录数据")

    rows = rows[::-1]
    weights = weights or DEFAULT_WEIGHTS.copy()
    timestamps = _ts(rows)
    deltas = _deltas(rows)

    base_dir = output or _report_dir(cfg, bvid, name, rows)

    name_label = name or bvid
    generated: list[Path] = []

    usable = [(cn, fn, mr) for cn, fn, mr in _CHART_REGISTRY
              if len(deltas) >= mr]
    if not usable:
        return generated

    with Progress() as progress:
        task = progress.add_task(
            f"生成 {len(usable)} 张图表",
            total=len(usable),
        )

        for chart_name, func, min_records in usable:
            progress.update(task, description=f"正在生成 {chart_name}...")

            fig, ax = plt.subplots(figsize=_FIGSIZE)
            ts_range = f"{timestamps[0].strftime('%m-%d %H:%M')} ~ {timestamps[-1].strftime('%m-%d %H:%M')}  [{len(rows)}条记录]"
            title = f"{name_label} · {chart_name}\n{ts_range}"

            try:
                if chart_name in ("05_观看留存率", "06_平均观看时长"):
                    func(ax, rows, deltas, duration, title)
                elif chart_name == "03_互动转化效率":
                    func(ax, deltas, weights, title)
                elif chart_name == "07_传播效率":
                    func(ax, rows, deltas, duration, title)
                elif chart_name in ("01_播放与互动",):
                    func(ax, rows, timestamps, title)
                else:
                    func(ax, deltas, title)

                _footer(fig)
                fig.autofmt_xdate()
                fig.tight_layout()
                out_path = base_dir / f"{chart_name}.png"
                fig.savefig(out_path, dpi=200, bbox_inches="tight")
                generated.append(out_path)
            except Exception as exc:
                logger.warning("[%s] %s 生成失败: %s", bvid, chart_name, exc)
            finally:
                plt.close(fig)
                progress.advance(task)

    return generated
