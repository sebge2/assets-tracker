from __future__ import annotations

from pathlib import Path

import pytest

from assets_tracker.db.connection import DatabaseSecurityError, connect


def test_plain_sqlite_database_is_refused_before_key_access(tmp_path: Path) -> None:
    database_path = tmp_path / "portfolio.db"  # type: ignore[operator]
    database_path.write_bytes(b"SQLite format 3\x00" + b"unprotected")

    def unexpected_key_access() -> bytes:
        raise AssertionError("the Keychain must not be accessed")

    with pytest.raises(DatabaseSecurityError, match="n'est pas chiffrée"):
        connect(database_path, key_provider=unexpected_key_access)
