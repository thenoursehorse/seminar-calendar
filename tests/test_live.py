from datetime import date

import pytest

from src.fetcher import fetch_page
from src.parsers import parse_main_page, parse_session_page
from src.validator import validate


@pytest.mark.live
def test_live_canary():
    # 1. fetch real main page
    html = fetch_page("https://smp.uq.edu.au/event/99/physics-colloquium")
    assert html is not None, "Failed to fetch main page"

    # 2. parse both tabs
    events = parse_main_page(html, "Physics colloquium")
    assert len(events) >= 1, "No events extracted from main page"

    # 3. select a session to enrich
    candidates = [e for e in events if e.title != "TBA" and not e.cancelled]
    upcoming = sorted(
        [c for c in candidates if c.date >= date.today()],
        key=lambda e: e.date,
    )
    if upcoming:
        target = upcoming[0]
    elif candidates:
        target = candidates[0]
    else:
        target = events[0]

    # 4. fetch and parse session page
    session_html = fetch_page(target.session_url)
    assert session_html is not None, (
        f"Failed to fetch session page {target.session_url}"
    )

    # 5. enrich
    enriched = parse_session_page(session_html, target)

    # 6. validate
    result = validate(events)
    assert result.passed, f"Validator failures: {result.failures}"

    # 7. assertions
    assert enriched.enriched is True, "Session page enrichment failed"
    assert enriched.venue is not None, f"Venue is None for session {target.session_id}"
