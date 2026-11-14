from assets_tracker.ui.widgets import source_badge_html, yahoo_quote_url


def test_url_encodes_special_characters() -> None:
    assert yahoo_quote_url("AIR.PA") == "https://finance.yahoo.com/quote/AIR.PA"
    assert yahoo_quote_url(" ^FCHI ") == "https://finance.yahoo.com/quote/%5EFCHI"


def test_badge_links_to_yahoo_when_symbol_known() -> None:
    html = source_badge_html("Cours Yahoo · 01/01/2026", "AIR.PA", "#fff")
    assert 'href="https://finance.yahoo.com/quote/AIR.PA"' in html
    assert "Cours Yahoo" in html


def test_badge_is_plain_text_without_symbol() -> None:
    assert source_badge_html("Cours manuel <x>", None, "#fff") == "Cours manuel &lt;x&gt;"
    assert "href" not in source_badge_html("Pas de cours", "  ", "#fff")
