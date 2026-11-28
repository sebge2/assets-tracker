"""Export et restauration SQLCipher protégés par une phrase de récupération."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class BackupError(RuntimeError):
    """La sauvegarde ne peut pas être créée ou restaurée en sécurité."""


def validate_recovery_phrase(phrase: str) -> None:
    if len(phrase) < 12:
        raise BackupError("La phrase de récupération doit contenir au moins 12 caractères.")


def export_backup(connection: Any, destination: Path, phrase: str) -> None:
    """Exporte une copie SQLCipher chiffrée avec `phrase`.

    La clé du Trousseau n'est jamais copiée dans l'archive.
    """
    validate_recovery_phrase(phrase)
    if destination.exists():
        raise BackupError("Le fichier de sauvegarde existe déjà.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    connection.commit()
    try:
        connection.execute(
            f"ATTACH DATABASE {_literal_path(destination)} AS recovery KEY {_literal(phrase)}"
        )
        connection.execute("SELECT sqlcipher_export('recovery')")
        connection.execute("DETACH DATABASE recovery")
    except Exception as error:
        _safe_unlink(destination)
        raise BackupError("Impossible de créer la sauvegarde chiffrée.") from error


def restore_backup(source: Path, phrase: str, destination: Path, local_key: bytes) -> Path:
    """Déchiffre `source` puis prépare une DB chiffrée pour le Trousseau local.

    Le fichier temporaire retourné doit être atomiquement remplacé dans l'app,
    une fois les connexions vers la base actuelle fermées.
    """
    validate_recovery_phrase(phrase)
    if not source.is_file():
        raise BackupError("Le fichier de sauvegarde est introuvable.")
    if len(local_key) != 32:
        raise BackupError("La clé locale de chiffrement est invalide.")
    temporary = destination.with_suffix(destination.suffix + ".restore")
    _safe_unlink(temporary)
    try:
        from sqlcipher3 import dbapi2 as sqlcipher

        restored = sqlcipher.connect(str(source))
        try:
            restored.execute(f"PRAGMA key = {_literal(phrase)}")
            restored.execute("SELECT count(*) FROM sqlite_master").fetchone()
            restored.execute(
                f"ATTACH DATABASE {_literal_path(temporary)} AS local KEY \"x'{local_key.hex()}'\""
            )
            restored.execute("SELECT sqlcipher_export('local')")
            restored.execute("DETACH DATABASE local")
        finally:
            restored.close()
    except Exception as error:
        _safe_unlink(temporary)
        raise BackupError("Phrase de récupération invalide ou sauvegarde corrompue.") from error
    return temporary


def replace_database(restored: Path, destination: Path) -> None:
    """Remplace atomiquement la base locale après validation de la restauration."""
    os.replace(restored, destination)


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _literal_path(path: Path) -> str:
    return _literal(str(path))


def _safe_unlink(path: Path) -> None:
    if path.exists():
        path.unlink()
