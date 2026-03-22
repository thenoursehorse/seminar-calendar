import logging
import re
from dataclasses import replace
from datetime import date, datetime, timedelta
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag

from src.constants import (
    BASE_URL,
    PAST_EVENT_CUTOFF_DAYS,
    SESSION_URL_PATTERN,
)
from src.models import Event

log = logging.getLogger(__name__)

BRISBANE = ZoneInfo("Australia/Brisbane")

_DATETIME_RE = re.compile(
    r"(\d{1,2})\s+"
    r"(\w+)\s+"
    r"(\d{4})\s*"
    r"(\d{1,2}):(\d{2})\s*"
    r"(am|pm)"
    r"(?:\s*[\u2013\u2014\-]\s*"
    r"(\d{1,2}):(\d{2})\s*"
    r"(am|pm))?",
    re.IGNORECASE,
)

_DATE_ONLY_RE = re.compile(
    r"(\d{1,2})\s+(\w+)\s+(\d{4})",
)

_MONTH_MAP = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

_KNOWN_LABELS = re.compile(
    r"^(speakers?|affiliations?|abstract|room)\s*:", re.IGNORECASE
)


def parse_main_page(
    html: bytes, series: str, reference_date: date | None = None
) -> list[Event]:
    if reference_date is None:
        reference_date = date.today()

    cutoff = reference_date - timedelta(days=PAST_EVENT_CUTOFF_DAYS)
    soup = BeautifulSoup(html, "lxml")

    # find tabs by link text
    upcoming_panel = _find_tab_panel(soup, "Upcoming sessions")
    past_panel = _find_tab_panel(soup, "Past sessions")

    if upcoming_panel is None and past_panel is None:
        log.error(
            "Could not locate tab navigation — expected links with text "
            "'Upcoming sessions' and 'Past sessions'. Possible page template change."
        )
        return []

    events_by_tab: dict[str, list[Event]] = {"upcoming": [], "past": []}

    for tab_name, panel in [("upcoming", upcoming_panel), ("past", past_panel)]:
        if panel is None:
            log.warning("Could not locate %s tab panel", tab_name)
            continue

        view_content = panel.find("div", class_="view-content")
        if view_content is None:
            log.warning(
                "Found 0 entry blocks in the %s tab — possible template change",
                tab_name,
            )
            continue

        items = view_content.find_all("div", class_="vertical-list__item")
        if not items:
            # structural fallback
            items = []
            for a in view_content.find_all("a", href=SESSION_URL_PATTERN):
                parent = a.find_parent("div", class_="event-session--teaser")
                if parent and parent not in items:
                    items.append(parent)
            if items:
                log.warning(
                    "vertical-list__item class not found — using structural "
                    "fallback for entry detection. Verify DOM structure."
                )
            else:
                log.warning(
                    "Found 0 entry blocks in the %s tab — possible template change",
                    tab_name,
                )
                continue

        # check for pagination (pager is a sibling of view-content, not inside it)
        pager = panel.find(class_="pager-next")
        if pager:
            log.warning(
                "Pagination controls detected — only first page of results "
                "extracted. Pipeline may be missing events."
            )

        for item in items:
            event = _parse_entry_block(item, series, cutoff, reference_date)
            if event is not None:
                events_by_tab[tab_name].append(event)

    # deduplicate: prefer upcoming tab copy
    seen_ids: set[str] = set()
    result: list[Event] = []
    for event in events_by_tab["upcoming"]:
        if event.session_id not in seen_ids:
            seen_ids.add(event.session_id)
            result.append(event)
    for event in events_by_tab["past"]:
        if event.session_id not in seen_ids:
            seen_ids.add(event.session_id)
            result.append(event)

    log.info(
        "%s: %d events extracted (upcoming=%d, past=%d, after dedup=%d)",
        series,
        len(result),
        len(events_by_tab["upcoming"]),
        len(events_by_tab["past"]),
        len(result),
    )
    return result


def parse_session_page(html: bytes, event: Event) -> Event:
    soup = BeautifulSoup(html, "lxml")
    warnings = list(event.warnings)

    # structural validity check (soft-404 detection)
    h1 = soup.find("h1")
    date_field = soup.find("span", class_="date-display-single")
    # exclude date spans inside the sidebar "other sessions" area
    if date_field:
        sidebar = soup.find("div", class_="layout-region__right")
        if sidebar and date_field in sidebar.descendants:
            # find one in main content instead
            main = soup.find("div", class_="layout-region__main")
            date_field = (
                main.find("span", class_="date-display-single") if main else None
            )

    if h1 is None and date_field is None:
        warnings.append(
            f"Session {event.session_id}: session page appears to be a soft-404 "
            "— no title or date found"
        )
        return replace(event, warnings=tuple(warnings))

    # title cross-check (warn-only)
    if h1 is not None:
        page_title = _normalize(h1.get_text())
        if page_title and page_title != _normalize(event.title):
            warnings.append(
                f"Session {event.session_id}: title mismatch — "
                f"main page: {event.title!r}, session page: {page_title!r}"
            )

    # date cross-check
    if date_field is None:
        warnings.append(f"Session {event.session_id}: no date on session page")
        return replace(event, warnings=tuple(warnings))

    session_date_text = _normalize(date_field.get_text())
    session_date, session_start, session_end, _ = _parse_datetime_str(session_date_text)

    if session_date is None:
        warnings.append(
            f"Session {event.session_id}: could not parse session page date"
        )
        return replace(event, warnings=tuple(warnings))

    if session_date != event.date:
        warnings.append(
            f"Session {event.session_id}: main page date is {event.date}, "
            f"session page date is {session_date} — enrichment discarded"
        )
        return replace(event, warnings=tuple(warnings))

    # date matches — proceed with enrichment
    new_start = event.start
    new_end = event.end
    new_time_unconfirmed = event.time_unconfirmed

    # time resolution when main page lacked time entirely
    if event.start is None:
        if session_start is not None:
            new_start = session_start
            new_end = session_end if session_end else session_start + timedelta(hours=1)
            new_time_unconfirmed = session_end is None
        else:
            warnings.append(
                f"Session {event.session_id}: no time on session page either"
            )

    # end-time resolution when main page assumed end
    elif event.time_unconfirmed and event.start is not None:
        if session_start is not None and session_end is not None:
            if session_start == event.start:
                new_end = session_end
                new_time_unconfirmed = False
                log.debug(
                    "Session %s: end time resolved from session page",
                    event.session_id,
                )
            else:
                warnings.append(
                    f"Session {event.session_id}: session page start time differs "
                    f"from main page — keeping main page values"
                )
    else:
        # main page has full time info, check for discrepancy
        if session_start is not None and session_start != event.start:
            warnings.append(
                f"Session {event.session_id}: time discrepancy — main page "
                f"start {event.start}, session page start {session_start}"
            )

    # scope to main content area for speaker/abstract
    main_content = soup.find("div", class_="layout-region__main")
    if main_content is None:
        main_content = soup

    # speaker/affiliation resolution from session page body
    new_speaker = event.speaker
    new_affiliation = event.affiliation

    body_field = main_content.find("div", class_="field-name-field-uq-session-body")
    if body_field and (event.speaker is None or event.affiliation is None):
        field_item = body_field.find("div", class_="field-item")
        if field_item:
            # get lines before the Abstract heading
            lines_before_abstract = []
            for child in field_item.children:
                if isinstance(child, Tag):
                    if child.name in ("h1", "h2", "h3", "h4") and _normalize(
                        child.get_text()
                    ).lower().startswith("abstract"):
                        break
                    text = _normalize(child.get_text(separator="\n"))
                    if text:
                        for line in text.split("\n"):
                            line = _normalize(line)
                            if line:
                                lines_before_abstract.append(line)

            if lines_before_abstract:
                sp, af, sp_warns = _parse_speaker_affiliation(lines_before_abstract)
                warnings.extend(sp_warns)
                if event.speaker is None and sp:
                    new_speaker = sp
                    warnings.append(
                        f"Session {event.session_id}: speaker resolved from "
                        f"session page: {sp}"
                    )
                if event.affiliation is None and af:
                    new_affiliation = af
                    warnings.append(
                        f"Session {event.session_id}: affiliation resolved from "
                        f"session page: {af}"
                    )

    # abstract extraction
    abstract_text = _extract_abstract(body_field) if body_field else None

    # venue extraction from sidebar
    venue = _extract_venue(soup)

    return replace(
        event,
        start=new_start,
        end=new_end,
        time_unconfirmed=new_time_unconfirmed,
        speaker=new_speaker,
        affiliation=new_affiliation,
        venue=venue,
        abstract=abstract_text,
        enriched=True,
        warnings=tuple(warnings),
    )


def _normalize(text: str) -> str:
    return text.replace("\xa0", " ").strip()


def _strip_label(text: str, label_pattern: str) -> str | None:
    match = re.match(label_pattern, text, re.IGNORECASE)
    if match:
        return text[match.end() :].strip()
    return None


def _to_24h(hour: int, ampm: str) -> int:
    ampm = ampm.lower()
    return hour % 12 + (12 if ampm == "pm" else 0)


def _parse_month(name: str) -> int | None:
    return _MONTH_MAP.get(name.lower())


def _parse_datetime_str(
    text: str,
) -> tuple[date | None, datetime | None, datetime | None, bool]:
    """Parse date/time string.

    Returns
    -------
    (date, start, end, time_unconfirmed)
    """
    m = _DATETIME_RE.search(text)
    if m:
        day = int(m.group(1))
        month = _parse_month(m.group(2))
        year = int(m.group(3))
        if month is None:
            return None, None, None, True

        try:
            d = date(year, month, day)
            start_h = _to_24h(int(m.group(4)), m.group(6))
            start_m = int(m.group(5))
            start = datetime(year, month, day, start_h, start_m, tzinfo=BRISBANE)

            if m.group(7) is not None:
                end_h = _to_24h(int(m.group(7)), m.group(9))
                end_m = int(m.group(8))
                end = datetime(year, month, day, end_h, end_m, tzinfo=BRISBANE)
                return d, start, end, False
            end = start + timedelta(hours=1)
            return d, start, end, True
        except ValueError:
            return None, None, None, True

    # date only, no time
    m2 = _DATE_ONLY_RE.search(text)
    if m2:
        day = int(m2.group(1))
        month = _parse_month(m2.group(2))
        year = int(m2.group(3))
        if month is not None:
            try:
                return date(year, month, day), None, None, True
            except ValueError:
                pass

    return None, None, None, True


def _find_tab_panel(soup: BeautifulSoup, tab_text: str) -> Tag | None:
    for a in soup.find_all("a"):
        if _normalize(a.get_text()) == tab_text:
            href = a.get("href", "")
            if href.startswith("#"):
                panel = soup.find(id=href[1:])
                if panel:
                    return panel
    return None


def _parse_speaker_affiliation(
    text_lines: list[str],
) -> tuple[str | None, str | None, list[str]]:
    """Extract speaker/affiliation from text lines.

    Returns
    -------
    (speaker, affiliation, warnings)
    """
    warnings: list[str] = []

    # 1. labelled format
    speaker = None
    affiliation = None
    for line in text_lines:
        if speaker is None:
            val = _strip_label(line, r"speakers?\s*:\s*")
            if val:
                speaker = val
                continue
        if affiliation is None:
            val = _strip_label(line, r"affiliations?\s*:\s*")
            if val:
                affiliation = val
                continue

    if speaker is not None:
        return speaker, affiliation, warnings

    # 2. multi-speaker Name: Institution
    candidate_lines = [
        line
        for line in text_lines
        if ":" in line and not line.startswith(":") and not _KNOWN_LABELS.match(line)
    ]
    if len(candidate_lines) >= 2:
        names = []
        institutions = []
        for line in candidate_lines:
            name, _, inst = line.partition(":")
            names.append(name.strip())
            institutions.append(inst.strip())
        speaker = " and ".join(names)
        affiliation = " and ".join(institutions)
        warnings.append("multi-speaker Name: Institution format detected")
        return speaker, affiliation, warnings

    # 3. positional fallback
    if text_lines:
        speaker = text_lines[0]
        affiliation = text_lines[1] if len(text_lines) > 1 else None
        if speaker:
            warnings.append("positional fallback used for speaker/affiliation")
        return speaker, affiliation, warnings

    return None, None, warnings


def _parse_entry_block(
    item: Tag, series: str, cutoff: date, reference_date: date
) -> Event | None:
    # find the content div (may be inside event-session--teaser wrapper)
    content = item.find("div", class_="event-session__content")
    if content is None:
        content = item

    # title + session URL
    title_h3 = content.find("h3", class_="event-session__title")
    if title_h3 is None:
        title_h3 = content.find("h3")
    if title_h3 is None:
        log.warning("Entry block with no title heading found — skipping.")
        return None

    title_a = title_h3.find("a")
    if title_a is None:
        log.warning("Entry block with no title link found — skipping.")
        return None

    title = _normalize(title_a.get_text())
    href = title_a.get("href", "")
    m = SESSION_URL_PATTERN.search(href)
    if not m:
        log.error("Session URL does not match expected pattern: %s", href)
        return None

    session_id = m.group(1)
    session_url = urljoin(BASE_URL, href)

    # date/time
    date_div = content.find("div", class_="event-session__date")
    if date_div is None:
        date_div = content.find("span", class_="date-display-single")

    warnings: list[str] = []
    if date_div is None:
        log.error("Session %s: no date element found — skipping", session_id)
        return None

    date_text = _normalize(date_div.get_text())
    event_date, start, end, time_unconfirmed = _parse_datetime_str(date_text)

    if event_date is None:
        log.error(
            "Session %s: could not parse date from %r — skipping", session_id, date_text
        )
        return None

    # cutoff filter
    if event_date < cutoff:
        log.debug(
            "Session %s: date %s before cutoff %s — discarded",
            session_id,
            event_date,
            cutoff,
        )
        return None

    if start is not None and end is not None and time_unconfirmed:
        warnings.append(f"Session {session_id}: assumed end time (start + 1 hour)")

    if start is None:
        warnings.append(f"Session {session_id}: no time found on main page")

    # cancelled
    cancelled = False
    status_div = content.find("div", class_="event-session__status")
    if status_div:
        status_text = _normalize(status_div.get_text()).lower()
        if "cancel" in status_text:
            cancelled = True
    if not cancelled:
        # fallback: text search in the entry block
        entry_text = _normalize(content.get_text()).lower()
        if re.search(r"\bcancell?ed\b", entry_text):
            cancelled = True

    # speaker/affiliation
    summary_div = content.find("div", class_="event-session__summary")
    speaker = None
    affiliation = None

    if summary_div:
        field_item = summary_div.find("div", class_="field-item")
        if field_item:
            # extract text lines by splitting on <br> tags
            text_content = _normalize(field_item.get_text(separator="\n"))
            lines = [
                _normalize(line)
                for line in text_content.split("\n")
                if _normalize(line)
            ]

            # filter out lines that are dates, series links, abstract markers
            filtered_lines = []
            skip_rest = False
            for line in lines:
                if skip_rest:
                    break
                if line.lower().startswith("abstract"):
                    skip_rest = True
                    continue
                if _DATETIME_RE.search(line):
                    continue
                if series.lower() in line.lower() and "in " in line.lower():
                    continue
                if re.match(r"^cancel", line, re.IGNORECASE):
                    continue
                filtered_lines.append(line)

            speaker, affiliation, spk_warnings = _parse_speaker_affiliation(
                filtered_lines
            )
            warnings.extend(spk_warnings)

    if speaker is None:
        warnings.append(f"Session {session_id}: no speaker found on main page")
    if affiliation is None:
        warnings.append(f"Session {session_id}: no affiliation found on main page")

    return Event(
        session_id=session_id,
        series=series,
        title=title,
        date=event_date,
        session_url=session_url,
        cancelled=cancelled,
        time_unconfirmed=time_unconfirmed,
        start=start,
        end=end,
        speaker=speaker,
        affiliation=affiliation,
        warnings=tuple(warnings),
    )


def _extract_abstract(body_field: Tag) -> str | None:
    field_item = body_field.find("div", class_="field-item")
    if field_item is None:
        return None

    # find the Abstract heading
    abstract_heading = None
    for el in field_item.find_all(["h1", "h2", "h3", "h4"]):
        if _normalize(el.get_text()).lower() == "abstract":
            abstract_heading = el
            break

    if abstract_heading is None:
        return None

    abstract_level = int(abstract_heading.name[1])
    paragraphs: list[str] = []

    for sib in abstract_heading.next_siblings:
        if not isinstance(sib, Tag):
            continue

        sib_text = _normalize(sib.get_text())

        # heading checks
        if sib.name in ("h1", "h2", "h3", "h4", "h5"):
            if not sib_text:
                continue  # skip empty headings (CMS artifacts)
            if sib_text.lower().startswith("about "):
                break
            if sib_text.lower() == "biography":
                break
            sib_level = int(sib.name[1])
            if sib_level <= abstract_level:
                break

        if sib_text:
            paragraphs.append(sib.get_text(separator="\n").strip())

    if not paragraphs:
        return None

    result = "\n\n".join(paragraphs)
    # placeholder normalization
    if result.lower().strip() in ("tba", "to be announced", "tbc"):
        return None
    return result


def _extract_venue(soup: BeautifulSoup) -> str | None:
    sidebar = soup.find("div", class_="layout-region__right")
    if sidebar is None:
        return None

    venue_pane = sidebar.find("div", class_="pane-node-field-uq-session-location")
    if venue_pane is None:
        # fallback: look for Venue heading
        for el in sidebar.find_all(True):
            if _normalize(el.get_text()) == "Venue":
                venue_pane = el.parent
                break

    if venue_pane is None:
        return None

    content = venue_pane.find("div", class_="panel-pane__content")
    if content is None:
        content = venue_pane

    raw_text = content.get_text(separator="\n")
    # normalize: collapse whitespace, join building + room
    parts = [_normalize(line) for line in raw_text.split("\n") if _normalize(line)]
    # strip heading label if present (from fallback path)
    if parts and parts[0].lower() == "venue":
        parts = parts[1:]
    if not parts:
        return None

    # join parts, handling Room: that may be split
    result_parts: list[str] = []
    i = 0
    while i < len(parts):
        part = parts[i]
        if part == "Room:" and i + 1 < len(parts):
            result_parts.append(f"Room: {parts[i + 1]}")
            i += 2
        else:
            result_parts.append(part)
            i += 1

    return ", ".join(result_parts)
