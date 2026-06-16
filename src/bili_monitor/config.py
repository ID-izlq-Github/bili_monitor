from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar


@dataclass
class Settings:
    _instance: ClassVar[Settings | None] = None

    db_path: Path = field(default_factory=lambda: Path.cwd() / "bili_monitor.db")
    image_dir: Path = field(default_factory=lambda: Path.cwd() / "output" / "image")
    export_dir: Path = field(default_factory=lambda: Path.cwd() / "output" / "export")

    min_interval: int = 30
    default_interval: int = 900
    max_tasks: int = 5

    max_db_size_mb: int = 30
    max_record_days: int = 180
    auto_cleanup: bool = False

    tick_interval: float = 2.0

    def __post_init__(self) -> None:
        data_dir = os.environ.get("BILI_DATA_DIR")
        if data_dir:
            base = Path(data_dir)
            base.mkdir(parents=True, exist_ok=True)
            self.db_path = base / "bili_monitor.db"
            self.image_dir = base / "image"
            self.export_dir = base / "export"
        self.image_dir.mkdir(parents=True, exist_ok=True)
        self.export_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def get_instance(cls) -> Settings:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
