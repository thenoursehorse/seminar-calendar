from datetime import timedelta

from src.constants import (
    MAX_FUTURE_DAYS,
    MAX_PLAUSIBLE_EVENTS,
    MIN_PLAUSIBLE_EVENTS,
    PAST_EVENT_CUTOFF_DAYS,
)
from src.validator import validate
from tests.conftest import REFERENCE_DATE, make_event


class TestValidator:
    def test_empty_list_fails(self):
        result = validate([], reference_date=REFERENCE_DATE)
        assert not result.passed
        assert any("empty" in f.lower() for f in result.failures)

    def test_empty_title_fails(self):
        events = [make_event(title="")]
        result = validate(events, reference_date=REFERENCE_DATE)
        assert not result.passed

    def test_missing_start_fails(self):
        events = [make_event(start=None)]
        result = validate(events, reference_date=REFERENCE_DATE)
        assert not result.passed

    def test_missing_end_fails(self):
        events = [make_event(end=None)]
        result = validate(events, reference_date=REFERENCE_DATE)
        assert not result.passed

    def test_time_unconfirmed_warns(self):
        events = [make_event(time_unconfirmed=True)]
        result = validate(events, reference_date=REFERENCE_DATE)
        assert result.passed
        assert any("time_unconfirmed" in w for w in result.warnings)

    def test_cancelled_time_unconfirmed_no_warn(self):
        events = [make_event(time_unconfirmed=True, cancelled=True)]
        result = validate(events, reference_date=REFERENCE_DATE)
        assert not any("time_unconfirmed" in w for w in result.warnings)

    def test_zero_upcoming_warns(self):
        past_date = REFERENCE_DATE - timedelta(days=30)
        events = [make_event(date=past_date)]
        result = validate(events, reference_date=REFERENCE_DATE)
        assert result.passed
        assert any("upcoming" in w.lower() for w in result.warnings)

    def test_date_beyond_cutoff_warns(self):
        old_date = REFERENCE_DATE - timedelta(days=PAST_EVENT_CUTOFF_DAYS + 1)
        events = [make_event(date=old_date)]
        result = validate(events, reference_date=REFERENCE_DATE)
        assert any("cutoff" in w.lower() for w in result.warnings)

    def test_far_future_warns(self):
        future_date = REFERENCE_DATE + timedelta(days=MAX_FUTURE_DAYS + 1)
        events = [make_event(date=future_date)]
        result = validate(events, reference_date=REFERENCE_DATE)
        assert any("beyond" in w.lower() for w in result.warnings)

    def test_duplicate_uids_fail(self):
        events = [
            make_event(session_id="123"),
            make_event(session_id="123"),
        ]
        result = validate(events, reference_date=REFERENCE_DATE)
        assert not result.passed
        assert any("duplicate" in f.lower() for f in result.failures)

    def test_valid_events_pass(self):
        events = [make_event(session_id=str(i)) for i in range(15)]
        result = validate(events, reference_date=REFERENCE_DATE)
        assert result.passed

    def test_invalid_session_url_fails(self):
        events = [make_event(session_url="https://example.com/bad")]
        result = validate(events, reference_date=REFERENCE_DATE)
        assert not result.passed

    def test_few_events_warns(self):
        events = [
            make_event(session_id=str(i)) for i in range(MIN_PLAUSIBLE_EVENTS - 1)
        ]
        result = validate(events, reference_date=REFERENCE_DATE)
        assert any(str(MIN_PLAUSIBLE_EVENTS) in w for w in result.warnings)

    def test_many_events_warns(self):
        events = [
            make_event(session_id=str(i)) for i in range(MAX_PLAUSIBLE_EVENTS + 1)
        ]
        result = validate(events, reference_date=REFERENCE_DATE)
        assert any(str(MAX_PLAUSIBLE_EVENTS) in w for w in result.warnings)

    def test_low_enrichment_rate_warns(self):
        events = [make_event(session_id=str(i), enriched=False) for i in range(10)]
        result = validate(events, reference_date=REFERENCE_DATE)
        assert any("enrichment" in w.lower() for w in result.warnings)

    def test_few_events_no_enrichment_warning(self):
        events = [make_event(session_id=str(i), enriched=False) for i in range(4)]
        result = validate(events, reference_date=REFERENCE_DATE)
        assert not any("enrichment" in w.lower() for w in result.warnings)
