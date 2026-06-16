from __future__ import annotations

import asyncio
import logging
import signal
import threading
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from bili_monitor.api.client import BiliAPIClient
from bili_monitor.config import Settings
from bili_monitor.core.scheduler import CommandQueue, MonitorState, Scheduler
from bili_monitor.db.database import Database
from bili_monitor.ui.panel import run_panel

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
)


# ── Shared init ─────────────────────────────────────────────────


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


# ── start ───────────────────────────────────────────────────────


@app.command()
def start(
    bvid: str = typer.Argument(..., help="BV号或视频URL"),
    interval: int = typer.Option(
        300, "--interval", "-i",
        min=30, max=3600,
        help="记录间隔（秒）",
    ),
):
    """开始监控一个视频（前台运行，Ctrl+C 停止）"""
    asyncio.run(_cmd_start(bvid, interval))


async def _cmd_start(bvid: str, interval: int) -> None:
    db, api = await _init()
    try:
        bvid = BiliAPIClient.resolve_bvid(bvid)
        state = MonitorState()
        sched = Scheduler(db, api, state)
        await sched.add_task(bvid, interval)
        console.print(
            f"[green]✓[/] 开始监控 [bold]{bvid}[/] (间隔 {interval}s)\n"
            "[dim]按 Ctrl+C 停止[/dim]"
        )
        loop = asyncio.get_event_loop()
        stop_event = asyncio.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop_event.set)
            except NotImplementedError:
                pass
        run_task = asyncio.create_task(sched.run())
        await stop_event.wait()
        sched.stop()
        await run_task
        console.print("\n[yellow]监控已停止[/]")
    finally:
        await _cleanup(db, api)


# ── stop ────────────────────────────────────────────────────────


@app.command()
def stop(
    bvid: str = typer.Argument(..., help="BV号或视频URL"),
):
    """停止一个监控任务"""
    asyncio.run(_cmd_stop(bvid))


async def _cmd_stop(bvid: str) -> None:
    db, api = await _init()
    try:
        bvid = BiliAPIClient.resolve_bvid(bvid)
        tasks = await db.get_all_tasks()
        match = [t for t in tasks if t.bvid == bvid]
        if not match:
            console.print(f"[red]✗[/] 未找到任务 [bold]{bvid}[/]")
            raise typer.Exit(1)
        await db.set_video_active(match[0].video_id, False)
        await db.delete_interval(match[0].video_id)
        console.print(f"[green]✓[/] 已停止 [bold]{bvid}[/]")
    finally:
        await _cleanup(db, api)


# ── update ─────────────────────────────────────────────────────


@app.command()
def update(
    bvid: str = typer.Argument(..., help="BV号"),
    interval: int = typer.Option(
        300, "--interval", "-i",
        min=30, max=3600,
        help="新的记录间隔（秒）",
    ),
):
    """修改监控任务的参数（如记录间隔）"""
    asyncio.run(_cmd_update(bvid, interval))


async def _cmd_update(bvid: str, interval: int) -> None:
    db, api = await _init()
    try:
        bvid = BiliAPIClient.resolve_bvid(bvid)
        tasks = await db.get_all_tasks()
        match = [t for t in tasks if t.bvid == bvid]
        if not match:
            console.print(f"[red]✗[/] 未找到任务 [bold]{bvid}[/]")
            raise typer.Exit(1)
        await db.save_interval(match[0].video_id, interval)
        await db.set_video_active(match[0].video_id, True)
        console.print(
            f"[green]✓[/] 已更新 [bold]{bvid}[/] 间隔 -> [bold]{interval}s[/]"
        )
    finally:
        await _cleanup(db, api)


# ── list ────────────────────────────────────────────────────────


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
        table.add_column("BV号", style="cyan")
        table.add_column("标题", style="white", no_wrap=False)
        table.add_column("UP主")
        table.add_column("状态")
        table.add_column("间隔")
        table.add_column("记录数", justify="right")
        table.add_column("最后记录")
        for t in tasks:
            status = "[green]● 活跃[/]" if t.active else "[dim]● 停止[/]"
            last = t.last_record or "[dim]—[/]"
            table.add_row(
                t.bvid, t.title[:40], t.uploader,
                status, f"{t.interval}s",
                str(t.record_count), last,
            )
        console.print(table)
    finally:
        await _cleanup(db, api)


# ── panel ───────────────────────────────────────────────────────


@app.command()
def panel():
    """打开交互式任务管理面板"""
    state = MonitorState()
    cmd_queue = CommandQueue()

    def _run_scheduler() -> None:
        async def _main() -> None:
            cfg = Settings.get_instance()
            db = Database(cfg.db_path)
            await db.connect()
            api = BiliAPIClient.get_instance()
            await api.start()
            sched = Scheduler(db, api, state, cmd_queue)
            try:
                await sched.run()
            finally:
                await api.close()
                await db.close()

        asyncio.run(_main())

    thread = threading.Thread(target=_run_scheduler, daemon=True)
    thread.start()

    try:
        run_panel(state, cmd_queue)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        state.running = False
        thread.join(timeout=5)


# ── export ──────────────────────────────────────────────────────


@app.command()
def export(
    bvid: str = typer.Argument(..., help="BV号"),
    fmt: str = typer.Option(
        "csv", "--format", "-f",
        help="导出格式 (csv/json)",
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o",
        help="输出路径（默认自动生成）",
    ),
):
    """导出视频记录数据"""
    asyncio.run(_cmd_export(bvid, fmt, output))


async def _cmd_export(
    bvid: str, fmt: str, output: Optional[Path]
) -> None:
    db, api = await _init()
    try:
        bvid = BiliAPIClient.resolve_bvid(bvid)
        tasks = await db.get_all_tasks()
        match = [t for t in tasks if t.bvid == bvid]
        if not match:
            console.print(f"[red]✗[/] 未找到 [bold]{bvid}[/] 的记录")
            raise typer.Exit(1)
        from bili_monitor.export.exporter import export_records
        path = await export_records(
            bvid, match[0].video_id, fmt, db, output
        )
        console.print(f"[green]✓[/] 已导出 → [bold]{path}[/]")
    finally:
        await _cleanup(db, api)


# ── viz ─────────────────────────────────────────────────────────


@app.command()
def viz(
    bvid: str = typer.Argument(..., help="BV号"),
    metrics: str = typer.Option(
        "views,likes,coins",
        "--metrics", "-m",
        help="指标列表（逗号分隔）",
    ),
    type: str = typer.Option(
        "trend", "--type", "-t",
        help="图表类型: trend / compare / ratio",
    ),
):
    """生成数据可视化"""
    asyncio.run(_cmd_viz(bvid, metrics, type))


async def _cmd_viz(bvid: str, metrics: str, type: str) -> None:
    db, api = await _init()
    try:
        bvid = BiliAPIClient.resolve_bvid(bvid)
        tasks = await db.get_all_tasks()
        match = [t for t in tasks if t.bvid == bvid]
        if not match:
            console.print(f"[red]✗[/] 未找到 [bold]{bvid}[/] 的记录")
            raise typer.Exit(1)
        metric_list = [m.strip() for m in metrics.split(",")]
        from bili_monitor.viz.plots import generate_plot
        path = await generate_plot(
            bvid, match[0].video_id, db, metric_list, type
        )
        console.print(f"[green]✓[/] 可视化已生成 → [bold]{path}[/]")
    finally:
        await _cleanup(db, api)


# ── daemon ──────────────────────────────────────────────────────


@app.command()
def daemon(
    action: str = typer.Argument(
        "status",
        help="start / stop / status",
    ),
):
    """管理守护进程（后台运行）"""
    asyncio.run(_cmd_daemon(action))


async def _cmd_daemon(action: str) -> None:
    from bili_monitor.daemon.daemon import DaemonManager
    mgr = DaemonManager()
    if action == "start":
        pid = mgr.start()
        if pid:
            console.print(f"[green]✓[/] 守护进程已启动 (PID: {pid})")
        else:
            console.print("[yellow]守护进程已在运行中[/]")
    elif action == "stop":
        if mgr.stop():
            console.print("[green]✓[/] 守护进程已停止")
        else:
            console.print("[yellow]守护进程未在运行[/]")
    elif action == "status":
        pid = mgr.status()
        if pid:
            console.print(f"[green]●[/] 守护进程运行中 (PID: {pid})")
        else:
            console.print("[dim]○[/] 守护进程未运行")
    else:
        console.print(f"[red]未知操作: {action} (start/stop/status)[/]")
        raise typer.Exit(1)
