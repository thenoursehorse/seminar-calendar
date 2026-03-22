import logging
from collections.abc import Sequence
from datetime import datetime, timedelta

from icalendar import Calendar, Event as IcsEvent, Timezone, TimezoneStandard

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

    cal.add_component(_build_brisbane_vtimezone())

    for event in events:
        if event.start is None or event.end is None:
            raise ValueError(
                f"Event {event.session_id} has start={event.start}, "
                f"end={event.end} — pipeline bug"
            )

        uid = f"{event.session_id}-{event.date.strftime('%Y%m%d')}@{UID_DOMAIN}"

        ics_event = IcsEvent()
        ics_event.add("UID", uid)
        ics_event.add("DTSTAMP", dtstamp)
        ics_event.add("DTSTART", event.start)
        ics_event.add("DTEND", event.end)
        ics_event.add("SUMMARY", _build_summary(event))
        ics_event.add("DESCRIPTION", _build_description(event))
        ics_event.add("SEQUENCE", 0)

        if event.venue:
            ics_event.add("LOCATION", event.venue)

        if event.cancelled:
            ics_event.add("STATUS", "CANCELLED")

        cal.add_component(ics_event)

    return cal.to_ical()


def _build_brisbane_vtimezone() -> Timezone:
    tz = Timezone()
    tz.add("TZID", "Australia/Brisbane")
    std = TimezoneStandard()
    std.add("DTSTART", datetime(1970, 1, 1, 0, 0, 0))
    std.add("TZOFFSETFROM", timedelta(hours=10))
    std.add("TZOFFSETTO", timedelta(hours=10))
    std.add("TZNAME", "AEST")
    tz.add_component(std)
    return tz


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
