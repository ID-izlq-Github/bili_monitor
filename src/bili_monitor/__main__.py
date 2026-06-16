import os
import sys

if os.environ.get("BILI_DAEMON") == "1":
    from bili_monitor.daemon.daemon import _run_daemon

    _run_daemon()
    sys.exit(0)

from bili_monitor.cli import app

if __name__ == "__main__":
    app()
