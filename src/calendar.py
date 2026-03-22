import logging
from collections.abc import Sequence
from datetime import datetime, timezone

from icalendar import Calendar, Event as IcsEvent

from src.constants import PRODID, UID_DOMAIN
from src.models import Event

log = logging.getLogger(__name__)


def generate_ics(
    events: Sequence[Event], calendar_name: str, dtstamp: datetime
) -> bytes:
    cal = Calendar()
    cal.add("PRODID", PRODID)
    cal.add("VERSION", "2.0")
    cal.add("CALSCALE", "GREGORIAN")
    cal.add("METHOD", "PUBLISH")
    cal.add("X-WR-CALNAME", calendar_name)
    cal.add("X-WR-TIMEZONE", "UTC")

    for event in events:
        if event.start is None or event.end is None:
            raise ValueError(
                f"Event {event.session_id} has start={event.start}, "
                f"end={event.end} — pipeline bug"
            )

        uid = f"{event.session_id}-{event.date.strftime('%Y%m%d')}@{UID_DOMAIN}"

        start_utc = event.start.astimezone(timezone.utc)
        end_utc = event.end.astimezone(timezone.utc)

        ics_event = IcsEvent()
        ics_event.add("UID", uid)
        ics_event.add("DTSTAMP", dtstamp)
        ics_event.add("CREATED", dtstamp)
        ics_event.add("LAST-MODIFIED", dtstamp)
        ics_event.add("DTSTART", start_utc)
        ics_event.add("DTEND", end_utc)
        ics_event.add("SUMMARY", _build_summary(event))
        ics_event.add("DESCRIPTION", _build_description(event))
        ics_event.add("SEQUENCE", 0)
        ics_event.add("TRANSP", "OPAQUE")

        if event.venue:
            ics_event.add("LOCATION", event.venue)

        if event.cancelled:
            ics_event.add("STATUS", "CANCELLED")
        else:
            ics_event.add("STATUS", "CONFIRMED")

        cal.add_component(ics_event)

    return cal.to_ical()


def _build_summary(event: Event) -> str:
    parts: list[str] = []

    if event.cancelled:
        parts.append("[CANCELLED]")

    parts.append(event.title)

    if event.speaker:
        if event.affiliation:
            parts.append(f"— {event.speaker} ({event.affiliation})")
        else:
            parts.append(f"— {event.speaker}")

    if event.time_unconfirmed and not event.cancelled:
        parts.append("[TIME TBC]")

    return " ".join(parts)


def _build_description(event: Event) -> str:
    if event.abstract:
        return f"{event.abstract}\n\n{event.session_url}"
    return event.session_url
