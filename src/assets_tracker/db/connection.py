from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path

APP_DIR_NAME = "AssetsTracker"


def default_db_path() -> Path:
    from PySide6.QtCore import QStandardPaths

    base = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
    base.mkdir(parents=True, exist_ok=True)
    return base / "portfolio.db"


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        resources.files("assets_tracker.db").joinpath("schema.sql").read_text("utf-8")
    )
    return conn
