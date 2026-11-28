from __future__ import annotations

from pathlib import Path

import pytest

from assets_tracker.db.backup import BackupError, export_backup, replace_database, restore_backup
from assets_tracker.db.connection import connect


def test_backup_round_trip_uses_recovery_phrase(tmp_path: Path) -> None:
    source = tmp_path / "portfolio.db"
    backup = tmp_path / "portfolio.assetbackup"
    restored = tmp_path / "restored.db"
    key = b"\x07" * 32
    connection = connect(source, key_provider=lambda: key)
    connection.execute("INSERT INTO assets (symbol, name, currency) VALUES ('ABC', 'Test', 'EUR')")
    connection.commit()

    export_backup(connection, backup, "phrase de récupération sûre")
    connection.close()
    temporary = restore_backup(backup, "phrase de récupération sûre", restored, key)
    replace_database(temporary, restored)
    recovered = connect(restored, key_provider=lambda: key)
    assert recovered.execute("SELECT name FROM assets WHERE symbol = 'ABC'").fetchone()[0] == "Test"
    recovered.close()


def test_backup_rejects_a_wrong_phrase(tmp_path: Path) -> None:
    source = tmp_path / "portfolio.db"
    backup = tmp_path / "portfolio.assetbackup"
    key = b"\x07" * 32
    connection = connect(source, key_provider=lambda: key)
    export_backup(connection, backup, "phrase de récupération sûre")
    connection.close()

    with pytest.raises(BackupError, match="invalide"):
        restore_backup(backup, "mauvaise phrase longue", tmp_path / "restored.db", key)
