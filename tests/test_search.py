from __future__ import annotations

import pytest

from assets_tracker.domain.search import matches, normalize


def test_normalize_drops_accents_and_case() -> None:
    assert normalize("Société Générale") == "societe generale"


def test_empty_query_matches_everything() -> None:
    assert matches("", "Actions Monde")
    assert matches("   ")


@pytest.mark.parametrize("query", ["monde", "MONDE", "action", "fr0010756098", "0010"])
def test_matches_name_or_isin_substring(query: str) -> None:
    assert matches(query, "Actions Monde", "FR0010756098")


def test_every_word_must_match_in_any_field() -> None:
    assert matches("euro fr0000", "Obligations Euro", "FR0000000002")
    assert not matches("euro us", "Obligations Euro", "FR0000000002")


def test_accents_and_isin_spaces_are_ignored() -> None:
    assert matches("societe", "Société Générale", "FR0000130809")
    assert matches("FR00 0013 0809", "Société Générale", "FR0000130809")


def test_no_match() -> None:
    assert not matches("xyz", "Actions Monde", "FR0010756098")
    assert not matches("xyz", "", "")
