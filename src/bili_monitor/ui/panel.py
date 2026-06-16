from __future__ import annotations

import atexit
import sys
import termios
import time
from typing import Optional

from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from bili_monitor.api.client import BiliAPIClient as BAC
from bili_monitor.core.scheduler import CommandQueue, MonitorState

_HELP = Text.from_markup(
    " [bold a][/] 添加任务    [bold d <id>][/] 删除任务    [bold q][/] 退出"
)

_fd = sys.stdin.fileno()
_old_term: Optional[list] = None


def _setup_terminal() -> None:
    global _old_term
    attrs = termios.tcgetattr(_fd)
    _old_term = list(attrs)
    attrs[3] &= ~(termios.ECHO | termios.ICANON | termios.ISIG)
    attrs[6][termios.VMIN] = 1
    attrs[6][termios.VTIME] = 0
    termios.tcsetattr(_fd, termios.TCSANOW, attrs)


def _restore_terminal() -> None:
    if _old_term is not None:
        termios.tcsetattr(_fd, termios.TCSADRAIN, _old_term)


atexit.register(_restore_terminal)


def _get_key(timeout: float = 0.05) -> Optional[str]:
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


def _make_layout(table: Table, footer: Text) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(Panel(table), name="main"),
        Layout(Panel(footer, style="dim"), name="footer", size=3),
    )
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
    if raw and raw.isdigit():
        return max(30, min(3600, int(raw)))
    return 300


def run_panel(state: MonitorState, cmd_queue: CommandQueue) -> None:
    _setup_terminal()

    input_mode: Optional[str] = None
    input_buffer = ""
    pending_bvid: Optional[str] = None
    footer_text = _HELP

    try:
        with Live(
            auto_refresh=False,
            vertical_overflow="visible",
            screen=False,
        ) as live:
            while state.running:
                key = _get_key(0.05)

                if input_mode is None:
                    if key == "q" or key == "\x03":
                        break
                    elif key == "a":
                        input_mode = "add_bvid"
                        input_buffer = ""
                        footer_text = _HELP
                    elif key == "d":
                        input_mode = "delete"
                        input_buffer = ""
                        footer_text = _HELP
                else:
                    if key is None:
                        pass
                    elif key == "\x03":
                        input_mode = None
                        input_buffer = ""
                        pending_bvid = None
                        footer_text = _HELP
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
                                footer_text = _HELP
                        elif input_mode == "add_interval":
                            interval = _parse_interval(input_buffer)
                            if pending_bvid:
                                cmd_queue.put("add", (pending_bvid, interval))
                                pending_bvid = None
                            input_mode = None
                            input_buffer = ""
                            footer_text = _HELP
                        elif input_mode == "delete":
                            if input_buffer.isdigit():
                                tasks = state.snapshot()
                                bvid = _find_bvid_by_id(tasks, int(input_buffer))
                                if bvid:
                                    cmd_queue.put("remove", (bvid,))
                            input_mode = None
                            input_buffer = ""
                            footer_text = _HELP
                    elif key == "\x7f":
                        input_buffer = input_buffer[:-1]
                    elif key.isprintable():
                        input_buffer += key

                if input_mode == "add_bvid":
                    footer_text = Text(" 输入 BV 号或视频链接: " + input_buffer + "▌", no_wrap=True)
                elif input_mode == "add_interval":
                    footer_text = Text(" 输入间隔秒数 (30-3600): " + input_buffer + "▌", no_wrap=True)
                elif input_mode == "delete":
                    footer_text = Text(" 输入要删除的任务编号: " + input_buffer + "▌", no_wrap=True)

                tasks = state.snapshot()
                total = sum(t.record_count for t in tasks)
                layout = _make_layout(_build_table(tasks, total), footer_text)
                live.update(layout)
                live.refresh()
                time.sleep(0.01)

    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        _restore_terminal()
