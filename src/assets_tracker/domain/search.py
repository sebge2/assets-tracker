"""Filtre de recherche des listes d'instruments : nom ou ISIN, sans accents ni casse.

Logique pure (ni Qt, ni SQLite, ni réseau).
"""

from __future__ import annotations

import unicodedata


def normalize(text: str) -> str:
    """Minuscules sans accents : « Société Générale » → « societe generale »."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def matches(query: str, *fields: str) -> bool:
    """Vrai si chaque mot de `query` figure dans l'un des `fields` (requête vide : tout passe).

    Les espaces d'un ISIN saisi par groupes (« BE00 0000 0000 ») sont ignorés.
    """
    words = normalize(query).split()
    if not words:
        return True
    haystack = [normalize(field) for field in fields if field]
    compact = [h.replace(" ", "") for h in haystack]
    return all(
        any(word in h for h in haystack) or any(word in c for c in compact) for word in words
    )
