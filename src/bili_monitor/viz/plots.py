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


def _style_ax(ax, title: str, xlabel: str = "时间", date_axis: bool = True) -> None:
    ax.set_title(title, fontsize=12, loc="left", pad=14, fontweight="bold")
    ax.set_xlabel(xlabel)
    if date_axis:
        locator = mdates.AutoDateLocator()
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
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


def _aggregate_binned(deltas, minutes=60):
    """按指定间隔聚合所有 Δ 字段，返回排序后的 bucket 列表"""
    buckets: dict[str, dict] = {}
    for d in deltas:
        t = d["timestamp"]
        slot_minute = (t.minute // minutes) * minutes
        slot = t.replace(minute=slot_minute, second=0, microsecond=0)
        key = slot.isoformat()
        if key not in buckets:
            b = {"timestamp": slot}
            for k, v in d.items():
                if k.startswith("Δ"):
                    b[k] = 0
            buckets[key] = b
        for k in buckets[key]:
            if k.startswith("Δ"):
                buckets[key][k] += d.get(k, 0) if isinstance(d.get(k), (int, float)) else 0
    return sorted(buckets.values(), key=lambda x: x["timestamp"])


def _smooth(values, window=3):
    """SMA 平滑，处理 API 取整带来的尖峰噪声"""
    arr = np.array(values, dtype=float)
    if len(arr) < window:
        return arr
    w = np.ones(window) / window
    smoothed = np.convolve(arr, w, mode="same")
    half = window // 2
    for i in range(half):
        smoothed[i] = np.mean(arr[:i + half + 1])
        smoothed[-(i + 1)] = np.mean(arr[-(i + half + 1):])
    return smoothed


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
               framealpha=0.9, fontsize=9)
    _style_ax(ax, title)


# ── Chart 2: 互动增量 ─────────────────────────────────────────

def _chart_interaction_pulse(ax, hourly, title):
    metrics = ["Δlikes", "Δcoins", "Δfavorites", "Δdanmaku", "Δreply"]
    labels = [_CN[m.lstrip("Δ")] for m in metrics]
    colors = [_COLORS[m.lstrip("Δ")] for m in metrics]
    ts = [h["timestamp"] for h in hourly]

    for i, m in enumerate(metrics):
        raw = [h[m] for h in hourly]
        vals = _smooth(raw, 2)
        ax.plot(ts, vals, color=colors[i], linewidth=1.8,
                alpha=0.8, label=labels[i])

    ax.set_ylabel("每15分钟增量", fontsize=10)
    ax.legend(loc="upper left", framealpha=0.9, fontsize=8)
    _style_ax(ax, title)
    ax.set_xlabel("")


# ── Chart 3: 互动转化效率 HDS ─────────────────────────────────

def _chart_hds(ax, hourly, weights, title):
    hds_raw = []
    ts = []
    for h in hourly:
        dv = h["Δviews"]
        if dv <= 0:
            continue
        numer = 0.0
        for wkey, dfkey in HDS_TO_DELTA.items():
            w = weights.get(wkey, 0)
            if w:
                numer += w * max(h.get(dfkey, 0), 0)
        hds_raw.append(numer / max(dv, 1))
        ts.append(h["timestamp"])

    if not hds_raw:
        ax.text(0.5, 0.5, "数据不足", ha="center", va="center", fontsize=14, color="#999")
        _style_ax(ax, title)
        return

    hds_vals = _smooth(hds_raw, 2)

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

def _chart_conversion(ax, binned, rows, timestamps, title):
    ts, like_rate, coin_rate, fav_rate, coin_like = [], [], [], [], []
    for b in binned:
        dv = b["Δviews"]
        dl = b["Δlikes"]
        dc = b["Δcoins"]
        df = b["Δfavorites"]
        if dv <= 0:
            continue
        like_rate.append(dl / dv)
        coin_rate.append(dc / dv)
        fav_rate.append(df / dv)
        coin_like.append(dc / max(dl, 1))
        ts.append(b["timestamp"])

    if not ts:
        ax.text(0.5, 0.5, "数据不足", ha="center", va="center", fontsize=14, color="#999")
        _style_ax(ax, title)
        return

    cum_views = [r["views"] or 1 for r in rows]
    cum_likes = [r["likes"] or 0 for r in rows]
    cum_coins = [r["coins"] or 0 for r in rows]
    cum_favs  = [r["favorites"] or 0 for r in rows]

    cum_like_curve = [l / max(v, 1) for l, v in zip(cum_likes, cum_views)]
    cum_coin_curve = [c / max(v, 1) for c, v in zip(cum_coins, cum_views)]
    cum_fav_curve  = [f / max(v, 1) for f, v in zip(cum_favs, cum_views)]
    cum_cl_curve   = [c / max(l, 1) for c, l in zip(cum_coins, cum_likes)]

    ax.plot(ts, like_rate, color=_COLORS["likes"], linewidth=1.8,
            alpha=0.85, label="点赞率 (10min)")
    ax.plot(timestamps, cum_like_curve, color=_COLORS["likes"], linewidth=1.2,
            linestyle="--", alpha=0.5, label="点赞率 累计")

    ax.plot(ts, coin_rate, color=_COLORS["coins"], linewidth=1.8,
            alpha=0.85, label="投币率 (10min)")
    ax.plot(timestamps, cum_coin_curve, color=_COLORS["coins"], linewidth=1.2,
            linestyle="--", alpha=0.5, label="投币率 累计")

    ax.plot(ts, fav_rate, color=_COLORS["favorites"], linewidth=1.8,
            alpha=0.85, label="收藏率 (10min)")
    ax.plot(timestamps, cum_fav_curve, color=_COLORS["favorites"], linewidth=1.2,
            linestyle="--", alpha=0.5, label="收藏率 累计")

    ax.set_ylabel("播放比值", fontsize=10)

    ax2 = ax.twinx()
    ax2.plot(ts, coin_like, color=_COLORS["danmaku"], linewidth=1.8,
             alpha=0.85, label="投币/点赞 (10min)")
    ax2.plot(timestamps, cum_cl_curve, color=_COLORS["danmaku"], linewidth=1.2,
             linestyle="--", alpha=0.5, label="投币/点赞 累计")
    ax2.set_ylabel("投币/点赞", fontsize=10)
    ax2.yaxis.label.set_color(_COLORS["danmaku"])
    ax2.tick_params(axis="y", colors=_COLORS["danmaku"])

    for curve_x, curve_y, color, axis, y_off in [
        (ts, like_rate, _COLORS["likes"], ax, 2),
        (ts, coin_rate, _COLORS["coins"], ax, 0),
        (ts, fav_rate, _COLORS["favorites"], ax, -2),
        (ts, coin_like, _COLORS["danmaku"], ax2, 0),
        (timestamps, cum_like_curve, _COLORS["likes"], ax, -2),
        (timestamps, cum_coin_curve, _COLORS["coins"], ax, -4),
        (timestamps, cum_fav_curve, _COLORS["favorites"], ax, -6),
        (timestamps, cum_cl_curve, _COLORS["danmaku"], ax2, -2),
    ]:
        if curve_x and curve_y:
            axis.annotate(f"{curve_y[-1]:.3f}", xy=(curve_x[-1], curve_y[-1]),
                          xytext=(2, y_off), textcoords="offset points",
                          fontsize=5.5, color=color, va="center", alpha=0.95,
                          bbox=dict(boxstyle="round,pad=0.1", facecolor="white",
                                  edgecolor=color, alpha=0.7, lw=0.5))

    l1, lb1 = ax.get_legend_handles_labels()
    l2, lb2 = ax2.get_legend_handles_labels()
    ax.legend(l1 + l2, lb1 + lb2, loc="upper left",
              framealpha=0.9, fontsize=7, ncol=2)
    _style_ax(ax, title)


# ── Chart 5: 观看留存率 ────────────────────────────────────────
# Uses raw rows for online, not deltas

def _chart_vdr_from_rows(ax, rows, deltas, duration, title):
    if not duration:
        ax.text(0.5, 0.5, "使用 `update xxx --refresh-meta`\n补全视频时长后即可生成",
                ha="center", va="center", fontsize=12, color="#999")
        _style_ax(ax, title)
        return

    vdr_vals, ts = [], []
    acc_dt, acc_views, start_online = 0, 0, None
    end_online, last_ts = 0, None
    for d in deltas:
        if d["Δviews"] <= 0:
            continue
        idx = d["i"]
        online_prev = rows[idx - 1]["online"] or 0
        online_curr = rows[idx]["online"] or 0
        if online_prev <= 0 or online_curr <= 0:
            continue
        if start_online is None:
            start_online = online_prev
            acc_dt, acc_views = 0, 0
        acc_dt += d["dt"]
        acc_views += d["Δviews"]
        end_online = online_curr
        last_ts = d["timestamp"]
        if acc_dt >= duration:
            online_avg = (start_online + end_online) / 2
            if online_avg > 0:
                expected = online_avg * acc_dt / duration
                if expected > 0:
                    vdr = acc_views / expected
                    vdr_vals.append(min(vdr, 5))
                    ts.append(last_ts)
            start_online = None

    if len(vdr_vals) < 3:
        ax.text(0.5, 0.5, "数据不足 (需 ≥3 个 Δt≥视频时长的有效间隔)",
                ha="center", va="center", fontsize=13, color="#999")
        _style_ax(ax, title)
        return

    ax.plot(ts, vdr_vals, color="#4C78A8", linewidth=2,
            marker="o", markersize=3.5, alpha=0.8, label="VDR", zorder=3)
    ax.fill_between(ts, 1, vdr_vals,
                     where=(np.array(vdr_vals) >= 1),
                     color="#54A24B", alpha=0.15, interpolate=True)
    ax.fill_between(ts, vdr_vals, 1,
                     where=(np.array(vdr_vals) < 1),
                     color="#E45756", alpha=0.15, interpolate=True)

    ax.axhline(y=1, color="#333333", linewidth=1.2, linestyle="--",
               alpha=0.7, label="基准 (VDR=1)")

    avg_vdr = np.mean(vdr_vals)
    ax.axhline(y=avg_vdr, color="#999999", linewidth=1,
               linestyle=":", alpha=0.6, label=f"均值 {avg_vdr:.2f}")

    ax.set_ylabel("VDR (<5)", fontsize=10)
    ax.legend(loc="upper left", framealpha=0.9, fontsize=9)
    ax.axhline(y=0, color="#cccccc", linewidth=0.8)

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

    online_count = sum(1 for r in rows if r["online"] is not None)
    if online_count < 5:
        ax.text(0.5, 0.5, f"在线人数数据不足 (当前 {online_count} 条, 需要 ≥5 条)",
                ha="center", va="center", fontsize=12, color="#999")
        _style_ax(ax, title)
        return

    stay_vals, ts = [], []
    for d in deltas:
        if d["Δviews"] <= 0:
            continue
        idx = d["i"]
        online_prev = rows[idx - 1]["online"] or 0
        online_curr = rows[idx]["online"] or 0
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


# ── Chart 7: 累计绝对值趋势 ───────────────────────────────────

def _chart_cumulative_totals(ax, rows, timestamps, title):
    likes = [r["likes"] or 0 for r in rows]
    coins = [r["coins"] or 0 for r in rows]
    favs = [r["favorites"] or 0 for r in rows]

    ax.plot(timestamps, likes, color=_COLORS["likes"], linewidth=1.8,
            alpha=0.85, label="点赞总量")
    ax.plot(timestamps, coins, color=_COLORS["coins"], linewidth=1.8,
            alpha=0.85, label="投币总量")
    ax.set_ylabel("点赞 · 投币", fontsize=10)

    ax2 = ax.twinx()
    ax2.plot(timestamps, favs, color=_COLORS["favorites"], linewidth=1.8,
             alpha=0.85, label="收藏总量")

    last_likes = likes[-1] if likes else 0
    last_coins = coins[-1] if coins else 0
    last_favs = favs[-1] if favs else 0
    ax2.set_ylabel("收藏", fontsize=10)
    ax2.yaxis.label.set_color(_COLORS["favorites"])
    ax2.tick_params(axis="y", colors=_COLORS["favorites"])
    ax2.text(0.98, 0.08, f"L={last_likes/10000:.1f}万  C={last_coins/10000:.1f}万  F={last_favs/10000:.1f}万",
             transform=ax.transAxes, ha="right", va="bottom",
             fontsize=9, color="#666",
             bbox=dict(boxstyle="round,pad=0.3", facecolor="#f0f0f0",
                       edgecolor="#ddd", alpha=0.8))

    l1, lb1 = ax.get_legend_handles_labels()
    l2, lb2 = ax2.get_legend_handles_labels()
    ax.legend(l1 + l2, lb1 + lb2, loc="upper left",
              framealpha=0.9, fontsize=9)
    _style_ax(ax, title)
    ax.set_xlabel("")


# ── Report generator ───────────────────────────────────────────

_CHART_REGISTRY: list[tuple[str, callable, int, str]] = [
    ("01_播放与互动", _chart_trend, 0, "播放量(左) + 点赞·投币(右)"),
    ("02_互动增量", _chart_interaction_pulse, 0, "每小时各互动指标的增量变化 (平滑)"),
    ("03_互动转化效率", _chart_hds, 0, "Σ(权重×互动)÷播放，越高互动转化越好 (平滑)"),
    ("04_三连率", _chart_conversion, 0, "点赞/投币/收藏÷播放(左) + 投币/点赞(右)"),
    ("05_观看留存率", _chart_vdr_from_rows, 0, "实际播放÷在线期望，Δt≥视频时长"),
    ("06_平均观看时长", _chart_avg_stay, 0, "单次观看秒数，红线 = 视频全长"),
    ("07_累计绝对值趋势", _chart_cumulative_totals, 0, "点赞/投币(左) 收藏(右) 总量增长"),
]


async def generate_report(
    bvid: str,
    video_id: int,
    db: Database,
    name: str = "",
    output: Optional[Path] = None,
    weights: Optional[dict] = None,
    duration: Optional[int] = None,
    videos: int = 1,
) -> list[Path]:
    cfg = Settings.get_instance()
    rows = await db.get_records(video_id)
    if not rows:
        raise ValueError(f"[{bvid}] 没有记录数据")

    rows = rows[::-1]
    weights = weights or DEFAULT_WEIGHTS.copy()
    timestamps = _ts(rows)
    deltas = _deltas(rows)
    deltas = [d for d in deltas if d["dt"] > 120]
    binned_10 = _aggregate_binned(deltas, 10)
    binned_30 = _aggregate_binned(deltas, 30)
    eff_duration = duration // max(videos, 1) if duration else None

    base_dir = output or _report_dir(cfg, bvid, name, rows)

    name_label = name or bvid
    generated: list[Path] = []

    usable = [(cn, fn, mr, ex) for cn, fn, mr, ex in _CHART_REGISTRY
              if len(deltas) >= mr]
    if not usable:
        return generated

    with Progress() as progress:
        task = progress.add_task(
            f"生成 {len(usable)} 张图表",
            total=len(usable),
        )

        for chart_name, func, min_records, explanation in usable:
            progress.update(task, description=f"正在生成 {chart_name}...")

            fig, ax = plt.subplots(figsize=_FIGSIZE)
            ts_range = f"{timestamps[0].strftime('%m-%d %H:%M')} ~ {timestamps[-1].strftime('%m-%d %H:%M')}  [{len(rows)}条记录]"
            title = f"{name_label} · {chart_name}\n{ts_range} · {explanation}"

            try:
                if chart_name in ("05_观看留存率", "06_平均观看时长"):
                    func(ax, rows, deltas, eff_duration, title)
                elif chart_name == "03_互动转化效率":
                    func(ax, binned_30, weights, title)
                elif chart_name == "02_互动增量":
                    func(ax, binned_30, title)
                elif chart_name == "04_三连率":
                    func(ax, binned_10, rows, timestamps, title)
                elif chart_name in ("07_累计绝对值趋势", "01_播放与互动"):
                    func(ax, rows, timestamps, title)
                else:
                    func(ax, deltas, title)

                _footer(fig)
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
