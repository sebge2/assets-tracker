from __future__ import annotations

from collections.abc import Callable
from importlib import resources
from pathlib import Path
from typing import Any

from assets_tracker.db.keychain import database_key

APP_DIR_NAME = "AssetsTracker"
_SQLITE_HEADER = b"SQLite format 3\x00"


class DatabaseSecurityError(RuntimeError):
    """Raised when the encrypted portfolio cannot be opened safely."""


def default_db_path() -> Path:
    from PySide6.QtCore import QStandardPaths

    base = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
    base.mkdir(parents=True, exist_ok=True)
    return base / "portfolio.db"


def connect(path: Path | str, key_provider: Callable[[], bytes] = database_key) -> Any:
    """Open an SQLCipher database after authorizing access to its key.

    The key is represented as hexadecimal before being passed to SQLCipher, so it
    never needs to be interpolated from user-controlled input.
    """
    db_path = Path(path)
    header = b""
    if db_path.exists():
        with db_path.open("rb") as database_file:
            header = database_file.read(len(_SQLITE_HEADER))
    if header == _SQLITE_HEADER:
        raise DatabaseSecurityError(
            "Cette base SQLite n'est pas chiffrée. Sa migration doit être réalisée "
            "explicitement avant ouverture."
        )

    try:
        from sqlcipher3 import dbapi2 as sqlcipher
    except ImportError as error:
        raise DatabaseSecurityError(
            "SQLCipher est indisponible ; la base chiffrée ne peut pas être ouverte."
        ) from error

    key = key_provider()
    if len(key) != 32:
        raise DatabaseSecurityError("La clé de chiffrement doit contenir 256 bits.")

    conn = sqlcipher.connect(str(db_path))
    conn.row_factory = sqlcipher.Row
    try:
        conn.execute(f"PRAGMA key = \"x'{key.hex()}'\"")
        conn.execute("PRAGMA cipher_memory_security = ON")
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        conn.executescript(
            resources.files("assets_tracker.db").joinpath("schema.sql").read_text("utf-8")
        )
        _migrate(conn)
    except Exception:
        conn.close()
        raise
    return conn


def _migrate(conn: Any) -> None:
    """Applique les migrations SQLite sans perdre l'historique local."""
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if version >= 1:
        return
    definition = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'cash_movements'"
    ).fetchone()[0]
    if "LOYALTY_BONUS" not in definition:
        with conn:
            conn.execute(
                "CREATE TABLE cash_movements_new ("
                "id INTEGER PRIMARY KEY, "
                "account_id INTEGER NOT NULL REFERENCES cash_accounts(id) ON DELETE CASCADE, "
                "kind TEXT NOT NULL CHECK (kind IN "
                "('OPENING', 'DEPOSIT', 'WITHDRAWAL', 'INTEREST', 'LOYALTY_BONUS')), "
                "movement_date TEXT NOT NULL, amount TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO cash_movements_new "
                "SELECT id, account_id, kind, movement_date, amount FROM cash_movements"
            )
            conn.execute("DROP TABLE cash_movements")
            conn.execute("ALTER TABLE cash_movements_new RENAME TO cash_movements")
            conn.execute(
                "CREATE INDEX idx_cash_movements_account "
                "ON cash_movements(account_id, movement_date)"
            )
    conn.execute("PRAGMA user_version = 1")
