import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from src.constants import (
    MAX_FUTURE_DAYS,
    MAX_PLAUSIBLE_EVENTS,
    MIN_ENRICHMENT_RATE,
    MIN_PLAUSIBLE_EVENTS,
    PAST_EVENT_CUTOFF_DAYS,
    SESSION_URL_PATTERN,
    UID_DOMAIN,
)
from src.models import Event

log = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    passed: bool
    failures: list[str]
    warnings: list[str]


def validate(
    events: Sequence[Event], reference_date: date | None = None
) -> ValidationResult:
    if reference_date is None:
        reference_date = date.today()

    failures: list[str] = []
    warnings: list[str] = []

    # non-empty
    if not events:
        failures.append("Event list is empty")
        return ValidationResult(passed=False, failures=failures, warnings=warnings)

    # no upcoming events
    upcoming = [e for e in events if e.date >= reference_date]
    if not upcoming:
        warnings.append("Zero upcoming events (date >= reference_date)")

    cutoff = reference_date - timedelta(days=PAST_EVENT_CUTOFF_DAYS)

    uids: set[str] = set()
    for event in events:
        # title
        if not event.title:
            failures.append(f"Session {event.session_id}: empty title")

        # date
        if event.date is None:
            failures.append(f"Session {event.session_id}: missing date")
            continue

        # start/end
        if event.start is None:
            failures.append(f"Session {event.session_id}: start is None")
        if event.end is None:
            failures.append(f"Session {event.session_id}: end is None")

        # time_unconfirmed (non-cancelled only)
        if event.time_unconfirmed and not event.cancelled:
            warnings.append(f"Session {event.session_id}: time_unconfirmed=True")

        # date too far in past
        if event.date < cutoff:
            warnings.append(
                f"Session {event.session_id}: date {event.date} before cutoff {cutoff}"
            )

        # date too far in future
        future_limit = reference_date + timedelta(days=MAX_FUTURE_DAYS)
        if event.date > future_limit:
            warnings.append(
                f"Session {event.session_id}: date {event.date} beyond {MAX_FUTURE_DAYS} days"
            )

        # session URL
        if not SESSION_URL_PATTERN.search(event.session_url):
            failures.append(
                f"Session {event.session_id}: invalid session URL {event.session_url}"
            )

        # duplicate UIDs
        uid = f"{event.session_id}-{event.date.strftime('%Y%m%d')}@{UID_DOMAIN}"
        if uid in uids:
            failures.append(f"Duplicate UID: {uid}")
        uids.add(uid)

    # plausible event count
    n = len(events)
    if n < MIN_PLAUSIBLE_EVENTS:
        warnings.append(f"Only {n} events (minimum expected: {MIN_PLAUSIBLE_EVENTS})")
    if n > MAX_PLAUSIBLE_EVENTS:
        warnings.append(f"{n} events exceeds maximum expected {MAX_PLAUSIBLE_EVENTS}")

    # enrichment rate
    if n >= 5:
        enriched_count = sum(1 for e in events if e.enriched)
        rate = enriched_count / n
        if rate < MIN_ENRICHMENT_RATE:
            warnings.append(
                f"Low enrichment rate: {enriched_count}/{n} "
                f"({rate:.0%} < {MIN_ENRICHMENT_RATE:.0%})"
            )

    passed = len(failures) == 0
    return ValidationResult(passed=passed, failures=failures, warnings=warnings)
