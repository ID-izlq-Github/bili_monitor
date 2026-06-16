from __future__ import annotations

import atexit
import sys
import termios
import time
import tty
from typing import Optional

from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from bili_monitor.api.client import BiliAPIClient as BAC
from bili_monitor.core.scheduler import CommandQueue, MonitorState

_HELP = Text.from_markup(
    "[bold q][/] 退出    [bold a][/] 添加任务    [bold d <id>][/] 删除任务"
)

_PROMPTS = {
    "add_bvid": "BV号或URL: ",
    "add_interval": "间隔(秒, 默认300): ",
    "delete": "要删除的任务ID: ",
}

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


def _build_table(status_list: list, total_records: int) -> Table:
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


def _make_layout(table: Table, input_mode: Optional[str], input_buffer: str) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(Panel(table, subtitle=_HELP), name="main"),
        Layout(name="footer", size=3),
    )
    if input_mode:
        prompt = _PROMPTS.get(input_mode, "> ")
        footer = Panel(Text(prompt + input_buffer + "▌"), style="bold yellow")
    else:
        footer = Panel(_HELP, style="dim")
    layout["footer"].update(footer)
    return layout


def _resolve_bvid(raw: str) -> Optional[str]:
    try:
        return BAC.resolve_bvid(raw)
    except ValueError:
        return None


def _find_bvid_by_id(status_list: list, idx: int) -> Optional[str]:
    if 1 <= idx <= len(status_list):
        return status_list[idx - 1].bvid
    return None


def _parse_interval(raw: str) -> int:
    if raw.isdigit():
        return max(30, min(3600, int(raw)))
    return 300


def run_panel(state: MonitorState, cmd_queue: CommandQueue) -> None:
    _setup_terminal()

    input_mode: Optional[str] = None
    input_buffer = ""
    pending_bvid: Optional[str] = None
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

                if input_mode is None:
                    if key == "q" or key == "\x03":
                        break
                    elif key == "a":
                        input_mode = "add_bvid"
                        input_buffer = ""
                    elif key == "d":
                        input_mode = "delete"
                        input_buffer = ""
                else:
                    if key is None:
                        pass
                    elif key == "\x03":
                        input_mode = None
                        input_buffer = ""
                    elif key in ("\r", "\n"):
                        if input_mode == "add_bvid":
                            bvid = _resolve_bvid(input_buffer)
                            if bvid:
                                pending_bvid = bvid
                                input_mode = "add_interval"
                                input_buffer = ""
                            else:
                                input_mode = None
                                input_buffer = ""
                        elif input_mode == "add_interval":
                            interval = _parse_interval(input_buffer)
                            if pending_bvid:
                                cmd_queue.put("add", (pending_bvid, interval))
                                pending_bvid = None
                            input_mode = None
                            input_buffer = ""
                        elif input_mode == "delete":
                            if input_buffer.isdigit():
                                tasks = state.snapshot()
                                bvid = _find_bvid_by_id(tasks, int(input_buffer))
                                if bvid:
                                    cmd_queue.put("remove", (bvid,))
                            input_mode = None
                            input_buffer = ""
                    elif key == "\x7f":
                        input_buffer = input_buffer[:-1]
                    elif key.isprintable():
                        input_buffer += key

                if now - last_refresh > 0.8 or key is not None:
                    last_refresh = now
                    tasks = state.snapshot()
                    total = sum(t.record_count for t in tasks)
                    layout = _make_layout(
                        _build_table(tasks, total),
                        input_mode,
                        input_buffer,
                    )
                    live.update(layout)
                    live.refresh()

    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        _restore_terminal()
