from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from bili_monitor.api.client import BiliAPIClient
from bili_monitor.config import Settings
from bili_monitor.daemon.daemon import DaemonManager
from bili_monitor.db.database import Database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bili_monitor.cli")
console = Console()

app = typer.Typer(
    name="bili-monitor",
    help="Bilibili 视频数据监控工具",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)


async def _init() -> tuple[Database, BiliAPIClient]:
    cfg = Settings.get_instance()
    db = Database(cfg.db_path)
    await db.connect()
    api = BiliAPIClient.get_instance()
    await api.start()
    return db, api


async def _cleanup(db: Database, api: BiliAPIClient) -> None:
    await db.close()
    await api.close()


def _find_bvid(api: BiliAPIClient, raw: str) -> str:
    try:
        return BiliAPIClient.resolve_bvid(raw)
    except ValueError:
        return raw


def _auto_name(raw: str) -> str:
    ts = datetime.now().strftime("%H%M%S")
    return f"bili_{ts}"


# ── create ─────────────────────────────────────────────────────


@app.command()
def create(
    bvid: str = typer.Argument(..., help="BV号或视频URL"),
    name: str = typer.Option("", "--name", "-n", help="别名（不传则自动生成）"),
    interval: int = typer.Option(
        900, "--interval", "-i",
        min=60, help="记录间隔（秒），大于3600时会二次确认",
    ),
    inactive: bool = typer.Option(
        False, "--inactive", help="创建后不自动激活",
    ),
):
    """注册新视频到系统"""
    asyncio.run(_cmd_create(bvid, name, interval, inactive))


async def _cmd_create(
    raw: str, name: str, interval: int, inactive: bool
) -> None:
    db, api = await _init()
    try:
        bvid = BiliAPIClient.resolve_bvid(raw)
        exist = await db.find_video(bvid)
        if exist:
            console.print(f"[red]✗[/] BV [bold]{bvid}[/] 已存在，请直接 start")
            raise typer.Exit(1)

        if interval > 3600:
            if not typer.confirm(f"⚠ 间隔 {interval}s (>1小时)，确认继续？"):
                raise typer.Exit(0)

        resolved_name = name or _auto_name(raw)
        if await db.name_exists(resolved_name):
            console.print(f"[red]✗[/] 别名 [bold]{resolved_name}[/] 已被使用")
            raise typer.Exit(1)

        meta = await api.fetch_video_meta(bvid)
        pubdate_iso = (
            datetime.fromtimestamp(meta.pubdate).isoformat()
            if meta.pubdate else None
        )
        video_id = await db.create_video(
            bvid, resolved_name, meta.title, meta.uploader,
            pubdate=pubdate_iso,
            duration=meta.duration or None,
            tname=meta.tname or None,
            videos=meta.videos,
        )
        await db.save_interval(video_id, interval)

        if not inactive:
            await db.set_video_active(video_id, True)
            mgr = DaemonManager()
            if not mgr.status():
                mgr.start()
                console.print("[green]✓[/] 守护进程已启动")
            else:
                mgr.reload()
            console.print(f"[green]✓[/] 已创建并激活 [bold]{resolved_name}[/] ({bvid})")
        else:
            console.print(f"[green]✓[/] 已创建 [bold]{resolved_name}[/] ({bvid}) [dim](未激活)[/]")
    finally:
        await _cleanup(db, api)


# ── snap ───────────────────────────────────────────────────────


@app.command()
def snap(
    bvid_or_name: Optional[str] = typer.Argument(
        None, help="BV号或别名（不传则配合 --all）",
    ),
    all_tasks: bool = typer.Option(
        False, "--all", "-a", help="所有活跃任务各记录一次",
    ),
):
    """立即记录一次数据（不等待调度器）"""
    asyncio.run(_cmd_snap(bvid_or_name, all_tasks))


async def _cmd_snap(
    bvid_or_name: Optional[str], all_tasks: bool
) -> None:
    if not bvid_or_name and not all_tasks:
        console.print("[red]✗[/] 请指定 BV号/别名，或使用 --all")
        raise typer.Exit(1)

    db, api = await _init()
    try:
        targets: list[tuple[int, str]] = []
        if all_tasks:
            tasks = await db.get_all_tasks()
            targets = [(t.video_id, t.bvid) for t in tasks if t.active]
            if not targets:
                console.print("[yellow]⚠[/] 没有活跃任务")
                raise typer.Exit(0)
        else:
            row = await db.find_video(bvid_or_name)
            if not row:
                console.print(f"[red]✗[/] 未找到 [bold]{bvid_or_name}[/]")
                raise typer.Exit(1)
            targets = [(row["id"], row["bvid"])]

        for video_id, bvid in targets:
            data = await api.fetch_record_data(bvid)
            now = datetime.now()
            await db.insert_record(video_id, now, data)
            console.print(
                f"[green]✓[/] [bold]{bvid}[/]  "
                f"{_snap_fmt(data.views)}播放 / {_snap_fmt(data.likes)}赞 / "
                f"{_snap_fmt(data.coins)}币 / {_snap_fmt(data.favorites)}收藏 / "
                f"{_snap_fmt(data.reply)}评论"
                + (f" / 最高排名{data.his_rank}" if data.his_rank else "")
            )
    finally:
        await _cleanup(db, api)


# ── delete ─────────────────────────────────────────────────────


@app.command()
def delete(
    bvid_or_name: str = typer.Argument(..., help="BV号或别名"),
):
    """彻底删除视频及所有记录"""
    asyncio.run(_cmd_delete(bvid_or_name))


async def _cmd_delete(bvid_or_name: str) -> None:
    db, api = await _init()
    try:
        row = await db.find_video(bvid_or_name)
        if not row:
            console.print(f"[red]✗[/] 未找到 [bold]{bvid_or_name}[/]")
            raise typer.Exit(1)
        await db.delete_video(row["id"])
        mgr = DaemonManager()
        mgr.reload()
        console.print(f"[green]✓[/] 已删除 [bold]{row['name']}[/]")
    finally:
        await _cleanup(db, api)


# ── start ──────────────────────────────────────────────────────


@app.command()
def start(
    bvid_or_name: Optional[str] = typer.Argument(
        None, help="BV号或别名（不传则启动守护进程）"
    ),
    all: bool = typer.Option(False, "--all", "-a", help="激活所有任务"),
):
    """启动守护进程 / 激活任务"""
    asyncio.run(_cmd_start(bvid_or_name, all))


async def _cmd_start(
    bvid_or_name: Optional[str], all: bool
) -> None:
    db, api = await _init()
    try:
        if all:
            tasks = await db.get_all_tasks()
            for t in tasks:
                if not t.active:
                    await db.set_video_active(t.video_id, True)

        if bvid_or_name:
            row = await db.find_video(bvid_or_name)
            if not row:
                console.print(f"[red]✗[/] 未找到 [bold]{bvid_or_name}[/]，请先 create")
                raise typer.Exit(1)
            await db.set_video_active(row["id"], True)

        mgr = DaemonManager()
        if all or bvid_or_name:
            if not mgr.status():
                mgr.start()
            else:
                mgr.reload()
            if all:
                console.print("[green]✓[/] 已激活所有任务")
            else:
                console.print(f"[green]✓[/] 已激活 [bold]{row['name']}[/]")
        else:
            if mgr.status():
                console.print("[yellow]守护进程已在运行中[/]")
            else:
                mgr.start()
                console.print("[green]✓[/] 守护进程已启动")
    finally:
        await _cleanup(db, api)


# ── stop ───────────────────────────────────────────────────────


@app.command()
def stop(
    bvid_or_name: Optional[str] = typer.Argument(
        None, help="BV号或别名（不传则报错）"
    ),
    all: bool = typer.Option(False, "--all", "-a", help="关闭守护进程（不改变任务活跃状态）"),
):
    """停用任务 / 关闭守护进程"""
    asyncio.run(_cmd_stop(bvid_or_name, all))


async def _cmd_stop(
    bvid_or_name: Optional[str], all: bool
) -> None:
    mgr = DaemonManager()

    if all:
        mgr.stop()
        console.print("[green]✓[/] 守护进程已停止 [dim](任务活跃状态保持不变)[/]")
        return

    db, api = await _init()
    try:
        if not bvid_or_name:
            console.print("[red]✗[/] 请指定 BV号/别名 (--all 关闭守护进程)")
            raise typer.Exit(1)

        row = await db.find_video(bvid_or_name)
        if not row:
            console.print(f"[red]✗[/] 未找到 [bold]{bvid_or_name}[/]")
            raise typer.Exit(1)
        await db.set_video_active(row["id"], False)
        mgr.reload()
        active_left = await db.count_active()
        if active_left == 0:
            mgr.stop()
            console.print(
                f"[green]✓[/] 已停用 [bold]{row['name']}[/]，"
                "[dim]无活跃任务，守护进程已停止[/]"
            )
        else:
            console.print(f"[green]✓[/] 已停用 [bold]{row['name']}[/]")
    finally:
        await _cleanup(db, api)


# ── update ─────────────────────────────────────────────────────


@app.command()
def update(
    bvid_or_name: str = typer.Argument(..., help="BV号或别名"),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="新别名"),
    interval: Optional[int] = typer.Option(
        None, "--interval", "-i",
        min=30, help="新记录间隔（秒），大于3600时会二次确认",
    ),
    refresh_meta: bool = typer.Option(
        False, "--refresh-meta", help="重新抓取视频标题、UP主、时长、分区、发布时间",
    ),
):
    """修改任务别名、记录间隔或刷新元数据"""
    asyncio.run(_cmd_update(bvid_or_name, name, interval, refresh_meta))


async def _cmd_update(
    bvid_or_name: str, name: Optional[str], interval: Optional[int],
    refresh_meta: bool,
) -> None:
    db, api = await _init()
    try:
        row = await db.find_video(bvid_or_name)
        if not row:
            console.print(f"[red]✗[/] 未找到 [bold]{bvid_or_name}[/]")
            raise typer.Exit(1)
        if interval is not None and interval > 3600:
            if not typer.confirm(f"⚠ 间隔 {interval}s (>1小时)，确认继续？"):
                raise typer.Exit(0)

        if name is not None:
            if await db.name_exists(name, exclude_bvid=row["bvid"]):
                console.print(f"[red]✗[/] 别名 [bold]{name}[/] 已被使用")
                raise typer.Exit(1)
            await db.update_name(row["id"], name)
        if interval is not None:
            await db.save_interval(row["id"], interval)
        if refresh_meta:
            meta = await api.fetch_video_meta(row["bvid"])
            pubdate_iso = (
                datetime.fromtimestamp(meta.pubdate).isoformat()
                if meta.pubdate else None
            )
            await db.update_video_meta(
                row["id"],
                title=meta.title,
                uploader=meta.uploader,
                duration=meta.duration or None,
                tname=meta.tname or None,
                pubdate=pubdate_iso,
                videos=meta.videos,
            )
        mgr = DaemonManager()
        mgr.reload()
        parts = [f"[bold]{row['name']}[/]"]
        if name is not None:
            parts.append(f"别名→{name}")
        if interval is not None:
            parts.append(f"间隔→{interval}s")
        if refresh_meta:
            parts.append("元数据已刷新")
        console.print(f"[green]✓[/] 已更新 " + "，".join(parts))
    finally:
        await _cleanup(db, api)


# ── show ───────────────────────────────────────────────────────


@app.command()
def show(
    bvid_or_name: str = typer.Argument(..., help="BV号或别名"),
    last: int = typer.Option(
        10, "--last", "-l",
        min=1, max=200, help="显示最近 N 条记录",
    ),
):
    """查看视频记录数据"""
    asyncio.run(_cmd_show(bvid_or_name, last))


async def _cmd_show(bvid_or_name: str, last: int) -> None:
    db, api = await _init()
    try:
        row = await db.find_video(bvid_or_name)
        if not row:
            console.print(f"[red]✗[/] 未找到 [bold]{bvid_or_name}[/]")
            raise typer.Exit(1)
        records = await db.get_records(row["id"], limit=last)
        if not records:
            console.print(f"[yellow][/] [bold]{row['name']}[/] 暂无记录")
            return
        table = Table(title=f"{row['name']} — 最近 {len(records)} 条记录")
        table.add_column("时间", style="dim", width=19)
        table.add_column("播放", justify="right")
        table.add_column("点赞", justify="right")
        table.add_column("投币", justify="right")
        table.add_column("收藏", justify="right")
        table.add_column("弹幕", justify="right")
        table.add_column("评论", justify="right")
        table.add_column("在线", justify="right")
        table.add_column("最高排名", justify="right")
        for r in records:
            table.add_row(
                r["timestamp"][:19],
                _n(r["views"]), _n(r["likes"]),
                _n(r["coins"]), _n(r["favorites"]),
                _n(r["danmaku"]), _n(r["reply"]),
                _n(r["online"]), _n(r["his_rank"]),
            )
        console.print(table)
    finally:
        await _cleanup(db, api)


# ── list ───────────────────────────────────────────────────────


@app.command()
def list():
    """列出所有监控任务"""
    asyncio.run(_cmd_list())


async def _cmd_list() -> None:
    db, api = await _init()
    try:
        tasks = await db.get_all_tasks()
        if not tasks:
            console.print("[yellow]暂无监控任务[/]")
            return
        table = Table(title=f"监控任务 ({len(tasks)})")
        table.add_column("别名", style="cyan")
        table.add_column("BV号")
        table.add_column("标题", no_wrap=False)
        table.add_column("UP主")
        table.add_column("分区", no_wrap=False)
        table.add_column("时长")
        table.add_column("分P")
        table.add_column("状态")
        table.add_column("间隔")
        table.add_column("记录数", justify="right")
        table.add_column("最后记录")
        table.add_column("发布时间")
        for t in tasks:
            status = "[green]● 活跃[/]" if t.active else "[dim]● 停止[/]"
            last = t.last_record or "[dim]—[/]"
            pub = t.pubdate or "[dim]—[/]"
            duration_str = _fmt_duration(t.duration) if t.duration else "[dim]—[/]"
            tname_str = t.tname or "[dim]—[/]"
            videos_str = str(t.videos) + "P" if t.videos > 1 else "[dim]—[/]"
            table.add_row(
                t.name, t.bvid, t.title[:40], t.uploader,
                tname_str, duration_str, videos_str,
                status, f"{t.interval}s",
                str(t.record_count), last, pub[:19] if t.pubdate else "[dim]—[/]",
            )
        console.print(table)
    finally:
        await _cleanup(db, api)


# ── export ─────────────────────────────────────────────────────


@app.command()
def export(
    bvid_or_name: str = typer.Argument(..., help="BV号或别名"),
    fmt: str = typer.Option(
        "csv", "--format", "-f", help="导出格式 (csv/json)",
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="输出路径",
    ),
):
    """导出视频记录数据"""
    asyncio.run(_cmd_export(bvid_or_name, fmt, output))


async def _cmd_export(
    bvid_or_name: str, fmt: str, output: Optional[Path]
) -> None:
    db, api = await _init()
    try:
        row = await db.find_video(bvid_or_name)
        if not row:
            console.print(f"[red]✗[/] 未找到 [bold]{bvid_or_name}[/]")
            raise typer.Exit(1)
        from bili_monitor.export.exporter import export_records
        from bili_monitor.export.exporter import META_FIELDS
        meta = {k: row[k] for k in META_FIELDS if k in row}
        path = await export_records(
            row["bvid"], row["id"], fmt, db, output, meta=meta
        )
        console.print(f"[green]✓[/] 已导出 → [bold]{path}[/]")
    finally:
        await _cleanup(db, api)


# ── viz ────────────────────────────────────────────────────────


@app.command()
def viz(
    bvid_or_name: str = typer.Argument(..., help="BV号或别名"),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="自定义输出目录",
    ),
    weights: Optional[Path] = typer.Option(
        None, "--weights", "-w", help="权重 JSON 文件（局部覆盖默认热度权重）",
    ),
):
    """生成数据可视化报告（一次性输出所有图表）"""
    asyncio.run(_cmd_viz(bvid_or_name, output, weights))


async def _cmd_viz(
    bvid_or_name: str,
    output: Optional[Path],
    weights: Optional[Path],
) -> None:
    db, api = await _init()
    try:
        row = await db.find_video(bvid_or_name)
        if not row:
            console.print(f"[red]✗[/] 未找到 [bold]{bvid_or_name}[/]")
            raise typer.Exit(1)

        records = await db.get_records(row["id"])
        if not records:
            console.print("[yellow]⚠[/] 没有数据，无法生成图表")
            return
        records = records[::-1]

        from bili_monitor.viz.plots import generate_report, load_weights

        w = load_weights(weights)
        try:
            paths = await generate_report(
                row["bvid"], records,
                name=row["name"],
                output=output,
                weights=w,
                duration=row["duration"],
                videos=row["videos"],
            )
        except ImportError as e:
            console.print(f"[red]✗[/] {e}")
            raise typer.Exit(1)

        if not paths:
            console.print("[yellow]⚠[/] 没有生成任何图表（数据不足）")
        else:
            for p in paths:
                console.print(f"[green]✓[/] → [bold]{p}[/]")
    finally:
        await _cleanup(db, api)


# ── daemon status ──────────────────────────────────────────────


@app.command()
def daemon(
    action: str = typer.Argument(
        "status", help="status",
    ),
):
    """查看守护进程状态"""
    if action != "status":
        console.print("[red]仅支持 daemon status[/]")
        raise typer.Exit(1)
    mgr = DaemonManager()
    pid = mgr.status()
    if pid:
        console.print(f"[green]●[/] 守护进程运行中 ([bold]{pid}[/])")
    else:
        console.print("[dim]○[/] 守护进程未运行")


# ── import ─────────────────────────────────────────────────────


@app.command(name="import")
def import_(
    file: Path = typer.Argument(..., help="导入文件路径 (CSV/JSON)"),
    bvid: str = typer.Option(..., "--bvid", "-b", help="目标视频 BV 号"),
    format: Optional[str] = typer.Option(
        None, "--format", "-f", help="文件格式 (csv/json，默认从扩展名推断)",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="仅预览，不写入数据库",
    ),
    overwrite: bool = typer.Option(
        False, "--overwrite", "-o", help="覆盖已存在的记录",
    ),
):
    """从文件导入记录数据到视频"""
    asyncio.run(_cmd_import(file, bvid, format, dry_run, overwrite))


async def _cmd_import(
    file: Path, bvid: str, format: Optional[str],
    dry_run: bool, overwrite: bool,
) -> None:
    if not file.exists():
        console.print(f"[red]✗[/] 文件不存在: [bold]{file}[/]")
        raise typer.Exit(1)

    db, api = await _init()
    try:
        from bili_monitor.data_import.importer import import_records
        result = await import_records(
            file, bvid, db,
            format=format, dry_run=dry_run, overwrite=overwrite,
        )
        if dry_run:
            console.print(f"[yellow]🔍 预览[/] {result.summary} [dim](未写入)")
        else:
            icon = "[green]✓" if result.errors == 0 else "[yellow]⚠"
            console.print(f"{icon}[/] 导入完成: {result.summary}")
    except ValueError as e:
        console.print(f"[red]✗[/] {e}")
        raise typer.Exit(1)
    finally:
        await _cleanup(db, api)


# ── helpers ────────────────────────────────────────────────────


def _n(val) -> str:
    if val is None:
        return "[dim]—[/]"
    n = int(val)
    if n >= 10_000:
        return f"{n / 10_000:.1f}万"
    return str(n)


def _snap_fmt(val) -> str:
    if val is None:
        return "—"
    n = int(val)
    if n >= 10_000:
        return f"{n / 10_000:.1f}万"
    return str(n)


def _fmt_duration(seconds: int) -> str:
    h, r = divmod(seconds, 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"
