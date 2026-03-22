from datetime import datetime, timedelta, timezone

import pytest
from icalendar import Calendar

from src.calendar import generate_ics
from tests.conftest import BRISBANE, REFERENCE_DATE, make_event

DTSTAMP = datetime(2026, 3, 23, 3, 0, tzinfo=timezone.utc)


def _parse_ics(data: bytes) -> Calendar:
    return Calendar.from_ical(data)


def _get_events(cal: Calendar) -> list:
    return [c for c in cal.walk() if c.name == "VEVENT"]


class TestGenerateIcs:
    def test_summary_full(self):
        events = [make_event(speaker="Dr Test", affiliation="MIT")]
        data = generate_ics(events, "Test", DTSTAMP)
        vevent = _get_events(_parse_ics(data))[0]
        summary = str(vevent.get("SUMMARY"))
        assert "Test Talk Title" in summary
        assert "Dr Test" in summary
        assert "(MIT)" in summary

    def test_summary_no_affiliation(self):
        events = [make_event(speaker="Dr Test", affiliation=None)]
        data = generate_ics(events, "Test", DTSTAMP)
        vevent = _get_events(_parse_ics(data))[0]
        summary = str(vevent.get("SUMMARY"))
        assert "Dr Test" in summary
        assert "(" not in summary

    def test_summary_no_speaker(self):
        events = [make_event(speaker=None, affiliation=None)]
        data = generate_ics(events, "Test", DTSTAMP)
        vevent = _get_events(_parse_ics(data))[0]
        summary = str(vevent.get("SUMMARY"))
        assert summary == "Test Talk Title"

    def test_cancelled_prefix(self):
        events = [make_event(cancelled=True)]
        data = generate_ics(events, "Test", DTSTAMP)
        vevent = _get_events(_parse_ics(data))[0]
        assert str(vevent.get("SUMMARY")).startswith("[CANCELLED]")
        assert str(vevent.get("STATUS")) == "CANCELLED"

    def test_time_tbc_suffix(self):
        events = [make_event(time_unconfirmed=True)]
        data = generate_ics(events, "Test", DTSTAMP)
        vevent = _get_events(_parse_ics(data))[0]
        assert "[TIME TBC]" in str(vevent.get("SUMMARY"))

    def test_time_tbc_suppressed_when_cancelled(self):
        events = [make_event(time_unconfirmed=True, cancelled=True)]
        data = generate_ics(events, "Test", DTSTAMP)
        vevent = _get_events(_parse_ics(data))[0]
        assert "[TIME TBC]" not in str(vevent.get("SUMMARY"))

    def test_location_present(self):
        events = [make_event(venue="Physics Annexe (06), Room: 407")]
        data = generate_ics(events, "Test", DTSTAMP)
        vevent = _get_events(_parse_ics(data))[0]
        assert "Physics Annexe" in str(vevent.get("LOCATION"))

    def test_location_omitted_when_none(self):
        events = [make_event(venue=None)]
        data = generate_ics(events, "Test", DTSTAMP)
        vevent = _get_events(_parse_ics(data))[0]
        assert vevent.get("LOCATION") is None

    def test_description_with_abstract(self):
        events = [make_event(abstract="Test abstract text.")]
        data = generate_ics(events, "Test", DTSTAMP)
        vevent = _get_events(_parse_ics(data))[0]
        desc = str(vevent.get("DESCRIPTION"))
        assert "Test abstract text." in desc
        assert "smp.uq.edu.au" in desc

    def test_description_without_abstract(self):
        events = [make_event(abstract=None)]
        data = generate_ics(events, "Test", DTSTAMP)
        vevent = _get_events(_parse_ics(data))[0]
        desc = str(vevent.get("DESCRIPTION"))
        assert desc == "https://smp.uq.edu.au/event/session/99999"

    def test_uid_format(self):
        d = REFERENCE_DATE + timedelta(days=7)
        events = [make_event(session_id="12345", date=d)]
        data = generate_ics(events, "Test", DTSTAMP)
        vevent = _get_events(_parse_ics(data))[0]
        uid = str(vevent.get("UID"))
        assert uid == "12345-20260330@uq-seminar-calendar"

    def test_same_day_distinct_uids(self):
        d = REFERENCE_DATE
        events = [
            make_event(
                session_id="10002",
                date=d,
                start=datetime(d.year, d.month, d.day, 11, 0, tzinfo=BRISBANE),
                end=datetime(d.year, d.month, d.day, 12, 0, tzinfo=BRISBANE),
            ),
            make_event(
                session_id="10003",
                date=d,
                start=datetime(d.year, d.month, d.day, 14, 0, tzinfo=BRISBANE),
                end=datetime(d.year, d.month, d.day, 15, 0, tzinfo=BRISBANE),
            ),
        ]
        data = generate_ics(events, "Test", DTSTAMP)
        vevents = _get_events(_parse_ics(data))
        uids = {str(v.get("UID")) for v in vevents}
        assert len(uids) == 2

    def test_utc_times_no_vtimezone(self):
        events = [make_event()]
        data = generate_ics(events, "Test", DTSTAMP)
        cal = _parse_ics(data)
        vtimezones = [c for c in cal.walk() if c.name == "VTIMEZONE"]
        assert len(vtimezones) == 0
        assert str(cal.get("X-WR-TIMEZONE")) == "UTC"

        vevent = _get_events(cal)[0]
        assert vevent.get("DTSTART").dt.tzname() == "UTC"
        assert vevent.get("DTEND").dt.tzname() == "UTC"

    def test_dtstamp_not_folded(self):
        events = [make_event()]
        data = generate_ics(events, "Test", DTSTAMP)
        lines = data.decode("utf-8").replace("\r\n", "\n").split("\n")
        dtstamp_lines = [x for x in lines if x.startswith("DTSTAMP")]
        assert len(dtstamp_lines) >= 1
        for line in dtstamp_lines:
            assert len(line) < 75  # not folded

    def test_past_events_included(self):
        past_date = REFERENCE_DATE - timedelta(days=30)
        events = [make_event(date=past_date)]
        data = generate_ics(events, "Test", DTSTAMP)
        vevents = _get_events(_parse_ics(data))
        assert len(vevents) == 1

    def test_non_ascii_characters(self):
        events = [
            make_event(
                speaker="Dr Ren\u00e9 M\u00fcller",
                title="Schr\u00f6dinger\u2019s Cat",
                abstract="Em-dash \u2014 and smart quotes \u201c\u201d",
            )
        ]
        data = generate_ics(events, "Test", DTSTAMP)
        vevent = _get_events(_parse_ics(data))[0]
        assert "Ren\u00e9" in str(vevent.get("SUMMARY"))

    def test_newlines_in_abstract(self):
        events = [make_event(abstract="Line one.\n\nLine two.")]
        data = generate_ics(events, "Test", DTSTAMP)
        vevent = _get_events(_parse_ics(data))[0]
        desc = str(vevent.get("DESCRIPTION"))
        assert "Line one." in desc
        assert "Line two." in desc

    def test_raises_on_none_start(self):
        events = [make_event(start=None, end=None)]
        with pytest.raises(ValueError, match="pipeline bug"):
            generate_ics(events, "Test", DTSTAMP)
