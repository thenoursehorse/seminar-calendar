import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from src.models import Event

REFERENCE_DATE = date(2026, 3, 23)
BRISBANE = ZoneInfo("Australia/Brisbane")
FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def make_event(**overrides: object) -> Event:
    """Factory with complete defaults. Override any field by keyword."""
    d = overrides.get("date", REFERENCE_DATE + timedelta(days=7))
    defaults: dict[str, object] = {
        "session_id": "99999",
        "series": "Physics colloquium",
        "title": "Test Talk Title",
        "date": d,
        "start": datetime(d.year, d.month, d.day, 11, 0, tzinfo=BRISBANE),
        "end": datetime(d.year, d.month, d.day, 12, 0, tzinfo=BRISBANE),
        "time_unconfirmed": False,
        "speaker": "Dr Test Speaker",
        "affiliation": "Test University",
        "session_url": "https://smp.uq.edu.au/event/session/99999",
        "venue": None,
        "abstract": None,
        "cancelled": False,
        "enriched": False,
        "warnings": (),
    }
    defaults.update(overrides)
    return Event(**defaults)


class StubFetcher:
    """Maps URLs to fixture file contents. Returns None for unmapped URLs."""

    def __init__(self, url_map: dict[str, str | bytes] | None = None) -> None:
        self._map: dict[str, bytes] = {}
        if url_map:
            for url, content in url_map.items():
                if isinstance(content, str):
                    # treat as fixture filename
                    path = os.path.join(FIXTURES_DIR, content)
                    with open(path, "rb") as f:
                        self._map[url] = f.read()
                else:
                    self._map[url] = content

    def __call__(self, url: str) -> bytes | None:
        return self._map.get(url)
