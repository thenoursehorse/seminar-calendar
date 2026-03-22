from datetime import date, datetime
from pathlib import Path

import pytest
from freezegun import freeze_time

from src.parsers import parse_main_page, parse_session_page
from tests.conftest import BRISBANE, FIXTURES_DIR, REFERENCE_DATE, make_event

MAIN_PAGE_HTML = Path(FIXTURES_DIR, "main_page.html").read_bytes()


@freeze_time(REFERENCE_DATE)
class TestParseMainPage:
    def _parse(self, html: bytes = MAIN_PAGE_HTML) -> list:
        return parse_main_page(
            html, "Physics colloquium", reference_date=REFERENCE_DATE
        )

    def test_extracts_upcoming_events(self):
        events = self._parse()
        upcoming = [e for e in events if e.date >= REFERENCE_DATE]
        assert len(upcoming) >= 10

    def test_extracts_past_events_within_cutoff(self):
        events = self._parse()
        past = [e for e in events if e.date < REFERENCE_DATE]
        assert len(past) >= 2

    def test_discards_beyond_cutoff(self):
        events = self._parse()
        ids = {e.session_id for e in events}
        assert "10022" not in ids

    def test_dedup_keeps_upcoming_copy(self):
        events = self._parse()
        dedup = [e for e in events if e.session_id == "10011"]
        assert len(dedup) == 1
        assert dedup[0].speaker == "Dr Upcoming Copy"

    def test_parses_title_date_time_speaker(self):
        events = self._parse()
        e = next(e for e in events if e.session_id == "10001")
        assert e.title == "Time Reversal Symmetry Breaking"
        assert e.date == date(2026, 3, 30)
        assert e.start == datetime(2026, 3, 30, 11, 0, tzinfo=BRISBANE)
        assert e.end == datetime(2026, 3, 30, 12, 0, tzinfo=BRISBANE)
        assert e.speaker == "Professor James Annett"
        assert e.affiliation == "University of Bristol"
        assert e.time_unconfirmed is False

    def test_tba_title(self):
        events = self._parse()
        e = next(e for e in events if e.session_id == "10002")
        assert e.title == "TBA"

    def test_various_times(self):
        events = self._parse()
        # 2pm-3pm
        e = next(e for e in events if e.session_id == "10003")
        assert e.start == datetime(2026, 3, 23, 14, 0, tzinfo=BRISBANE)
        assert e.end == datetime(2026, 3, 23, 15, 0, tzinfo=BRISBANE)
        # 10:30am-11:30am
        e7 = next(e for e in events if e.session_id == "10007")
        assert e7.start == datetime(2026, 3, 30, 10, 30, tzinfo=BRISBANE)
        assert e7.end == datetime(2026, 3, 30, 11, 30, tzinfo=BRISBANE)

    def test_cancelled_detection(self):
        events = self._parse()
        e = next(e for e in events if e.session_id == "10004")
        assert e.cancelled is True

    def test_no_time_save_the_date(self):
        events = self._parse()
        e = next(e for e in events if e.session_id == "10005")
        assert e.start is None
        assert e.end is None
        assert e.time_unconfirmed is True
        assert any("no time" in w for w in e.warnings)

    def test_single_time_no_range(self):
        events = self._parse()
        e = next(e for e in events if e.session_id == "10006")
        assert e.start == datetime(2026, 3, 30, 12, 0, tzinfo=BRISBANE)
        assert e.end == datetime(2026, 3, 30, 13, 0, tzinfo=BRISBANE)
        assert e.time_unconfirmed is True
        assert any("assumed end" in w for w in e.warnings)

    def test_missing_speaker(self):
        events = self._parse()
        e = next(e for e in events if e.session_id == "10012")
        assert e.speaker is None
        assert any("no speaker" in w for w in e.warnings)

    def test_missing_affiliation(self):
        events = self._parse()
        e = next(e for e in events if e.session_id == "10005")
        assert e.affiliation is None

    def test_unlabelled_speaker_parsed_by_position(self):
        events = self._parse()
        e = next(e for e in events if e.session_id == "10007")
        assert e.speaker == "Dr Jane Positional"
        assert e.affiliation == "Curtin University"
        assert any("positional" in w.lower() for w in e.warnings)

    def test_plural_labelled_speaker(self):
        events = self._parse()
        e = next(e for e in events if e.session_id == "10009")
        assert e.speaker == "Alice and Bob"
        assert e.affiliation == "MIT and Oxford"

    def test_multi_speaker_name_institution(self):
        events = self._parse()
        e = next(e for e in events if e.session_id == "10008")
        assert "Judy-Anne Osborne" in e.speaker
        assert "Amelia Dickenson-Jones" in e.speaker
        assert "CARMA" in e.affiliation
        assert any("multi-speaker" in w for w in e.warnings)

    def test_speaker_parsed_with_inline_abstract(self):
        events = self._parse()
        e = next(e for e in events if e.session_id == "10010")
        assert e.speaker == "Dr Inline"
        assert e.affiliation == "UQ"

    def test_same_day_events_distinct(self):
        events = self._parse()
        same_day = [e for e in events if e.date == REFERENCE_DATE]
        assert len(same_day) >= 2
        ids = {e.session_id for e in same_day}
        assert "10002" in ids
        assert "10003" in ids

    def test_contacts_not_extracted_as_speakers(self):
        events = self._parse()
        for e in events:
            if e.speaker:
                assert "Karen Kheruntsyan" not in e.speaker

    def test_tab_panel_ids_flexible(self):
        # Replace panel IDs but keep tab link text
        modified = MAIN_PAGE_HTML.replace(
            b"qt-event_page_sessions-foundation-tabs-1",
            b"custom-tabs-1",
        ).replace(
            b"qt-event_page_sessions-foundation-tabs-2",
            b"custom-tabs-2",
        )
        events = parse_main_page(
            modified, "Physics colloquium", reference_date=REFERENCE_DATE
        )
        assert len(events) > 0

    def test_no_title_link_skipped(self, caplog):
        html = b"""<html><body>
        <ul class="tabs tabs__list">
          <li class="tab-title"><a class="tabs__link" href="#tab1">Upcoming sessions</a></li>
          <li class="tab-title"><a class="tabs__link" href="#tab2">Past sessions</a></li>
        </ul>
        <div class="content tab__content active" id="tab1">
          <div class="view-content">
            <div class="vertical-list__item">
              <div class="event-session--teaser">
                <div class="event-session__content">
                  <h3 class="event-session__title">No link here</h3>
                </div>
              </div>
            </div>
          </div>
        </div>
        <div class="content tab__content" id="tab2"><div class="view-content"></div></div>
        </body></html>"""
        events = parse_main_page(
            html, "Physics colloquium", reference_date=REFERENCE_DATE
        )
        assert len(events) == 0
        assert "no title link" in caplog.text.lower()

    def test_pagination_detected(self, caplog):
        html = MAIN_PAGE_HTML.replace(
            b"</div>\n  </div>\n</div>\n\n<!-- Tab 2",
            b'<li class="pager-next"><a href="?page=1">Next</a></li></div>\n  </div>\n</div>\n\n<!-- Tab 2',
        )
        parse_main_page(html, "Physics colloquium", reference_date=REFERENCE_DATE)
        assert "pagination" in caplog.text.lower()


class TestParseSessionPage:
    def _load(self, name: str) -> bytes:
        return Path(FIXTURES_DIR, name).read_bytes()

    def test_normal_enrichment(self):
        event = make_event(
            session_id="10001",
            date=date(2026, 3, 30),
            start=datetime(2026, 3, 30, 11, 0, tzinfo=BRISBANE),
            end=datetime(2026, 3, 30, 12, 0, tzinfo=BRISBANE),
        )
        result = parse_session_page(self._load("session_normal.html"), event)
        assert result.enriched is True
        assert result.venue == "Physics Annexe (06), Room: 407"
        assert "Superconductivity" in result.abstract
        assert "Sr2RuO4" in result.abstract

    def test_abstract_excludes_about_section(self):
        event = make_event(session_id="10001", date=date(2026, 3, 30))
        result = parse_session_page(self._load("session_normal.html"), event)
        assert "Physics Colloquium series hosts" not in result.abstract

    def test_empty_heading_skipped(self):
        event = make_event(session_id="10001", date=date(2026, 3, 30))
        result = parse_session_page(self._load("session_normal.html"), event)
        # both paragraphs should be in abstract (empty h3 skipped)
        assert "controversial history" in result.abstract

    def test_biography_excluded(self):
        event = make_event(
            session_id="10007",
            date=date(2026, 3, 30),
            start=datetime(2026, 3, 30, 10, 30, tzinfo=BRISBANE),
            end=datetime(2026, 3, 30, 11, 30, tzinfo=BRISBANE),
        )
        result = parse_session_page(self._load("session_with_biography.html"), event)
        assert result.enriched is True
        assert "paragraph one" in result.abstract
        assert "paragraph two" in result.abstract
        assert "long career" not in result.abstract

    def test_preamble_excluded(self):
        event = make_event(
            session_id="10007",
            date=date(2026, 3, 30),
            start=datetime(2026, 3, 30, 10, 30, tzinfo=BRISBANE),
            end=datetime(2026, 3, 30, 11, 30, tzinfo=BRISBANE),
        )
        result = parse_session_page(self._load("session_with_biography.html"), event)
        assert "joint Maths" not in result.abstract

    def test_venue_room_split(self):
        event = make_event(
            session_id="10007",
            date=date(2026, 3, 30),
            start=datetime(2026, 3, 30, 10, 30, tzinfo=BRISBANE),
            end=datetime(2026, 3, 30, 11, 30, tzinfo=BRISBANE),
        )
        result = parse_session_page(self._load("session_with_biography.html"), event)
        assert "Parnell Building (07)" in result.venue
        assert "Room: 222" in result.venue

    def test_date_mismatch(self):
        event = make_event(session_id="10001", date=date(2026, 3, 30))
        result = parse_session_page(self._load("session_date_mismatch.html"), event)
        assert result.enriched is False
        assert result.venue is None
        assert result.abstract is None
        assert any(
            "date" in w.lower() and "mismatch" in w.lower() for w in result.warnings
        )

    def test_title_mismatch_warns(self):
        event = make_event(
            session_id="10001",
            title="Different Title",
            date=date(2026, 3, 30),
        )
        result = parse_session_page(self._load("session_normal.html"), event)
        assert result.enriched is True
        assert any("title mismatch" in w.lower() for w in result.warnings)

    def test_time_resolved_from_session_page(self):
        event = make_event(
            session_id="10005",
            date=date(2026, 3, 30),
            start=None,
            end=None,
            time_unconfirmed=True,
        )
        result = parse_session_page(self._load("session_no_time.html"), event)
        assert result.start == datetime(2026, 3, 30, 14, 0, tzinfo=BRISBANE)
        assert result.end == datetime(2026, 3, 30, 15, 0, tzinfo=BRISBANE)
        assert result.time_unconfirmed is False

    def test_end_time_resolved(self):
        event = make_event(
            session_id="10006",
            date=date(2026, 3, 30),
            start=datetime(2026, 3, 30, 12, 0, tzinfo=BRISBANE),
            end=datetime(2026, 3, 30, 13, 0, tzinfo=BRISBANE),
            time_unconfirmed=True,
        )
        result = parse_session_page(self._load("session_end_time_resolve.html"), event)
        assert result.end == datetime(2026, 3, 30, 13, 0, tzinfo=BRISBANE)
        assert result.time_unconfirmed is False

    def test_speaker_resolved_from_session_page(self):
        event = make_event(
            session_id="10012",
            date=date(2026, 3, 30),
            speaker=None,
            affiliation=None,
        )
        result = parse_session_page(self._load("session_no_speaker.html"), event)
        assert result.speaker == "Dr Doug Johnstone"
        assert result.affiliation == "National Research Council Canada"

    def test_main_page_speaker_not_overridden(self):
        event = make_event(
            session_id="10001",
            date=date(2026, 3, 30),
            speaker="Main Page Speaker",
            affiliation="Main Uni",
        )
        result = parse_session_page(self._load("session_normal.html"), event)
        assert result.speaker == "Main Page Speaker"
        assert result.affiliation == "Main Uni"

    @pytest.mark.parametrize(
        "text",
        [
            "TBA",
            "To be announced.",
            "Abstract TBA",
            "N/A",
        ],
    )
    def test_placeholder_abstract_returns_none(self, text):
        html = self._load("session_tba_abstract.html")
        html = html.replace(b"<p>TBA</p>", f"<p>{text}</p>".encode())
        event = make_event(session_id="10002", date=date(2026, 3, 30))
        result = parse_session_page(html, event)
        assert result.abstract is None

    def test_missing_venue_still_enriched(self):
        event = make_event(session_id="10002", date=date(2026, 3, 30))
        result = parse_session_page(self._load("session_tba_abstract.html"), event)
        assert result.venue is None
        assert result.enriched is True

    def test_soft_404_detection(self):
        event = make_event(session_id="10001", date=date(2026, 3, 30))
        result = parse_session_page(self._load("session_soft_404.html"), event)
        assert result.enriched is False
        assert any("soft-404" in w for w in result.warnings)

    def test_cancelled_not_overridden(self):
        event = make_event(
            session_id="10001",
            date=date(2026, 3, 30),
            cancelled=True,
        )
        result = parse_session_page(self._load("session_normal.html"), event)
        assert result.cancelled is True

    def test_warnings_immutable(self):
        event = make_event(
            session_id="10001", date=date(2026, 3, 30), warnings=("existing",)
        )
        result = parse_session_page(self._load("session_normal.html"), event)
        assert "existing" in result.warnings
        assert event.warnings == ("existing",)
