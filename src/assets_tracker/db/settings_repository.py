"""Configuration de l'application : paires clé/valeur stockées dans la table `settings`."""

from __future__ import annotations

from typing import Any

FMP_API_KEY = "fmp_api_key"  # clé d'API Financial Modeling Prep


class SettingsRepository:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def get(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def set(self, key: str, value: str) -> None:
        """Enregistre `value` (remplace l'existante). Une valeur vide supprime la clé."""
        cleaned = value.strip()
        if not key.strip():
            raise ValueError("La clé de configuration est vide.")
        if not cleaned:
            self.delete(key)
            return
        with self._conn:
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, cleaned),
            )

    def delete(self, key: str) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM settings WHERE key = ?", (key,))

    def all(self) -> dict[str, str]:
        rows = self._conn.execute("SELECT key, value FROM settings ORDER BY key").fetchall()
        return {str(r["key"]): str(r["value"]) for r in rows}
