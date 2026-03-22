from datetime import datetime
from pathlib import Path

from icalendar import Calendar

from src.main import run_pipeline
from tests.conftest import BRISBANE, FIXTURES_DIR, REFERENCE_DATE, StubFetcher

SESSION_FIXTURES = {
    "https://smp.uq.edu.au/event/session/10001": "session_normal.html",
    "https://smp.uq.edu.au/event/session/10005": "session_no_time.html",
    "https://smp.uq.edu.au/event/session/10006": "session_end_time_resolve.html",
    "https://smp.uq.edu.au/event/session/10007": "session_with_biography.html",
    "https://smp.uq.edu.au/event/session/10012": "session_no_speaker.html",
}


def test_full_pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr("src.main.OUTPUT_DIR", str(tmp_path))

    main_html = Path(FIXTURES_DIR, "main_page.html").read_bytes()
    url_map: dict[str, bytes] = {
        "https://smp.uq.edu.au/event/99/physics-colloquium": main_html,
    }
    for url, fixture in SESSION_FIXTURES.items():
        url_map[url] = Path(FIXTURES_DIR, fixture).read_bytes()

    fetcher = StubFetcher(url_map)

    series = [
        {
            "name": "Physics colloquium",
            "url": "https://smp.uq.edu.au/event/99/physics-colloquium",
            "output": "physics-colloquium.ics",
        }
    ]

    ok = run_pipeline(
        series,
        reference_date=datetime(
            REFERENCE_DATE.year,
            REFERENCE_DATE.month,
            REFERENCE_DATE.day,
            tzinfo=BRISBANE,
        ),
        fetcher=fetcher,
    )
    assert ok

    output = tmp_path / "physics-colloquium.ics"
    assert output.exists()

    data = output.read_bytes()
    cal = Calendar.from_ical(data)
    vevents = [c for c in cal.walk() if c.name == "VEVENT"]

    # should have multiple events (upcoming + past within cutoff)
    assert len(vevents) >= 10

    # all UIDs are distinct
    uids = [str(v.get("UID")) for v in vevents]
    assert len(uids) == len(set(uids))

    # same-day events present
    same_day_uids = [u for u in uids if "10002-" in u or "10003-" in u]
    assert len(same_day_uids) == 2

    # enriched event has venue and abstract
    e10001 = next(v for v in vevents if "10001-" in str(v.get("UID")))
    assert "Physics Annexe" in str(e10001.get("LOCATION"))
    assert "Superconductivity" in str(e10001.get("DESCRIPTION"))

    # speaker resolved from session page
    e10012 = next(v for v in vevents if "10012-" in str(v.get("UID")))
    assert "Doug Johnstone" in str(e10012.get("SUMMARY"))

    # multi-speaker
    e10008 = next(v for v in vevents if "10008-" in str(v.get("UID")))
    assert "Judy-Anne Osborne" in str(e10008.get("SUMMARY"))

    # end time resolved from session page
    e10006 = next(v for v in vevents if "10006-" in str(v.get("UID")))
    assert e10006.get("DTEND") is not None

    # no-time event got default time
    e10005 = next(v for v in vevents if "10005-" in str(v.get("UID")))
    assert e10005.get("DTSTART") is not None
    assert e10005.get("DTEND") is not None

    # cancelled event
    e10004 = next(v for v in vevents if "10004-" in str(v.get("UID")))
    assert str(e10004.get("SUMMARY")).startswith("[CANCELLED]")
    assert str(e10004.get("STATUS")) == "CANCELLED"

    # all events have start and end
    for v in vevents:
        assert v.get("DTSTART") is not None
        assert v.get("DTEND") is not None
