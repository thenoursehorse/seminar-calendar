from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Event:
    # required fields (no defaults)
    session_id: str
    series: str
    title: str
    date: date
    session_url: str

    # optional/defaulted fields
    cancelled: bool = False
    time_unconfirmed: bool = False
    start: datetime | None = None
    end: datetime | None = None
    speaker: str | None = None
    affiliation: str | None = None
    venue: str | None = None
    abstract: str | None = None
    enriched: bool = False
    warnings: tuple[str, ...] = ()
