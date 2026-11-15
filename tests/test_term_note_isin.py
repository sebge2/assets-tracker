from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from assets_tracker.db.connection import connect
from assets_tracker.db.term_note_repository import TermNoteRepository
from assets_tracker.domain.term_notes import InterestPayout, TermNoteDetails, normalize_isin

D = Decimal
NOTE = TermNoteDetails(
    "Crédit Mutuel",
    D(10000),
    D("3.2"),
    date(2025, 3, 15),
    date(2028, 3, 15),
    InterestPayout.MATURITY,
)


@pytest.fixture
def repository(tmp_path: Path) -> Iterator[TermNoteRepository]:
    connection = connect(tmp_path / "portfolio.db", key_provider=lambda: b"\x0b" * 32)
    yield TermNoteRepository(connection)
    connection.close()


def test_normalize_isin_cleans_and_accepts_empty() -> None:
    assert normalize_isin(" be00 0000 0000 ") == "BE0000000000"
    assert normalize_isin("") == ""
    assert normalize_isin("   ") == ""


@pytest.mark.parametrize("bad", ["BE123", "1E0000000000", "BE000000000A", "BE00000000000"])
def test_normalize_isin_rejects_bad_format(bad: str) -> None:
    with pytest.raises(ValueError, match="ISIN"):
        normalize_isin(bad)


def test_isin_is_optional_and_defaults_to_empty(repository: TermNoteRepository) -> None:
    holding = repository.create("Bon sans ISIN", NOTE)
    assert holding.details.isin == ""


def test_isin_is_stored_normalized_and_can_be_changed(repository: TermNoteRepository) -> None:
    created = repository.create("Bon", replace(NOTE, isin="be0000000000"))
    assert created.details.isin == "BE0000000000"
    asset_id = created.asset.id
    assert asset_id is not None
    updated = repository.update(asset_id, "Bon", replace(NOTE, isin="FR0000000001"))
    assert updated.details.isin == "FR0000000001"
    cleared = repository.update(asset_id, "Bon", NOTE)
    assert cleared.details.isin == ""


def test_invalid_isin_is_refused_by_the_repository(repository: TermNoteRepository) -> None:
    with pytest.raises(ValueError, match="ISIN"):
        repository.create("Bon", replace(NOTE, isin="XX"))
    assert repository.list_holdings() == []
