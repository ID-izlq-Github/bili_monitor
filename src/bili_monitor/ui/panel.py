from __future__ import annotations

import atexit
import sys
import termios
import threading
import time
import tty
from typing import Optional, Tuple

from rich.live import Live
from rich.panel import Panel as RichPanel
from rich.table import Table
from rich.text import Text

from bili_monitor.core.scheduler import CommandQueue, MonitorState

_HELP = Text.from_markup(
    "[bold q][/] 退出  "
    "[bold a][/] 添加任务  "
    "[bold d <id>][/] 删除任务  "
    "[bold r][/] 刷新"
)

_fd = sys.stdin.fileno()
_old_term: Optional[list] = None


def _setup_terminal() -> None:
    global _old_term
    _old_term = termios.tcgetattr(_fd)
    tty.setraw(_fd)


def _restore_terminal() -> None:
    if _old_term is not None:
        termios.tcsetattr(_fd, termios.TCSADRAIN, _old_term)


atexit.register(_restore_terminal)


def _get_key(timeout: float = 0.15) -> Optional[str]:
    import select
    if select.select([sys.stdin], [], [], timeout)[0]:
        try:
            return sys.stdin.read(1)
        except (ValueError, OSError):
            return None
    return None


def _read_line(prompt: str, timeout: float = 0.15) -> Optional[str]:
    chars: list[str] = []
    while True:
        ch = _get_key(timeout)
        if ch is None:
            continue
        if ch in ("\r", "\n"):
            break
        if ch == "\x03":
            return None
        if ch == "\x7f":
            if chars:
                chars.pop()
            sys.stdout.write("\b \b")
            sys.stdout.flush()
            continue
        chars.append(ch)
        sys.stdout.write(ch)
        sys.stdout.flush()
    return "".join(chars).strip()


def _build_table(
    status_list: list,
    total_records: int,
) -> Table:
    table = Table(
        header_style="bold cyan",
        border_style="blue",
        title=f"[bold]BiliMonitor[/]  任务 {len(status_list)}/5  记录 {total_records}",
        title_style="bold white",
    )
    table.add_column("ID", style="dim", width=3)
    table.add_column("BV号", style="cyan", width=13)
    table.add_column("标题", width=32, no_wrap=False, overflow="ellipsis")
    table.add_column("UP主", width=14, overflow="ellipsis")
    table.add_column("状态", width=8)
    table.add_column("间隔", justify="right", width=6)
    table.add_column("记录数", justify="right", width=6)
    table.add_column("最后记录", width=16)

    for idx, t in enumerate(status_list, 1):
        status = "[green]● 活跃[/]" if t.active else "[dim]● 停止[/]"
        last = t.last_run if t.last_run else "[dim]—[/]"
        table.add_row(
            str(idx),
            t.bvid,
            t.title,
            t.uploader,
            status,
            f"{t.interval}s",
            str(t.record_count),
            last,
        )
    return table


def _find_bvid_by_id(
    status_list: list, idx: int
) -> Optional[str]:
    if 1 <= idx <= len(status_list):
        return status_list[idx - 1].bvid
    return None


def run_panel(
    state: MonitorState,
    cmd_queue: CommandQueue,
) -> None:
    _setup_terminal()

    total_records = 0
    buffer = ""
    last_refresh = 0.0

    try:
        with Live(
            auto_refresh=False,
            vertical_overflow="visible",
            screen=True,
        ) as live:
            while state.running:
                now = time.time()
                key = _get_key(0.1)

                if key == "q":
                    break

                if key == "a":
                    live.update(Text("\n输入 BV号或URL: ", style="bold yellow"))
                    bvid = _read_line("", timeout=0.1)
                    if bvid:
                        live.update(
                            Text("\n输入间隔(秒，默认300): ", style="bold yellow")
                        )
                        interval_str = _read_line("", timeout=0.1)
                        interval = 300
                        if interval_str and interval_str.isdigit():
                            interval = max(
                                30,
                                min(3600, int(interval_str)),
                            )
                        from bili_monitor.api.client import BiliAPIClient as BAC
                        try:
                            BV = BAC.resolve_bvid(bvid)
                            cmd_queue.put("add", (BV, interval))
                        except ValueError:
                            pass
                    _restore_terminal()
                    _setup_terminal()

                if key == "r":
                    pass

                if key == "d":
                    live.update(
                        Text("\n输入要删除的任务ID: ", style="bold yellow")
                    )
                    id_str = _read_line("", timeout=0.1)
                    if id_str and id_str.isdigit():
                        tasks = state.snapshot()
                        bvid = _find_bvid_by_id(tasks, int(id_str))
                        if bvid:
                            cmd_queue.put("remove", (bvid,))
                    _restore_terminal()
                    _setup_terminal()

                if key == "\x03":
                    break

                if now - last_refresh > 1.0:
                    last_refresh = now
                    tasks = state.snapshot()
                    total_records = sum(t.record_count for t in tasks)
                    table = _build_table(tasks, total_records)
                    live.update(RichPanel(table, subtitle=_HELP))
                    live.refresh()
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        _restore_terminal()
