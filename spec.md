# UQ Seminar Calendar Generator — Specification

## Overview

A stateless Python application that scrapes the UQ School of Mathematics and Physics seminar pages, generates `.ics` calendar files, and publishes them via GitHub Pages. Subscribers add a URL to their calendar app (Google Calendar, Apple Calendar, Outlook) and receive automatic updates.

Each run is a clean snapshot of the current website state. No data is carried over between runs.

Calendars include both upcoming sessions and past sessions within a rolling 1-year lookback window. This ensures subscribers retain historical context and that the pipeline can distinguish a genuinely empty upcoming schedule from a broken parser.

---

## Tech Stack

| Component | Choice | Rationale |
|---|---|---|
| Language | Python 3.12+ | Natural fit for HTML parsing + calendar generation. Widely supported in CI. |
| HTML parsing | `beautifulsoup4` + `lxml` | Standard, well-documented, robust against messy HTML. |
| HTTP | `requests` | Simple, synchronous. No need for async given the modest request volume (~50 requests per series per run). |
| Calendar generation | `icalendar` | Mature library for producing standards-compliant `.ics` files. |
| Timezone handling | `zoneinfo` (stdlib) | No third-party dependency needed for `Australia/Brisbane`. |
| Package management | `uv` | Handles Python version, dependency resolution, and lockfile (`uv.lock`) in one tool. |
| CI/CD | GitHub Actions | Free, runs on cron, commits output back to repo. |
| Hosting | GitHub Pages | Free static hosting. Serves the `.ics` files at stable public URLs. |
| Testing | `pytest` + `freezegun` | Standard. Snapshot-based unit tests + optional live canary tests. `freezegun` pins `date.today()` in tests. |

---

## Data Model

### Event

A single dataclass representing one talk/session on the calendar.

| Field | Type | Source | Required | Default | Description |
|---|---|---|---|---|---|
| `session_id` | `str` | Main page (from URL) | Yes | — | Stable unique key per talk. Extracted from `/event/session/17611`. Used to generate the `.ics` UID. |
| `series` | `str` | Configuration | Yes | — | Which seminar series this belongs to, e.g. `"Physics colloquium"`. Determines `.ics` filename and calendar name. |
| `title` | `str` | Main page | Yes | — | Talk title. Can be `"TBA"`. The `[CANCELLED]` prefix is applied at `.ics` generation time, not stored here. |
| `date` | `date` | Main page | Yes | — | The date of the talk. This is the one truly required temporal field — without a date, there is no calendar event. Always parsed from the main page. |
| `start` | `datetime` (tz-aware) `\| None` | Main page or session page | No | `None` | Talk start time. Always `Australia/Brisbane`. Parsed from strings like `"23 March 2026 11:00am"`. `None` if only a date was available with no time on either page. |
| `end` | `datetime` (tz-aware) `\| None` | Main page or session page | No | `None` | Talk end time. Parsed explicitly from the time range. `None` if only a date was available with no time on either page. |
| `time_unconfirmed` | `bool` | Internal | Yes | `False` | `True` when neither the main page nor the session page provided a parseable start/end time, and the defaults (11:00am–12:00pm) were applied. Also `True` when the main page provided only a start time with no range and the end time was assumed (see Feature 2), unless the session page later resolves the end time (see Feature 3). Controls `[TIME TBC]` suffix on `.ics` `SUMMARY`. |
| `speaker` | `str \| None` | Main page or session page | No | `None` | Full name with title, e.g. `"Professor James Annett"`. `None` if not yet listed (save-the-date entry) and not resolved from the session page. Multiple speakers are stored as a single concatenated string (e.g. `"Judy-Anne Osborne and Amelia Dickenson-Jones"`). |
| `affiliation` | `str \| None` | Main page or session page | No | `None` | Speaker's institution, e.g. `"University of Bristol"`. `None` if not yet listed and not resolved from the session page. Multiple affiliations are stored as a single concatenated string. |
| `session_url` | `str` | Main page | Yes | — | Full URL to the individual session page. Used for enrichment and included in the calendar event's `DESCRIPTION`. Always present for any event that passes parsing (events with invalid URLs are skipped). |
| `venue` | `str \| None` | Session page | No | `None` | Building and room, e.g. `"Physics Annexe (06), Room: 407"`. May include additional location information such as Zoom links (e.g. `"Forgan Smith Building (01), Room: E302 and via Zoom (https://...)"`). `None` if enrichment failed or if the session page had no venue section. Maps to `.ics` `LOCATION`. |
| `abstract` | `str \| None` | Session page | No | `None` | Talk abstract. `None` if enrichment failed or if abstract text was only a placeholder (e.g. `"TBA"`). Maps to `.ics` `DESCRIPTION`. |
| `cancelled` | `bool` | Main page | Yes | `False` | Whether the talk is marked cancelled on the website. Controls `[CANCELLED]` title prefix and `.ics` `STATUS:CANCELLED`. |
| `enriched` | `bool` | Internal | Yes | `False` | Whether session page enrichment succeeded. Set to `True` when the session page was fetched and parsed without a date mismatch, regardless of whether all enrichment fields (venue, abstract, speaker, affiliation) were populated — even partial enrichment (e.g. venue but no abstract) counts. A session page that loads successfully but has no venue heading and no abstract still counts as `enriched = True` (the page was structurally valid; it just had no optional data). For logging/debugging only. Not written to `.ics`. |
| `warnings` | `tuple[str, ...]` | Internal | Yes | `()` | Issues encountered during processing (date mismatches, failed fetches, missing fields). Logged, not written to `.ics`. |

### Design notes

- The `title` field stores the raw title from the website. The `[CANCELLED]` prefix is applied only at `.ics` generation time, derived from the `cancelled` boolean.
- Each pipeline stage returns a new `Event` with a new `warnings` tuple (concatenation via `(*old_event.warnings, "new warning")`). Stages never mutate previously returned `Event` objects.
- The `warnings` field uses `tuple[str, ...]` rather than `list[str]` to enforce immutability at runtime. Because the `Event` dataclass is `frozen=True`, field reassignment is prevented, but a `list` would still allow in-place `.append()` mutations that could silently corrupt a previous stage's output. A `tuple` makes any such mutation fail immediately with an `AttributeError`, catching pipeline bugs early.
- `enriched` defaults to `False` and is set to `True` only after successful session page parsing with no date mismatch. Partial enrichment (e.g. venue populated but abstract missing, or speaker filled in but venue absent) still counts as `enriched = True`. Note: `enriched` means "session page was successfully processed" — it is `True` even if the session page provided no new data (e.g. all fields were already populated from the main page). It does *not* mean "event was enriched with new data." **Naming rationale:** A more precise name would be `session_page_parsed`, but `enriched` is used throughout this spec and in log messages. The implementer may rename it if desired, but should update all references consistently. The key point is: the validator's `MIN_ENRICHMENT_RATE` check measures "what fraction of session pages were reachable and structurally valid," not "what fraction of events gained new data from enrichment."
- **Soft-404 detection:** Some CMSes return HTTP 200 with a generic "page not found" body instead of a proper 404. If a session page loads successfully (200 OK) but lacks the expected structural elements (no date field, no title heading), the session page parser should treat this as a structural failure: log a warning mentioning a possible soft-404, set `enriched = False`, and skip enrichment. This prevents silent enrichment failures that would be hard to diagnose from logs alone (the fetcher would show success, but the enrichment rate would drop).
- Cancelled events remain in the calendar after their date passes, reflecting whatever the main page shows.
- A `date` is always required — it is the minimum viable information for a calendar entry. Without it, the event is skipped entirely.
- When neither the main page nor the session page provides a parseable time, the defaults from `DEFAULT_START_HOUR` and `DEFAULT_DURATION_HOURS` (see `src/constants.py`) are applied. The primary application point is the enrichment stage (Feature 3). However, if enrichment is skipped entirely (e.g. session page fetch failure), defaults are applied by the orchestrator's post-enrichment fixup step (see Feature 7, step 3d). `time_unconfirmed` is set to `True`, and a warning is appended. The `.ics` `SUMMARY` receives a `[TIME TBC]` suffix so subscribers know the time may change (unless the event is cancelled — see Feature 4). See Feature 3 for the case where the session page has a time even though the main page does not.
- When the main page provides only a start time with no range separator, the end time is assumed (`start + 1 hour`). `time_unconfirmed` is set to `True` in this case (see Feature 2), because the subscriber should know the end time is a guess. This is a deliberate departure from treating "start time present" as fully confirmed — the end time is still an assumption. The session page enricher (Feature 3) may later resolve the end time from the session page's full time range and clear `time_unconfirmed`.
- Missing `speaker` or `affiliation` each produce a warning but do not prevent the event from being included. The `SUMMARY` format degrades gracefully (e.g. just the title if both are missing).
- Multiple speakers and/or affiliations are stored as single concatenated strings rather than lists. The data model does not attempt to decompose multi-speaker entries. This is a deliberate simplification: multi-speaker talks are rare, and the concatenated string displays correctly in calendar SUMMARY fields.
- Same-day events (two different talks on the same date) are expected and handled correctly by the UID scheme, which includes the `session_id`. No deduplication or collision check is needed at the date level.
- Entries with title-embedded metadata (pre-2020, e.g. sessions 8093, 7739) are parsed using the same logic as modern entries. The `title` field will contain the entire embedded string (e.g. `Prof John Lattanzio, "The Most Important Lessons from Apollo, 50 years later..." 25/10/2019 11:00am`). These entries are discarded by the date cutoff filter before they reach the ICS generator, so the unusual title format has no effect on output.

---

## Parsing Strategy

This section defines the overarching parsing philosophy. Individual features reference it rather than repeating it.

### Content scoping before extraction

Both the main page and session page contain extensive navigation menus, footers, and boilerplate that repeat across all UQ pages. Before extracting any event data, parsers must first locate the relevant content container and restrict all subsequent searches to within that container.

- **Main page:** Locate the tab panel containers (see Feature 2). All extraction operates within these containers only.
- **Session page:** Locate the main content area (the container holding the title, date, speaker, and abstract) and the sidebar (holding the venue). All extraction operates within these two regions only. The navigation, footer, and other page chrome are excluded.

Never call BeautifulSoup `find()` or `find_all()` on the root `soup` object for event data — always scope to the relevant container first.

### Text-first extraction

Parsers use BeautifulSoup with **text-content-based selectors as the primary mechanism** for extracting data. For example: find an element whose text begins with `"Speaker:"` and strip the label, rather than selecting `div.views-field-speaker span.field-content`.

CSS class names and DOM IDs (documented in Features 2 and 3) are used **only for scoping** — e.g., locating the tab containers, distinguishing main content from sidebar. They are never the sole selector for data extraction.

**Decision rule for the implementer:** if a CSS class and a text pattern could both locate a piece of data, use the text pattern. Use the CSS class only to narrow the search area first.

**Clarification on structural selectors:** For data that has no distinguishing text label (e.g. the title link inside an `<h3>` element, or the session URL from an `<a href>`), structural selectors are acceptable as the primary mechanism. The text-first rule applies specifically to labelled data fields (`Speaker:`, `Affiliation:`, `Abstract:`, `Venue:`, date strings) where the human-readable label is a more stable anchor than the surrounding CSS class names.

UQ has a much stronger reason to preserve human-readable labels like "Speaker:" and "Affiliation:" than to preserve internal CSS class names or template structure.

### Label stripping

When extracting labelled fields (e.g. `Speaker: Professor James Annett`), use a regex to strip the label and normalize whitespace in a single operation. The recommended approach:

```python
import re

def strip_label(text: str, label_pattern: str) -> str | None:
    """Strip a label prefix and return the remainder, or None if no match.
    
    label_pattern should match the label including optional plural 's',
    e.g. r'^Speakers?:\s*' for Speaker:/Speakers:.
    """
    match = re.match(label_pattern, text, re.IGNORECASE)
    if match:
        return text[match.end():].strip()
    return None

# Usage:
speaker = strip_label(normalized_text, r'^Speakers?:\s*')
affiliation = strip_label(normalized_text, r'^Affiliations?:\s*')
```

This handles variable whitespace after the colon (`Speaker: Name`, `Speaker:Name`, `Speaker:  Name`) and the singular/plural variants in one pass.

### Whitespace normalization

UQ's CMS inserts `&nbsp;` (`\xa0`) in various places. BeautifulSoup decodes these as `\xa0`. All extracted text should be normalized by replacing `\xa0` with regular spaces, then stripping, before any text matching or storage. Use a helper like `text.replace('\xa0', ' ').strip()` consistently. **Ordering note:** When extracting labelled fields (e.g. `Speaker: Professor James Annett`), apply whitespace normalization *after* label removal as well — the live page has trailing whitespace after some speaker names (e.g. `Howard Wiseman   ` with trailing spaces before a line break). The sequence should be: extract text → normalize `\xa0` → strip → remove label prefix → strip again.

### Character encoding

When using BeautifulSoup, pass `response.content` (raw bytes) rather than `response.text` (pre-decoded string) to let BeautifulSoup's encoding detection work correctly. UQ's server may not always set the charset header, and BeautifulSoup's detection is more robust than `requests`' heuristic.

### Date/time parsing

The website uses date/time strings in the format `"23 March 2026 11:00am–12:00pm"`. The parser must handle the following variants observed on the live page:

- Full range: `"23 March 2026 11:00am–12:00pm"` (en-dash separator)
- Non-hour-boundary times: `"15 August 2025 10:30am–11:30am"`
- Noon/1pm times: `"20 June 2025 12:00pm–1:00pm"`
- Single time with no range: `"11 December 2017 12:00pm"` (older entries)
- Long duration events: `"27 July 2018 1:00pm–4:00pm"` (3-hour event — observed on session 5810)

The range separator may be an en-dash (`\u2013`), em-dash (`\u2014`), or hyphen-minus (`-`). All three must be accepted.

**Implementation:** Use a regex to extract the date and time components, then construct `datetime` objects directly using manual 12-hour-to-24-hour conversion. This avoids all locale issues with `strptime`'s `%p` directive.

**Reference regex and parsing logic:**

```python
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

BRISBANE = ZoneInfo("Australia/Brisbane")

# Matches: "23 March 2026 11:00am–12:00pm" or "11 December 2017 12:00pm"
# Groups: day, month_name, year, start_hour, start_min, start_ampm,
#         optional(end_hour, end_min, end_ampm)
_DATETIME_RE = re.compile(
    r'(\d{1,2})\s+'               # day
    r'(\w+)\s+'                    # month name
    r'(\d{4})\s+'                  # year
    r'(\d{1,2}):(\d{2})'          # start hour:min
    r'(am|pm)'                     # start am/pm
    r'(?:\s*[\u2013\u2014\-]\s*'   # optional range separator (en-dash, em-dash, hyphen)
    r'(\d{1,2}):(\d{2})'          # end hour:min
    r'(am|pm))?',                  # end am/pm
    re.IGNORECASE
)

def _to_24h(hour: int, minute: int, ampm: str) -> tuple[int, int]:
    """Convert 12-hour time to 24-hour. Returns (hour, minute)."""
    ampm = ampm.lower()
    hour_24 = hour % 12 + (12 if ampm == 'pm' else 0)
    return hour_24, minute
```

This avoids all locale issues. The month name is resolved via a lookup dict or `datetime.strptime("%B")` on just the month token (which is locale-independent for English month names in practice, and GitHub Actions always provides an English locale).

### Cancelled event detection

The parser detects cancelled events by searching for text matching `"cancelled"` or `"canceled"` (case-insensitive) within the entry block. Both British and American spellings are accepted. The match is a whole-word prefix match (e.g. `"Cancellation notice"` would also match, which is acceptable — the word "cancel" in any form within an event entry reliably indicates cancellation on this website).

### Known HTML variants

The UQ website uses several different formats across its history of entries. The parser must handle all variants that fall within the `PAST_EVENT_CUTOFF_DAYS` window. This is a consolidated reference — individual features reference specific variants where relevant.

| Variant | Example session(s) | Description |
|---|---|---|
| Labelled speaker | 17611, 17612 | `Speaker: Name` / `Affiliation: Institution` with explicit labels |
| Plural labelled speaker | (rare) | `Speakers: Name1 and Name2` / `Affiliations: Inst1 and Inst2` |
| Unlabelled speaker | 17460, 16775 | Bare text lines: speaker name on one line, institution on the next, no labels |
| Multi-speaker `Name: Institution` | 16840 | Each line is `Name: Institution` with no `Speaker:` label. See Feature 2 for dedicated handling of this format. |
| Unlabelled multi-speaker with "and" | 11697 | `Emeritus Professor Ross McKenzie and Dr Henry Nourse` / `University of Queensland` — two speakers on one line joined by "and", single affiliation on next line. Handled correctly by the positional fallback (step 3): the full line becomes `speaker`, the next line becomes `affiliation`. No special parsing needed. |
| No speaker | 17092 | No speaker/affiliation on main page at all. Session page provides them. |
| Inline abstract on main page | 16952 | `Abstract:` followed by paragraph text appears in the main page listing. Note: on the live page the label `Abstract:` is followed by a newline and then the paragraph. **Verify in devtools** whether the label and paragraph are a single element or separate siblings, as this affects the skip logic. |
| Title-embedded metadata | 8093, 7739 | Speaker, title, date, time all embedded in the `<h3>` title. Pre-2020 entries, well outside cutoff. Parsed normally (unusual title is harmless since these are discarded by date cutoff). |
| Single-line speaker with role | 5037 | `Professor Aidan Byrne, UQ Provost` — name and role on a single line with no separate affiliation. Positional fallback extracts the full line as `speaker`; `affiliation` is `None`. This is acceptable. |
| Cancelled before date | 15575 | "Cancelled" marker appears before the date/time line |
| Cancelled after date | (verify in devtools) | "Cancelled" marker appears after the date/time line. **Note:** Sessions 13400 and 13082 were initially thought to be examples, but the markdown-extracted content shows "Cancelled" appearing *before* the date in both cases — the rendered visual order may differ from DOM order. The implementer **must** verify in browser devtools whether a true "Cancelled after date" DOM variant exists on the live page. If no such variant exists, the parser still handles it (text-content-based detection is position-independent), but test fixtures should reflect the actual DOM ordering rather than a hypothetical one. |

**Cross-page format note:** The main page and session page may use different speaker/affiliation formats for the same event. For example, session 17460 uses the unlabelled format on the main page but the labelled format (`Speaker:` / `Affiliation:`) on its session page. Parsers handle both formats independently at each stage.

**Affiliation formatting note:** Affiliations on the live site contain inconsistent formatting (e.g. `"INFN Padua,and Padua University, Italy"` with a missing space after the comma). The parser stores affiliations as-is without cleanup — these are display strings, not structured data.

---

## Implementation Prerequisite: DOM Verification

Before implementing any parser, the implementer **must** verify the actual CSS class names and DOM nesting on the live page. The class names in this spec (e.g. `views-row`, `view-content`, `tabs-panel`, sidebar wrapper classes) are based on observations from March 2026 and may have changed.

**Verification script:** Write a short Python script that:
1. Fetches the main page HTML (raw bytes) using `requests`.
2. Parses with `BeautifulSoup(html, "lxml")`.
3. Locates the tab links ("Upcoming sessions" / "Past sessions") and prints their parent elements' tag names and class attributes.
4. Follows the `href` fragment to the tab panel and prints the panel's tag name and class attributes.
5. Within the first tab panel, prints the tag name and class attributes of the first 3 entry block containers (the elements that repeat per session).
6. Within the first entry block, prints the tag name and class attributes of each direct child element, alongside a truncated version of their text content (first 60 characters).
7. Fetches one session page (e.g. session 17611) and prints:
   - The main content area wrapper's tag name and class attributes.
   - The sidebar wrapper's tag name and class attributes.
   - The venue heading's tag name and level.
   - The abstract heading's tag name and level.
8. Within the first entry block of the main page, prints whether speaker/affiliation elements are direct children of the entry block or nested inside a wrapper `<div>`. This is critical for the positional fallback — if they are nested, the "direct child" strategy needs to recurse into that wrapper.

Use this output to confirm or correct the following CSS class names before proceeding with implementation:
- `views-row` (entry block container)
- `view-content` (entry list container within each tab)
- `tabs-panel` (tab content panel)
- Sidebar wrapper class (spec guesses `"sidebar"` — may be `"region-sidebar"`, `"aside"`, etc.)
- Main content wrapper class (spec guesses `"node-content"` — may differ)
- `date-display-single` (date element class)
- Whether speaker/affiliation lines are direct children of the entry block or nested in a wrapper

If any class name differs from this spec, update the spec before implementing the parsers. The text-first extraction strategy (see Parsing Strategy) minimises dependence on class names, but the scoping selectors are critical for correctness.

---

## Features

### Feature 1: Event Data Model (`src/models.py`)

The `Event` dataclass as defined above. No external dependencies.

**Complete dataclass definition:**

```python
from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Event:
    # --- Required fields (no defaults — must be provided at construction) ---
    session_id: str
    series: str
    title: str
    date: date
    session_url: str

    # --- Optional/defaulted fields ---
    cancelled: bool = False
    time_unconfirmed: bool = False
    start: datetime | None = None       # tz-aware (Australia/Brisbane) or None
    end: datetime | None = None         # tz-aware (Australia/Brisbane) or None
    speaker: str | None = None
    affiliation: str | None = None
    venue: str | None = None
    abstract: str | None = None
    enriched: bool = False
    warnings: tuple[str, ...] = ()
```

**Notes on the dataclass:**
- The class is `frozen=True` to enforce immutability. Pipeline stages create new `Event` instances using `dataclasses.replace()` rather than mutating existing ones. For example: `new_event = replace(old_event, venue="Physics Annexe (06), Room: 407", warnings=(*old_event.warnings, "new warning"))`.
- Required fields (no defaults) are listed first, followed by optional fields with defaults. This ordering is required by Python's dataclass rules and also allows positional construction in tests if desired.
- The `warnings` field uses `tuple[str, ...]` with a default of `()`. Unlike `list`, a tuple cannot be mutated in place (no `.append()`), so accidental `event.warnings.append("oops")` will raise `AttributeError` at runtime rather than silently corrupting a previous pipeline stage's output. New warnings are accumulated via tuple concatenation: `warnings=(*old_event.warnings, "new warning")`.

**Input:** n/a (definition only)
**Output:** n/a

---

### Feature 2: Main Page Parser (`src/parsers.py :: parse_main_page`)

Parses the main listing page HTML and extracts session entries from both the "Upcoming sessions" and "Past sessions" tabs.

**Input:** HTML bytes (raw `response.content`), series name string, `reference_date: date | None = None` (defaults to `date.today()` when `None`).
**Output:** List of `Event` objects with baseline fields populated (`session_id`, `series`, `title`, `date`, `start`, `end`, `speaker`, `affiliation`, `session_url`, `cancelled`, `time_unconfirmed`). Enrichment fields (`venue`, `abstract`) are `None`. `enriched` is `False`. `warnings` may contain entries if fields were missing or defaulted. Only events within the `PAST_EVENT_CUTOFF_DAYS` window are returned — the date cutoff is applied internally by the parser before returning results. **All event counts reported in logs and returned to callers are post-deduplication, post-cutoff counts.**

#### Expected HTML structure (main page)

The main page uses Foundation-styled tabs to render two server-side tabs. Both tabs' content is present in the initial HTML response (no JavaScript required). The approximate DOM structure:

```
<!-- Tab navigation -->
<ul class="tabs" data-tabs id="...">
  <li class="tabs-title is-active">
    <a href="#qt-event_page_sessions-foundation-tabs-1">Upcoming sessions</a>
  </li>
  <li class="tabs-title">
    <a href="#qt-event_page_sessions-foundation-tabs-2">Past sessions</a>
  </li>
</ul>

<!-- Tab 1: Upcoming sessions -->
<div class="tabs-panel is-active" id="qt-event_page_sessions-foundation-tabs-1">
  <div class="view-content">
    <!-- Repeating entry blocks (one per session): -->
    <div class="views-row">
      <div class="views-field views-field-field-session-image">
        <img ... />                                         <!-- Thumbnail (ignore) -->
      </div>
      <div class="views-field views-field-title">
        <h3><a href="/event/session/17611">Title</a></h3>  <!-- Title + session URL -->
      </div>
      <div class="views-field views-field-field-event-session-date">
        <span class="date-display-single">
          23 March 2026 11:00am–12:00pm                     <!-- Date/time text -->
        </span>
      </div>
      <!-- "Cancelled" marker, when present, appears as a separate element
           within the views-row. Position varies: it may appear BEFORE or
           AFTER the date field. Detect by text content, not position. -->
      <div class="views-field views-field-field-event-session-series">
        in <a href="...">Physics colloquium</a>             <!-- Series link (skip) -->
      </div>
      <!-- Speaker/affiliation lines — see Parsing Strategy: Known HTML Variants -->
    </div>
    <!-- ... more views-row entries ... -->
  </div>
</div>

<!-- Tab 2: Past sessions -->
<div class="tabs-panel" id="qt-event_page_sessions-foundation-tabs-2">
  <div class="view-content">
    <!-- Same views-row entry structure as Tab 1 -->
  </div>
</div>
```

**⚠️ DOM verification required:** The CSS class names above (`views-row`, `views-field-title`, `views-field-field-event-session-date`, `views-field-field-event-session-series`, `views-field-field-session-image`, `view-content`, `tabs-panel`, `tabs-title`) and their nesting hierarchy must be verified against the live page using browser devtools before implementation. The structure above is based on observations from March 2026 and may have changed. In particular, verify:
- That `views-row` is the correct class for entry blocks and that it is a direct child of `view-content`.
- Whether speaker/affiliation elements use the same `views-field` wrapper class or are bare text nodes within the `views-row`.
- Whether the labelled and unlabelled speaker formats use different DOM element types (e.g. `<div>` vs bare text) or the same element with different text content.

**Important:** The tab content panel IDs (`qt-event_page_sessions-foundation-tabs-1` for Upcoming, `qt-event_page_sessions-foundation-tabs-2` for Past) are used for distinguishing upcoming from past entries. However, because these IDs could change, the parser should **also** support locating tabs by the text content of their tab links ("Upcoming sessions" / "Past sessions") as a fallback. The recommended strategy:

1. Find tab link elements whose text matches "Upcoming sessions" / "Past sessions".
2. Extract the `href` fragment (e.g. `#qt-event_page_sessions-foundation-tabs-1`).
3. Use that fragment to locate the corresponding content panel by `id`.

This two-step approach is resilient to ID renames while still using the DOM structure to scope entries correctly.

**Tab navigation failure diagnostic:** If the parser cannot locate either tab link (by text content matching "Upcoming sessions" or "Past sessions"), it should log an ERROR: `"Could not locate tab navigation — expected links with text 'Upcoming sessions' and 'Past sessions'. Possible page template change."` and return an empty list. This check serves as the main page equivalent of the session page's soft-404 detection (Feature 3). It is distinct from finding the tabs but finding zero entries within them (which produces a different diagnostic — see below).

**Note on "Upcoming" tab contents:** UQ's definition of "upcoming" does not correspond strictly to `date >= today`. On the live page, events whose date has passed may remain in the "Upcoming sessions" tab for several weeks (observed: ~6 weeks as of March 2026, with entries from early February still listed as "upcoming", representing approximately 5–6 past entries in the Upcoming tab). The parser does not need to care about this distinction — it extracts events from both tabs and applies the `PAST_EVENT_CUTOFF_DAYS` filter uniformly. The deduplication logic (prefer the upcoming-tab copy) handles any overlap.

**Note on CSS selectors vs text-based selectors:** The DOM class names above (e.g. `views-field-title`, `views-field-field-event-session-date`) are provided as a reference for locating elements. See Parsing Strategy for the general rule: text patterns are primary selectors for labelled data, CSS classes are for scoping only. For structural data without text labels (title link, session URL), structural selectors are acceptable as the primary mechanism.

**Scoping:** The parser must restrict its extraction to the `views-row` entry blocks within each tab's `view-content` container. Content outside these blocks — such as the "Contacts" section above the tabs (which includes a staff name, e.g. `Karen Kheruntsyan`, and email address) and the series description text — must not be parsed. A staff name in the Contacts section could otherwise be mistaken for a speaker by the positional fallback.

**Parsing rules:**
- Events are extracted from both the "Upcoming sessions" tab and the "Past sessions" tab, identified by following the tab link `href` anchors to their corresponding content panels.
- Past sessions with `date < reference_date - timedelta(days=PAST_EVENT_CUTOFF_DAYS)` are discarded. An event exactly `PAST_EVENT_CUTOFF_DAYS` old (i.e., `date == reference_date - timedelta(days=PAST_EVENT_CUTOFF_DAYS)`) is **kept**. Note: the past tab on the live page contains the **entire history** (back to 2017, ~100+ entries). The parser extracts all of them and then applies the cutoff filter, discarding those outside the window. At DEBUG log level, this will produce many "discarded" messages — this is expected.
- **Deduplication:** After extracting events from both tabs, duplicate `session_id` values are removed. An event right at the upcoming/past boundary may appear in both tabs simultaneously — and because the Upcoming tab retains events for weeks past their date, this overlap can be large. If a `session_id` appears in both, the upcoming-tab copy is kept and the past-tab copy is discarded. No field-level merging is attempted between duplicate entries — the entire Event object from the upcoming tab replaces the past-tab copy, even if the past-tab copy hypothetically had a field that the upcoming-tab copy lacks. This prevents duplicate UIDs in the `.ics` output. **Note on data freshness:** This dedup strategy assumes the Upcoming tab is at least as current as the Past tab. If UQ were to update the Past-tab copy with new information (e.g. adding a speaker) while the Upcoming-tab copy remained stale, the stale copy would win. In practice, UQ appears to update both tabs simultaneously, so this is not a concern.
- `session_id` is extracted from the session URL path using the `SESSION_URL_PATTERN` regex from `src/constants.py` (e.g. `17611` from `/event/session/17611`). If the URL does not match the expected pattern, the event is skipped entirely and an error is logged.
- `session_url` is constructed using `urllib.parse.urljoin(BASE_URL, href)`. This correctly handles both relative paths (`/event/session/17611`) and absolute URLs (`https://smp.uq.edu.au/event/session/17611`) that the CMS might emit.
- `date` is always parsed. If no date can be extracted, the event is skipped entirely (with a logged error).
- Start/end times are parsed from date/time strings using the regex and approach described in Parsing Strategy: Date/time parsing. Whatever time the main page states is used as-is. Timezone is `Australia/Brisbane`. If the time string contains a range separator (en-dash `\u2013`, em-dash `\u2014`, or hyphen-minus `-`), both start and end are parsed. If only a single time is present with no range, `start` is set to that time and `end` is set to `start + 1 hour`. `time_unconfirmed` is set to `True` (the end time is assumed), and a warning is appended noting the assumed duration.
- If the main page provides a date but no parseable start/end time (a save-the-date entry), `start` and `end` are left as `None` at this stage. `time_unconfirmed` is set to `True` provisionally, and a warning is appended. The session enricher (Feature 3) may later resolve the time from the session page and clear `time_unconfirmed`. If enrichment is skipped or fails, the orchestrator applies defaults (see Feature 7, step 3d).
- `cancelled` is detected using the approach described in Parsing Strategy: Cancelled event detection.
- `title` may be `"TBA"`.
- If a `views-row` contains no title link (`<a>` within a heading element), the entry is skipped entirely and a WARNING is logged: `"Entry block with no title link found — skipping."`.

**Speaker/affiliation parsing rules:**

The UQ website uses several different formats for speaker and affiliation information (see Parsing Strategy: Known HTML Variants). The main page parser uses the following strategy, applied in order of priority:

1. **Labelled format (primary):** Look for text beginning with `"Speaker:"` or `"Speakers:"` (case-insensitive) and strip the label using the regex approach from Parsing Strategy: Label stripping. Similarly for `"Affiliation:"` or `"Affiliations:"`. This handles both singular and plural forms. Example: `Speakers: Judy-Anne Osborne and Amelia Dickenson-Jones` → `speaker = "Judy-Anne Osborne and Amelia Dickenson-Jones"` (everything after stripping the `"Speakers: "` prefix).

2. **Multi-speaker `Name: Institution` detection:** If no labelled speaker/affiliation is found, and there are two or more candidate lines (after filtering, see step 3) where each line contains a colon that is *not* at the start and *not* part of a known label pattern, attempt to parse each line as `Name: Institution`. Split each line on the first colon. Concatenate the name parts into `speaker` (joined with `" and "`) and the institution parts into `affiliation` (joined with `" and "`). A warning is appended noting the multi-speaker format was detected.

   **Known label patterns to exclude from step 2:** Lines starting with any of the following (case-insensitive) are **not** candidates for `Name: Institution` parsing and should be skipped: `Speaker:`, `Speakers:`, `Affiliation:`, `Affiliations:`, `Abstract:`, `Room:`. This prevents a labelled line that failed step 1 due to a minor DOM variation (e.g. the label in a nested `<span>` affecting `get_text()` boundaries) from being misinterpreted as a `Name: Institution` pair. If, after excluding these, fewer than two candidate lines with colons remain, step 2 does not match and falls through to step 3.

   **Live reference case:** Session 16840 uses this format: `Judy-Anne Osborne: CARMA (and previously Monash)` / `Amelia Dickenson-Jones: St John's College Woodlawn`. This step produces `speaker = "Judy-Anne Osborne and Amelia Dickenson-Jones"` and `affiliation = "CARMA (and previously Monash) and St John's College Woodlawn"`.

3. **Positional fallback:** If neither the labelled format nor the multi-speaker `Name: Institution` format matched, fall back to positional extraction. A candidate text line is the `.get_text(strip=True)` (after whitespace normalization) of each direct child element of the `views-row` block, after filtering out elements that are: the image element, the title heading element, the date/time element, a known skip element (see below), or elements with empty text after normalization. The first remaining candidate line is treated as the speaker name; the next candidate line is treated as the affiliation. A warning is appended when the fallback path is used, noting which format was detected. **⚠️ DOM verification required:** Verify in devtools whether speaker/affiliation elements are direct children of `views-row` or nested deeper. If they are nested within a wrapper `<div>`, the positional extraction should iterate over that wrapper's children instead. Specifically: if the `views-row` has a child `<div>` that is not the image, title, date, or series element, check that div's children for candidate lines before falling back to direct children of `views-row`.

4. **Known elements to skip during positional extraction:** The following are recognized as non-speaker content and skipped by the positional fallback:
   - The series link element (identified by containing a link whose text matches the series name, e.g. any element containing text like `"Physics colloquium"` within a link). This generalizes across series rather than matching literal text like `"in Physics colloquium"`.
   - `"Abstract:"` / `"Abstract"` text — and **all sibling elements following** an element whose text starts with `"Abstract:"` within the same entry block. This prevents both the label and any inline abstract paragraph text from being consumed by the positional fallback. (See session 16952 where a full abstract paragraph follows the `Abstract:` label on the main page.)
   - Any line matching the date/time pattern.
   - The "Cancelled" marker text.

5. **No candidates found:** If no candidate lines remain after filtering, both `speaker` and `affiliation` are set to `None`, warnings are appended, and the event is still included.

Either or both may be `None` if not present. Missing values produce a warning but do not skip the event.

**Note on unlabelled speaker prevalence:** On the live page as of March 2026, roughly half of the past entries within the 365-day window use the unlabelled (positional) format. The implementer should weight test coverage accordingly — this is not a rare fallback path.

**Abstract on the main page:** The main page parser does not extract abstracts. If abstract text appears inline on the main page (as happens for some entries, e.g. session 16952 where `Abstract:` followed by a full paragraph appears in the listing), it is ignored at the main page parsing stage. The `"Abstract:"` label and all subsequent siblings within the entry block are included in the known-elements skip list to prevent the positional speaker/affiliation fallback from consuming them. The abstract is picked up during session page enrichment as normal.

**Pagination:** The parser assumes both tabs deliver all events in a single HTML response. If the main page introduces pagination (e.g. "Load more" buttons or page query parameters), events beyond the first page will be missed. The canary test (Feature 10) provides indirect detection — a sudden drop in past event count would trigger the plausible-event-count warning. A more direct check: if the parser detects a pagination control element (e.g. a "next page" link or a `pager-next` element within the session listing area), it should log a WARNING: `"Pagination controls detected — only first page of results extracted. Pipeline may be missing events."`. No attempt is made to follow pagination links; this would require a spec revision.

**Diagnostic logging for structural failures:** If the parser finds zero entry blocks in a tab, it should log a specific message: `"Found 0 entry blocks in the {tab_name} tab — possible template change"`. This is distinct from the validator's "empty event list" FAIL and helps diagnose whether the issue is a CSS class rename vs genuinely empty content.

**Entry block detection fallback:** If zero `views-row` elements are found within a tab's `view-content` container, the parser should attempt a structural fallback: within the tab panel, find all elements that contain a descendant `<a>` whose `href` matches `SESSION_URL_PATTERN`. The nearest common ancestor of each such link and its sibling date text is treated as the entry block. If this fallback is used, a WARNING is logged: `"views-row class not found — using structural fallback for entry detection. Verify DOM structure."` This fallback is less precise (it may capture extra elements) but prevents a total parser failure on a class rename.

---

### Feature 3: Session Page Parser (`src/parsers.py :: parse_session_page`)

Parses an individual session page HTML to extract supplementary data. Cross-checks the session page date against the main page date. Resolves missing fields when the main page lacked them.

**Input:** HTML bytes (raw `response.content`), existing `Event` object (from main page parser).
**Output:** A new `Event` object (copy of the input with updated fields and appended warnings — never mutated). With `venue` and `abstract` populated if available. `enriched` set to `True` if successful. On date mismatch: returns a new Event copied from the input with the warning appended and `enriched = False`; no enrichment fields are populated.

**Scope of enrichment:** The session page parser enriches the following fields:
- `venue` — always (session page is the only source).
- `abstract` — always (session page is the only source).
- `start` / `end` / `time_unconfirmed` — when the main page lacked a time entirely (`start is None`).
- `end` / `time_unconfirmed` — when `time_unconfirmed is True` because the main page provided only a start time with no range (end was assumed as `start + 1 hour`). If the session page provides a full time range whose start matches the main page start: use the session page's end time and clear `time_unconfirmed` to `False`. If the session page's start differs from the main page start: keep the main page values unchanged and append a warning noting the discrepancy.
- `speaker` / `affiliation` — only when the main page left them as `None`.

This follows the principle: "authoritative means the main page wins when there is a conflict, not that it preempts data it never provided." The main page never provided the end time in the single-time case — it was assumed by the parser. The session page's actual end time is therefore not a conflict.

**Note on cross-page format differences:** The main page and session page may use different speaker/affiliation formats for the same event. For example, session 17460 uses the unlabelled (positional) format on the main page but the labelled format (`Speaker:` / `Affiliation:`) on its session page. The session page parser applies the same labelled-first, positional-fallback strategy independently of whatever format the main page used.

#### Expected HTML structure (session page)

The session page has two distinct regions: a **main content area** and a **sidebar**. The parser must scope its extraction correctly across both.

```
<!-- Main content area -->
<div class="node-content" or similar content wrapper>
  <a href="/event/99/physics-colloquium">Physics colloquium</a>  <!-- Breadcrumb -->

  <h1 class="page-title">Talk Title</h1>                         <!-- Title -->

  <div class="field field-event-session-date">
    <span class="date-display-single">
      23 March 2026 11:00am–12:00pm                               <!-- Date/time -->
    </span>
  </div>

  <!-- Speaker/affiliation — same labelled or unlabelled formats as main page -->
  <div>Speaker: Professor James Annett</div>
  <div>Affiliation: University of Bristol</div>

  <!-- Optional preamble text (e.g. italic intro paragraph) — EXCLUDE from abstract -->

  <h2>Abstract</h2>                                               <!-- Abstract heading -->
  <p>Abstract text paragraph 1...</p>
  <p>Abstract text paragraph 2...</p>

  <!-- Optional empty heading (CMS artifact, e.g. <h3></h3>) — skip -->

  <!-- Optional biography section — EXCLUDE from abstract -->
  <h2>Biography</h2>
  <p>Bio text...</p>
  <img src="..." />                                               <!-- Speaker photo may appear here -->

  <h3>About Physics colloquium</h3>                               <!-- Boilerplate — EXCLUDE -->
  <p>The Physics Colloquium series hosts...</p>
</div>

<!-- Sidebar -->
<div class="sidebar" or similar sidebar wrapper>
  <h3>Venue</h3>                                                  <!-- Venue heading -->
  <div class="field field-event-session-venue">
    <p>Physics Annexe (06)<br/>Room: 407</p>                      <!-- Venue text -->
  </div>

  <h4>Other upcoming sessions</h4>                                <!-- IGNORE entirely -->
  <div>
    <!-- Session cards with titles, dates, links — not part of this event -->
  </div>
</div>
```

**⚠️ DOM verification required:** The following structural claims must be verified against the live page using browser devtools before implementation:
- The **sidebar wrapper** class name (shown as `"sidebar"` above — the actual class may differ, e.g. `"region-sidebar"`, `"aside"`, etc.). This is critical for scoping venue extraction and excluding "Other upcoming sessions" data.
- The **main content wrapper** class name (shown as `"node-content"` above — may be different).
- The **venue heading level** (`<h3>` shown above — verify whether it is consistently `<h3>` or varies). Also verify the venue field wrapper class `"field field-event-session-venue"`.
- Whether the **venue text** uses `<br/>` to separate building and room, or uses separate elements. Session 17460 is known to split `Room:` and the room number across separate elements — verify the DOM structure for this case.
- The **Abstract heading level** (`<h2>` shown above). Verify this is consistent across sessions. The abstract extraction stop condition depends on heading level comparison.
- The **"About Physics colloquium" heading level** (`<h3>` shown above). The prefix-match approach ("About ") is resilient to exact wording changes, but the heading level relative to the Abstract heading matters for the stop condition.
- Whether the session page shows any **cancellation indicator** for events marked cancelled on the main page. The `cancelled` field is always determined by the main page and is never overridden, but this should be documented based on observation.

**Structural validity check (soft-404 detection):** Before attempting field extraction, the session page parser should verify that the page contains the minimum expected structural elements: a title heading and a date field. If neither is present, the page is likely a soft-404 (CMS returning a generic page with HTTP 200). In this case, log a warning (e.g. `"Session 17611: session page appears to be a soft-404 — no title or date found"`), set `enriched = False`, and return a copy of the original event with the warning appended. This prevents silent enrichment failures where the fetcher reports success but the page content is useless.

**Content scoping rules:**

The parser applies different scoping rules to different fields:

- **Event data extraction** (title, date, speaker, affiliation, abstract): restricted to the **main content area** only. The sidebar must be excluded to prevent "Other upcoming sessions" entries from being mistaken for the current event's data.
- **Venue extraction**: reads from the **sidebar venue block** specifically (identified by the "Venue" heading or the venue field wrapper). This is the only source for venue data.
- **All other sidebar content** (e.g. "Other upcoming sessions" cards): ignored entirely.

**Cross-check rules:**
- The session page's date is compared against the `Event`'s `date` from the main page.
- If the dates match: venue and abstract are added, `enriched` is set to `True`.
- If the dates disagree: the main page's data is kept, enrichment data is discarded, a warning is appended (e.g. `"Session 17611: main page date is 2026-03-23, session page date is 2026-03-24 — enrichment discarded"`), `enriched` remains `False`.
- If start/end times are present on both pages and differ but dates match: the main page times are kept (main page is authoritative for data it has), enrichment data (venue, abstract) is still used, and a warning is appended noting the time discrepancy.
- **Title cross-check (warn-only):** If the session page title differs from the main page title (after whitespace normalization), a warning is appended (e.g. `"Session 17611: title mismatch — main page: 'TBA', session page: 'Time Reversal Symmetry Breaking...'"`). Enrichment is **not** discarded on title mismatch alone — titles legitimately change (e.g. `"TBA"` → real title) and the main page is authoritative for the title field. The warning provides a signal for cases where the session page may be serving stale or mismatched content.
- **Known limitation — same-date false-positive cross-check:** If two events share the same date and the CMS serves the wrong session page for one of them, the date cross-check will pass (dates match) and the title cross-check will emit a warning but not block enrichment. This could result in a session receiving the wrong venue/abstract. In practice, this requires a CMS routing bug on a date-collision, which is extremely unlikely. The title mismatch warning provides an audit trail.

**Time resolution when main page lacked a time entirely (`start is None`):**
- "Main page is authoritative" applies only to data the main page actually has. If the main page explicitly lacked a time, the session page is consulted.
- If the session page has a parseable start/end time and the dates match: use the session page time, clear `time_unconfirmed` to `False`, and add venue/abstract as normal. `enriched` is set to `True`.
- If the session page also lacks a parseable time: leave `start` and `end` as `None`, leave `time_unconfirmed=True`, and append a warning. Defaults are **not** applied here — they are applied by the orchestrator's post-enrichment fixup (Feature 7, step 3d), which runs regardless of whether enrichment succeeded. Venue/abstract are still added if available.

**End-time resolution when main page had a start but assumed the end (`time_unconfirmed is True` and `start is not None`):**
- The main page provided a start time but no range separator, so the end was assumed as `start + 1 hour`. The main page never actually provided the end time — the parser guessed it.
- If the session page has a full time range (start and end both parseable), and the session page's start time matches the main page's start time: use the session page's end time, clear `time_unconfirmed` to `False`, and append an informational log (e.g. `"Session 4340: end time resolved from session page: 1:00pm"`).
- If the session page has a full time range but the session page's start time differs from the main page's start: keep the main page values unchanged (the discrepancy suggests the session page may have stale data), append a warning noting the start time mismatch, and leave `time_unconfirmed = True`.
- If the session page also lacks a time or has only a single time with no range: keep the main page values unchanged, leave `time_unconfirmed = True`.

**Speaker/affiliation resolution when main page lacked them:**
- If the main page's `speaker` is `None` and the session page has a parseable speaker (using the same labelled/positional strategy as the main page parser), use the session page's speaker. Similarly for `affiliation`.
- If the main page already has a `speaker` or `affiliation`, the session page's values are ignored for those fields (main page is authoritative for data it has).
- A warning is appended when speaker/affiliation is resolved from the session page (e.g. `"Session 17092: speaker resolved from session page: Dr. Doug Johnstone"`).
- **Live reference case:** Session 17092 ("What the Variability of Embedded Protostars...") has no speaker on the main page as of March 2026, but its session page lists `Dr. Doug Johnstone` / `National Research Council Canada - Herzberg Astronomy and Astrophysics Research Centre`. This is a concrete example of this code path in action.

**Abstract extraction:** The parser extracts text under the `"Abstract"` heading on the session page. The extraction uses `get_text(separator="\n")` on each paragraph element, which strips all inline HTML formatting (bold, italic, superscript, subscript) and produces plain text. This means chemical formulae like `Sr<sub>2</sub>RuO<sub>4</sub>` become `Sr2RuO4` — the subscript information is lost. This is an acceptable trade-off: `.ics` `DESCRIPTION` fields are plain text, and attempting to preserve formatting (e.g. via Unicode subscript characters) adds complexity for marginal benefit.

The extraction iterates through **element siblings** after the Abstract heading. Specifically, use `abstract_heading.find_next_siblings()` or filter `abstract_heading.next_siblings` to `Tag` objects only — do not process `NavigableString` nodes (bare text between elements), which are typically just whitespace in the DOM. For each **element** encountered, the following checks are applied **in this priority order**:

1. If it is a heading element with **no text** after whitespace normalization: **skip it** (do not stop, do not include). Continue to the next sibling. This handles CMS artifact empty headings (confirmed present on session 17092 as an empty `<h3>`).
2. If it is a **non-empty heading** whose text starts with `"About "` (prefix match, to catch `"About Physics colloquium"` and similar series-specific boilerplate): **stop**. Exclude this heading and all subsequent content.
3. If it is a **non-empty heading** whose text matches `"Biography"` (case-insensitive): **stop**. Exclude this heading and all subsequent content.
4. If it is a **non-empty heading** at a level equal to or higher (i.e., `<h1>` or `<h2>` if Abstract was `<h2>`) than the Abstract heading: **stop**. Exclude this heading and all subsequent content.
5. If the end of the main content area is reached: **stop**.
6. Otherwise: extract the element's text content and append to the abstract.

**Critical: the empty-heading check (step 1) must be evaluated before the heading-level check (step 4).** An empty `<h3>` between the abstract and the "About" section must be skipped, not treated as a stop condition.

Content between the speaker/affiliation block and the Abstract heading (such as italicized preamble text, e.g. `"This is a joint Maths and Physics colloquium..."`) is also excluded from the abstract.

**Abstract placeholder normalization:** If the extracted abstract text is only a placeholder (e.g. `"TBA"`, `"tba"`, `"To be announced"`, or similar), `abstract` is set to `None`. A bare placeholder adds no value to the calendar event's DESCRIPTION field.

**Venue extraction:** The parser extracts the venue block from the sidebar and normalizes it into a single string. Whitespace and newlines within the venue block are collapsed. The target format is `"Building Name (Code), Room: NNN"`. When the building name and room are on separate lines (e.g. `Parnell Building (07)<br/>Room: 222`, or when `Room:` and the number are split across elements as on session 17460: `Room:` then `222` separately), they are joined with `, ` as separator and the room label is rejoined as `Room: NNN`. If no room number is present, the venue string is just the building name. The resulting string maps directly to the ICS `LOCATION` property. Venue strings may include additional location information beyond building and room (e.g. Zoom links: `"Forgan Smith Building (01), Room: E302 and via Zoom (https://...)"`). The parser preserves this additional text as-is rather than attempting to parse or strip it.

**Missing venue section:** Some session pages may not have a "Venue" heading or venue field at all. This is not an error — `venue` is set to `None`, and `enriched` is still set to `True` if the page was otherwise structurally valid (date cross-check passed). The absence of a venue section is distinct from an enrichment failure.

**Cancelled events on session pages:** The session page may not display any cancellation indicator even for events marked as cancelled on the main page. The `cancelled` field is always determined by the main page and is never overridden by the session page.

**Text handling:** BeautifulSoup decodes HTML entities and normalises Unicode. The parser applies whitespace normalization (see Parsing Strategy) and then preserves the decoded text as-is (including accented characters, em-dashes, smart quotes). No additional encoding or escaping is done — that is the responsibility of the ICS generator (Feature 4).

**Main page is authoritative for data it has.** Session pages never create events. Session pages never override fields that the main page provided (with the specific exception of the assumed end time — see "End-time resolution" above, where the main page provided a start but the end was a parser assumption, not actual data). Session pages fill in fields the main page left empty (time, speaker, affiliation) and add fields only available from session pages (venue, abstract). A session page that contradicts the main page date has its enrichment discarded with a warning. "Authoritative" means the main page wins when there is a conflict, not that it preempts data it never provided.

---

### Feature 4: ICS Generator (`src/calendar.py`)

Produces a valid `.ics` calendar string from a list of events.

**Function signature:**

```python
def generate_ics(events: list[Event], calendar_name: str, dtstamp: datetime) -> bytes:
```

**Input:** List of `Event` objects, calendar name string (the `"name"` field from the series config, e.g. `"Physics colloquium"` — used for `X-WR-CALNAME`), and a timezone-aware UTC `datetime` for `DTSTAMP` (the pipeline execution start time, truncated to the minute, passed in by the orchestrator).
**Output:** UTF-8 encoded `.ics` content as `bytes`. The `icalendar` library's `cal.to_ical()` method returns `bytes` (UTF-8 encoded). The generator returns this directly.

**Precondition:** All events must have non-`None` `start` and `end` by the time they reach the ICS generator. The orchestrator's post-enrichment fixup (Feature 7, step 3d) guarantees this by applying defaults to any event where `start` is still `None`. If the ICS generator receives an event with `start=None`, it is a pipeline bug — the generator should raise a `ValueError` rather than produce invalid output.

**ICS mapping:**

| Event field | ICS property |
|---|---|
| `session_id` + `date` | `UID` (e.g. `17611-20260323@uq-seminar-calendar`) |
| `title` (with prefixes/suffixes, see below) | `SUMMARY` |
| `start` | `DTSTART` (with `TZID=Australia/Brisbane`) |
| `end` | `DTEND` (with `TZID=Australia/Brisbane`) |
| `speaker` + `affiliation` | Appended to `SUMMARY` when available |
| `venue` | `LOCATION` (omitted if `None`) |
| `abstract` + `session_url` | `DESCRIPTION` (see formatting below) |
| `cancelled` | `STATUS:CANCELLED` (in addition to `[CANCELLED]` prefix on summary) |
| `dtstamp` parameter | `DTSTAMP` (see DTSTAMP section below) |

**SUMMARY construction:** The summary degrades gracefully based on available data:
- Full: `"[CANCELLED] Title — Speaker (Affiliation) [TIME TBC]"`
- No affiliation: `"Title — Speaker"`
- No speaker: `"Title"`
- `[CANCELLED]` prefix applied only when `cancelled = True`.
- `[TIME TBC]` suffix applied only when `time_unconfirmed = True` **and** `cancelled = False`. A cancelled event suppresses the `[TIME TBC]` suffix — subscribers don't need to know the tentative time of a talk that isn't happening.

**DESCRIPTION formatting:**

Because `session_url` is a required `str` field (always present for any event that passes parsing), the DESCRIPTION always contains at least the session URL. The formatting cases are:

- When abstract is available: abstract text, followed by two newlines (`\n\n` in ICS wire format), followed by the session URL.
- When abstract is `None`: session URL alone.
- `DESCRIPTION` is never omitted — it always contains the session URL as a link back to the source.

Newlines within the abstract (paragraph breaks) are preserved as ICS `\n` sequences. The `icalendar` library handles the required escaping of commas, semicolons, and backslashes in text properties.

**DTSTAMP handling:**
- RFC 5545 requires `DTSTAMP` on every `VEVENT`. This is the datetime the calendar object instance was created.
- `DTSTAMP` is set to the `dtstamp` parameter passed in by the orchestrator — a single fixed UTC value for all events in a run (the pipeline execution start time, truncated to the minute).
- Because `DTSTAMP` changes on every run regardless of content changes, the CI workflow must use a content-aware comparison that ignores `DTSTAMP` lines when deciding whether to commit (see Feature 11).

**⚠️ DTSTAMP line folding:** The CI content-aware diff (Feature 12) strips `DTSTAMP` lines from the output before comparison. The unit test (Feature 8) verifies that `DTSTAMP` lines are not folded by the `icalendar` library (i.e., the full `DTSTAMP:...` value appears on a single line). **If this test fails** with the resolved `icalendar` version (indicating the library does fold `DTSTAMP` lines), the CI diff must use the Python-based unfolding approach (Feature 12, step 6) rather than the simpler `grep -v` fallback. Check this during initial testing and choose the appropriate CI diff strategy accordingly.

**VTIMEZONE:**
- The `.ics` file must include a `VTIMEZONE` component for `Australia/Brisbane`. Some clients (notably Outlook) do not resolve `TZID` references without a matching `VTIMEZONE` in the file.
- Brisbane does not observe daylight saving time, so the `VTIMEZONE` has a single `STANDARD` component with a fixed UTC offset of `+10:00` and no `DAYLIGHT` component.
- The `STANDARD` sub-component must include the following RFC 5545-required properties:
  - `DTSTART:19700101T000000`
  - `TZOFFSETFROM:+1000`
  - `TZOFFSETTO:+1000`
  - `TZNAME:AEST`
- The `VTIMEZONE` is emitted once at the top of the calendar, before any `VEVENT` components.
- Implementation note: the `icalendar` library does not auto-generate `VTIMEZONE` blocks. The generator must construct and add it explicitly. Construct a `icalendar.Timezone()` component, add a `icalendar.TimezoneStandard()` sub-component with the properties listed above, and add the timezone to the calendar before the VEVENTs.

**Reference implementation for VTIMEZONE construction:**

```python
from datetime import datetime, timedelta
from icalendar import Calendar, Timezone, TimezoneStandard

def build_brisbane_vtimezone() -> Timezone:
    tz = Timezone()
    tz.add("TZID", "Australia/Brisbane")

    std = TimezoneStandard()
    std.add("DTSTART", datetime(1970, 1, 1, 0, 0, 0))  # naive datetime (local time)
    std.add("TZOFFSETFROM", timedelta(hours=10))         # timedelta, not string
    std.add("TZOFFSETTO", timedelta(hours=10))           # timedelta, not string
    std.add("TZNAME", "AEST")

    tz.add_component(std)
    return tz

# Usage:
cal = Calendar()
cal.add_component(build_brisbane_vtimezone())
# ... then add VEVENTs ...
```

Note: `TZOFFSETFROM` and `TZOFFSETTO` must be `timedelta` objects, not strings. The `icalendar` library serializes them as `+1000`. The `DTSTART` in the `TimezoneStandard` sub-component must be a naive `datetime` (no tzinfo), representing a local time.

**Unicode and escaping:**
- Speaker names, titles, and abstracts may contain accented characters, em-dashes, smart quotes, and other non-ASCII text. The ICS generator receives these as decoded Python strings (from BeautifulSoup via the parsers).
- The `icalendar` library handles RFC 5545 text escaping (backslash-escaping of commas, semicolons, backslashes, and newlines in `SUMMARY`, `DESCRIPTION`, and `LOCATION`).
- The output `.ics` file is encoded as UTF-8. Since `cal.to_ical()` returns UTF-8 bytes, write them directly in binary mode (`'wb'`).
- Test coverage must include events with non-ASCII characters in speaker names, titles, and abstracts.

**SEQUENCE and the TBA-to-real-title problem:** All events are emitted with `SEQUENCE:0`. Because the pipeline is stateless and has no way to detect whether a UID's content has changed since the last run, incrementing `SEQUENCE` is not possible. This is a known limitation — see Operator Runbook for troubleshooting stale events. **Impact on subscribers:** The most common real-world update for this calendar is a title changing from `"TBA"` to a real title. The UID stays the same (same session ID, same date), but some calendar clients (particularly Outlook) may not display the updated title without a `SEQUENCE` increment. Google Calendar and Apple Calendar generally handle this correctly. If this becomes a significant user complaint, the stateless design could be extended with a lightweight state file that tracks `{UID: content_hash}` to enable `SEQUENCE` incrementing — but this is deferred as a future extension unless demand warrants it.

**UID design:** `{session_id}-{date YYYYMMDD}@uq-seminar-calendar`. Uses the `date` field (always present) rather than `start` (which may have been defaulted). The date is included so that a rescheduled talk (same session ID, different date) appears as a new event rather than silently moving. The old UID disappears from the file, so subscribers' calendars remove the old entry and add the new one. **Rescheduling race condition:** During the brief window between when UQ updates the website and when the CI pipeline runs, a rescheduled talk might appear with both the old and new dates on the website simultaneously (e.g. old date in Past tab, new date in Upcoming tab). The deduplication logic (by `session_id`, preferring the upcoming copy) means only the new-date entry survives. The old-date UID will vanish from the `.ics` file, and subscribers' calendars will remove it. This is the desired behavior. Note: same-day events (two different talks on the same date with different session IDs) produce distinct UIDs and are handled correctly.

**Cross-series UID note:** The UID format does not include the series name. If two different series share the same session ID (e.g. a cross-listed talk), UIDs would collide across separate `.ics` files. Each `.ics` file is independent so this has no effect on subscribers. However, if the "combined calendar" future extension (see Future Extensions) is implemented, series must be incorporated into the UID to avoid collisions.

**Additional ICS properties:**
- `PRODID` from `src/constants.py`
- `X-WR-CALNAME:{series name}`
- `CALSCALE:GREGORIAN`
- `METHOD:PUBLISH`

**Line folding:** The `icalendar` library handles `.ics` line length requirements automatically.

---

### Feature 5: Fetcher (`src/fetcher.py`)

The only module with network I/O. All other modules are pure functions.

**Interface:**

```python
def fetch_page(url: str) -> bytes | None
```

**Input:** A URL string.
**Output:** Raw HTML bytes (`response.content`) on success, `None` on failure (timeout, HTTP error, network error).

**Behaviour:**
- Timeout: `REQUEST_TIMEOUT` seconds per request (from `src/constants.py`).
- Retries: Up to `MAX_RETRIES` additional attempts after the initial failure (so 2 total attempts when `MAX_RETRIES = 1`), with 2-second backoff. **Transient failures** are: HTTP 5xx responses, HTTP 429 (Too Many Requests), `requests.Timeout`, and `requests.ConnectionError`. **Non-transient failures** (not retried): HTTP 4xx other than 429, `requests.TooManyRedirects`, `requests.exceptions.SSLError`. SSL errors are not retried because certificate issues are unlikely to resolve within seconds; a specific warning is logged mentioning the SSL error so the operator can investigate. On HTTP 429, if a `Retry-After` header is present, the fetcher uses `max(2, int(retry_after_value))` as the backoff delay (capped at 60 seconds to prevent indefinite blocking). If `Retry-After` is a date string rather than an integer, fall back to the 2-second default.
- Rate limiting: `REQUEST_DELAY`-second delay between consecutive requests (politeness to UQ servers). This delay is enforced globally — including across series boundaries when multiple series are processed in a single run. The fetcher maintains a module-level `_last_request_time` variable to enforce the global delay. Before each request, it sleeps for `max(0, REQUEST_DELAY - (now - _last_request_time))`. This is the only mutable state in the fetcher module (aside from the `requests.Session` instance).
- **Test isolation:** The fetcher exposes a `_reset_rate_limiter()` function (prefixed with underscore to signal internal use) that resets `_last_request_time` to `None`. The live canary test's setup should call this to avoid test-order-dependent delays. The stub fetcher used in unit tests bypasses the rate limiter entirely since it doesn't call `fetch_page`.
- User-Agent: `USER_AGENT` from `src/constants.py`.
- URL validation: only fetches URLs under `ALLOWED_DOMAIN` (from `src/constants.py`). The check is: the URL's hostname equals `ALLOWED_DOMAIN` or ends with `.{ALLOWED_DOMAIN}`. Any other domain returns `None` and logs a warning. This check applies to the initial URL only, not the final resolved URL after redirects.
- **Redirect policy:** Redirects within `*.uq.edu.au` (note: broader than `ALLOWED_DOMAIN`) are followed normally. Redirects to domains outside `*.uq.edu.au` cause the request to be treated as a failure (return `None`, log a warning including the redirect target URL). This broader scope is intentional: UQ may redirect between subdomains (e.g. `smp.uq.edu.au` → `events.uq.edu.au`) and these should be followed, but redirects to external domains should not. To make this explicit, the redirect domain check uses `ALLOWED_REDIRECT_DOMAIN` from `src/constants.py`.
- **Logging on failure:** The fetcher logs the HTTP status code (if available) and redirect target URL (if a redirect occurred) on failure. This is critical for diagnosing permanent URL changes (e.g. a 301 redirect to a new URL structure).
- **Response handling:** The fetcher returns `response.content` (raw bytes). This lets BeautifulSoup handle encoding detection (see Parsing Strategy: Character encoding).
- Returns `None` on any failure. The caller (orchestrator) decides how to handle it.

**Dependency injection:** The orchestrator accepts a fetch function as a parameter. In production it's `fetch_page`. In tests it's a stub that returns fixture HTML.

```python
from typing import Protocol

class Fetcher(Protocol):
    def __call__(self, url: str) -> bytes | None: ...
```

The `Fetcher` Protocol is defined in `src/fetcher.py` alongside the concrete `fetch_page` function. The orchestrator imports it from there.

**Connection reuse:** The fetcher should use a module-level `requests.Session()` instance for all HTTP requests. This provides TCP connection reuse and fewer TLS handshakes across the ~40–50 requests to the same host per run, without adding complexity. The session object is the second piece of module-level mutable state (alongside `_last_request_time`). It should be created lazily on first use or at module import.

---

### Feature 6: Runtime Validator (`src/validator.py`)

Validates the final list of events before writing the `.ics` file. Acts as a safety net against parser breakage or website changes. All checks operate on the **post-cutoff, post-deduplication** event list (i.e., the list that would be written to the `.ics` file).

**Input:** List of `Event` objects, `reference_date: date | None = None` (defaults to `date.today()` when `None`).
**Output:** A validation result: pass/fail boolean plus a list of violation descriptions.

**Invariants checked:**

| Check | Severity | Description |
|---|---|---|
| Non-empty event list | FAIL | At least one event must exist. A zero-event result almost certainly means the parser broke. With past events included in the output, this should never legitimately be zero unless the series is brand new. |
| No upcoming events | WARN | Zero events with `date >= reference_date` is expected during semester breaks. Not a failure — the calendar will still contain recent past events. |
| All events have non-empty titles | FAIL | A title that failed to parse indicates structural HTML changes. |
| All events have a valid `date` | FAIL | `date` is required for every event. (Defense-in-depth: the main page parser already skips events without a date, so this check should never trigger in normal operation. It guards against bugs in the parser.) |
| All events have non-`None` `start` and `end` | FAIL | After the orchestrator's post-enrichment fixup, every event must have a start and end time. `None` at this point indicates a pipeline bug. |
| Unconfirmed times | WARN | Any non-cancelled event with `time_unconfirmed = True` is noted. Cancelled events are excluded from this check since the `[TIME TBC]` suffix is suppressed for them anyway (see Feature 4) — there is no user-visible consequence of an unconfirmed time on a cancelled event. |
| No dates too far in the past | WARN | Events with `date < reference_date - timedelta(days=PAST_EVENT_CUTOFF_DAYS)` should have been filtered during parsing. If any slip through, that is a logic error worth flagging. |
| No dates in the far future | WARN | Events more than `MAX_FUTURE_DAYS` (from `src/constants.py`) days out from `reference_date` are suspicious. |
| No duplicate UIDs | FAIL | Would cause undefined calendar behaviour. The main page parser deduplicates by `session_id` (see Feature 2), so this check acts as a safety net. |
| Plausible event count | WARN | Fewer than `MIN_PLAUSIBLE_EVENTS` or more than `MAX_PLAUSIBLE_EVENTS` (from `src/constants.py`) events is unusual. Note: these thresholds apply to the post-cutoff list. A new series with less than a full year of history may legitimately have fewer than `MIN_PLAUSIBLE_EVENTS` events — the operator should adjust the threshold or suppress the warning for new series. |
| All session URLs are valid | FAIL | Must match the `SESSION_URL_PATTERN` from `src/constants.py`. (Defense-in-depth: the parser already skips events with invalid URLs.) |
| Low enrichment rate | WARN | If fewer than `MIN_ENRICHMENT_RATE` (strictly less than) of events have `enriched = True`, the session page template may have changed. Not a failure (events still have baseline data), but worth flagging for investigation. This check is skipped if fewer than 5 events are present (too few for a meaningful rate). **Note:** During initial development and testing, this warning may fire frequently due to network conditions or running with a small event subset. This is not cause for alarm — it is most meaningful in production runs with the full event list. |

**FAIL-severity violations cause the orchestrator to abort and keep the existing `.ics`. WARN-severity violations are logged but do not prevent output.**

---

### Feature 7: Orchestrator (`src/main.py`)

The pipeline entrypoint. Composes all other features.

**Input:** Configuration (list of series definitions), `reference_date: date | None = None` (defaults to `date.today()` when `None`), optional injected fetcher.
**Output:** `.ics` files written to disk (or kept unchanged on validation failure). Exit code 0 if all series succeeded, 1 if any series failed.

**Invocation:** The orchestrator is invoked as `python -m src.main`. The module must include an `if __name__ == "__main__":` block that parses CLI arguments and calls the main pipeline function. No `[project.scripts]` entrypoint is defined in `pyproject.toml`.

**CLI flags:**
- `--dry-run`: Run the full pipeline (fetch, parse, enrich, validate, generate ICS) but do not write any files. Log what would be written. Useful for debugging.
- `--log-level LEVEL`: Override the default `LOG_LEVEL` (equivalent to the `LOG_LEVEL` environment variable).

**Multi-series behaviour:**

The orchestrator processes each series in `SERIES` sequentially. Each series is independent: a failure in one series does not prevent other series from being processed. The orchestrator tracks per-series success/failure and:

- Writes the `.ics` output for each series that passes validation.
- Logs errors for each series that fails (fetch failure, validation failure).
- After all series are processed: exits 0 if every series succeeded, exits 1 if any series failed.

This means the CI workflow may commit partial updates (some `.ics` files updated, others unchanged). The commit message indicates which series succeeded and which failed.

**DTSTAMP capture:** The orchestrator captures a single UTC timestamp at the very start of the `main()` function, truncated to the minute: `dtstamp = datetime.now(timezone.utc).replace(second=0, microsecond=0)`. This value is passed to all `generate_ics()` calls across all series, ensuring every event in every series shares the same `DTSTAMP` for a given run.

**Pipeline (per series):**

```
1. Fetch main page
   └─ If fetch fails → log error (including HTTP status if available), mark series as failed, skip to next series

2. Parse main page → list of baseline Events (upcoming + past within cutoff)
   (pass reference_date through to parser)
   (parser applies the date cutoff internally — only events within the cutoff window are returned)
   (parser deduplicates by session_id across tabs)

3. For each Event (only those returned from step 2, i.e. already filtered by date cutoff):
   a. Fetch session page (with REQUEST_DELAY between requests — enforced globally, not per-series)
   b. If fetch succeeds → parse session page, cross-check, enrich (including speaker/affiliation fill-in
      and end-time resolution when time_unconfirmed)
   c. If fetch fails → keep baseline Event, log at DEBUG level.
      Note: the fetcher itself also logs at WARNING/ERROR for HTTP failures.
      This dual logging is intentional — the fetcher's log is about the HTTP
      failure; the orchestrator's DEBUG is about the pipeline decision to skip
      enrichment.
   d. POST-ENRICHMENT FIXUP: If the event's `start` is still `None` after enrichment
      (whether enrichment succeeded, failed, or was skipped), apply defaults:
      set `start` to `date` + `DEFAULT_START_HOUR` (Australia/Brisbane),
      set `end` to `start` + `DEFAULT_DURATION_HOURS`,
      set `time_unconfirmed` to `True` (if not already),
      append a warning noting that defaults were applied.
      This guarantees every event has a non-None start/end before validation.

      **Fixup implementation:**

      ```python
      from dataclasses import replace
      from datetime import datetime, timedelta
      from zoneinfo import ZoneInfo

      BRISBANE = ZoneInfo("Australia/Brisbane")

      if event.start is None:
          default_start = datetime(
              event.date.year, event.date.month, event.date.day,
              DEFAULT_START_HOUR, 0, 0,
              tzinfo=BRISBANE
          )
          event = replace(event,
              start=default_start,
              end=default_start + timedelta(hours=DEFAULT_DURATION_HOURS),
              time_unconfirmed=True,
              warnings=(*event.warnings,
                        f"Session {event.session_id}: no time found on main page "
                        f"or session page — defaulted to "
                        f"{DEFAULT_START_HOUR}:00-"
                        f"{DEFAULT_START_HOUR + DEFAULT_DURATION_HOURS}:00")
          )
      ```

4. Run validator on final event list (pass reference_date through to validator)
   └─ If FAIL → log violations, mark series as failed, skip to next series
   └─ If WARN → log warnings, continue

5. Generate .ics content (pass the dtstamp captured at the start of main())

6. If --dry-run: log summary and skip to next series.
   If the output file already exists on disk, compare the generated ICS content
   (excluding DTSTAMP lines) against the existing file. Log
   `"DRY RUN: content would change for {series}"` or
   `"DRY RUN: no content change for {series}"`. If the output file does not
   exist, log `"DRY RUN: would create new file for {series}"`.
   (If validation failed in step 4, this step is not reached — the series was already
   marked as failed and skipped.)
   Otherwise: ensure OUTPUT_DIR exists (os.makedirs(OUTPUT_DIR, exist_ok=True)).
   Write .ics to temp file in the same directory as the output file
   (e.g. docs/.physics-colloquium.ics.tmp), then atomically replace
   the output file via os.replace().
   The temp file MUST be on the same filesystem as the target to
   ensure os.replace() is atomic.
   The `icalendar` library's `cal.to_ical()` returns `bytes` (UTF-8 encoded).
   Write these bytes directly in binary mode: `open(path, 'wb')`. Do not
   decode and re-encode.
   **Cleanup on failure:** The temp file write and `os.replace()` should be
   wrapped in a try/finally block that removes the temp file if the replacement
   fails (e.g. disk full, permissions error). Leftover `.tmp` files would
   otherwise accumulate in the output directory.

7. Log summary for this series: N events total (post-dedup, post-cutoff), M enriched, K warnings
```

**After all series:**

```
8. Log overall summary: which series succeeded, which failed.
   In addition to logging, the orchestrator prints a machine-readable summary
   to stdout (one line per series) for use by the CI workflow:
     UPDATED: Physics colloquium
     FAILED: Maths Seminar
   The CI workflow uses this output to construct commit messages.
   Note: use the series name as-is from the SERIES config (preserving original capitalization).

9. Exit 0 if all succeeded, exit 1 if any failed
```

**Fail-safe behaviour:** The existing `.ics` for a given series is only overwritten after the new one passes validation. If anything goes wrong at any stage, the old file remains intact. Subscribers' calendars keep showing the last known-good data.

**Logging:** The pipeline uses Python's `logging` module. The orchestrator configures a single stream handler writing to stderr with format `%(levelname)s %(name)s %(message)s`. Log level defaults to `LOG_LEVEL` (from `src/constants.py`) and can be overridden via the `--log-level` CLI flag or a `LOG_LEVEL` environment variable (CLI flag takes precedence). Module-level loggers use `__name__`. The level semantics:
- `ERROR`: events that cause a series to fail (fetch failure, validation FAIL).
- `WARNING`: validator WARNs and non-fatal parsing anomalies (fallback speaker format, assumed duration, pagination controls detected, zero entry blocks in a tab, possible soft-404 on session page).
- `INFO`: per-series summaries (N events total, M enriched, K warnings).
- `DEBUG`: per-event enrichment details (successful fetches, skipped enrichments, individual field parsing).

---

### Feature 8: Snapshot Unit Tests (`tests/test_parsers.py`, `tests/test_calendar.py`)

**Time control:** All tests that depend on the current date use `freezegun` to pin `date.today()` to a known value. The fixture HTML files contain events with dates relative to this pinned date. Additionally, `parse_main_page` and the validator accept a `reference_date` parameter, so tests pass the frozen date explicitly rather than relying solely on the mock. The `reference_date` parameter is the authoritative time-control mechanism; `freezegun` is defense-in-depth for any code path that accidentally calls `date.today()` directly.

Note: `freezegun` has documented quirks with `zoneinfo`. Brisbane's fixed +10:00 offset (no DST transitions) avoids the most common issues, but if `freezegun` causes problems with timezone-aware datetimes, rely on the `reference_date` parameter and consider removing `freezegun` in favour of parameter-only time control.

**Fixtures** (saved in `tests/fixtures/`):

Fixtures are minimal, hand-crafted HTML files that contain only the DOM structure needed for parsing — not full copies of the live page. Each fixture tests a specific parsing scenario. The implementer should create fixture files that match the test scenarios listed below; exact filenames are an implementation detail.

**Fixture date strategy:** All dates in fixture HTML files are defined relative to `REFERENCE_DATE` (2026-03-23). This date was chosen because session 17611 (a real upcoming event with full data) falls on this date, making it a natural boundary case for upcoming vs. past event logic. All fixture dates are defined relative to this date, ensuring tests pass regardless of when they are run. The recommended date assignments for fixture events:

| Fixture event purpose | Date expression | Concrete date |
|---|---|---|
| Upcoming event (near future) | `REFERENCE_DATE + 7 days` | 2026-03-30 |
| Upcoming event (same day) | `REFERENCE_DATE` | 2026-03-23 |
| Second same-day event (distinct session ID, different time) | `REFERENCE_DATE` | 2026-03-23 |
| Recent past event (within cutoff) | `REFERENCE_DATE - 30 days` | 2026-02-21 |
| Older past event (within cutoff) | `REFERENCE_DATE - 200 days` | 2025-09-05 |
| Past event beyond cutoff (should be discarded) | `REFERENCE_DATE - 400 days` | 2025-02-17 |
| Far future event (triggers validator WARN) | `REFERENCE_DATE + 400 days` | 2027-04-27 |

**Note on same-day fixtures:** The second same-day event must have a different session ID *and* a different time (e.g. the first at 11:00am–12:00pm and the second at 2:00pm–3:00pm) to exercise that the time-parsing logic works independently per entry and that UIDs are distinct.

**Note on fixture–cutoff coupling:** Fixture dates must fall within the `PAST_EVENT_CUTOFF_DAYS` window relative to `REFERENCE_DATE`, or they will be silently discarded by the parser before reaching the ICS generator or validator. If `PAST_EVENT_CUTOFF_DAYS` is changed (e.g. reduced to 180 for a new series), fixtures with dates at `REFERENCE_DATE - 200 days` would start being dropped. The `tests/fixtures/README.md` should document this dependency.

The `tests/fixtures/README.md` should document these conventions and the `REFERENCE_DATE` value so that future contributors can create consistent fixtures. It should also note that the Contacts section (staff name and email above the tabs) must be included in main page fixtures to test scoping — see the "Content outside views-row blocks" test scenario below.

**Main page parser test scenarios:**
- Extracts correct number of upcoming events.
- Extracts correct number of past events within the cutoff window.
- Discards past events older than `PAST_EVENT_CUTOFF_DAYS`.
- Deduplicates events appearing in both tabs by `session_id`, keeping the upcoming-tab copy.
- Correctly parses title, date, start/end time, speaker, affiliation for each event.
- Handles `"TBA"` titles.
- Parses any valid time (11am, 1pm, 2pm, 10:30am, etc.) as-is from the main page.
- Detects cancelled events regardless of whether the "Cancelled" marker appears before or after the date line.
- Detects cancelled events with case variations (`Cancelled`, `CANCELLED`, `cancelled`).
- When a date is present but no time: `start`/`end` are `None`, `time_unconfirmed` is `True`, warning is appended.
- When a single time is present with no range: `start` is set to that time, `end` is `start + 1 hour`, `time_unconfirmed` is `True`, warning is appended noting assumed end time.
- When speaker is missing: `speaker` is `None`, warning is appended, event is still included.
- When affiliation is missing: `affiliation` is `None`, warning is appended, event is still included.
- When both speaker and affiliation are missing (save-the-date entry): event is still included with warnings.
- When speaker and affiliation use the unlabelled format (no `Speaker:` / `Affiliation:` prefix): positional fallback extracts both correctly, warning is appended noting the fallback format.
- When using the positional fallback, the series link element (e.g. `in Physics colloquium`) is correctly skipped and not consumed as speaker or affiliation text.
- When speaker and affiliation use the plural labelled format (`Speakers:` / `Affiliations:`): labels are recognized and stripped correctly.
- When speaker and affiliation use the multi-speaker `Name: Institution` format (e.g. session 16840): both lines are parsed, names are concatenated into `speaker`, institutions into `affiliation`, and a warning is appended noting the format.
- When an inline `Abstract:` block is present on the main page: it is skipped by the positional speaker/affiliation parser (along with all subsequent sibling elements in the entry block) and ignored by the main page parser.
- When the session URL does not match the expected pattern: event is skipped, error is logged.
- Locates tabs by tab link text ("Upcoming sessions" / "Past sessions") even if tab panel IDs change.
- When tab panel IDs differ from the expected values (e.g. `#custom-tabs-1` instead of `#qt-event_page_sessions-foundation-tabs-1`): tabs are still located correctly via the tab link text fallback.
- Two events on the same date (different session IDs and different times): both are extracted with correct, distinct UIDs.
- Content outside `views-row` blocks (e.g. the "Contacts" section with a staff name and email above the tabs) is not mistaken for event data. The fixture must include a realistic Contacts section (e.g. `Karen Kheruntsyan` / `karen.kheruntsyan@uq.edu.au`) to test this.
- When a `views-row` contains no title link: the entry is skipped and a WARNING is logged.
- When pagination controls are detected within the tab content area (e.g. a `<li class='pager-next'><a href='?page=1'>Next</a></li>` element): a WARNING is logged noting pagination detection, and events from the current page are still extracted normally.

**Session page parser test scenarios:**
- Extracts venue and abstract from a normal session page.
- Normalizes multi-line venue blocks into `"Building Name (Code), Room: NNN"` format, including when `Room:` and the room number are split across separate elements (e.g. session 17460).
- Preserves venue strings that include Zoom links or other additional location information.
- Sets `enriched = True` on success.
- Handles missing abstract gracefully (venue still extracted, `enriched` still `True`).
- Handles missing venue section gracefully (no "Venue" heading present — `venue` is `None`, `enriched` still `True` if other enrichment succeeded).
- Detects date mismatch → discards enrichment, appends warning, `enriched` remains `False`.
- Title mismatch → appends warning, enrichment proceeds normally.
- When `start is None` (main page had no time) and session page has a parseable time: time is used, `time_unconfirmed` is cleared to `False`, `enriched` is `True`.
- When `start is None` and session page also lacks a time: `start`/`end` remain `None`, `time_unconfirmed` remains `True`, warning appended. (Defaults are applied later by orchestrator.)
- When main page provided start but assumed end (`time_unconfirmed is True`, `start is not None`) and session page has a full time range with matching start: end is updated from session page, `time_unconfirmed` cleared to `False`.
- When main page provided start but assumed end and session page has a full time range with a different start: main page values kept unchanged, `time_unconfirmed` remains `True`, warning appended.
- When main page `speaker` is `None` and session page has a speaker: speaker is filled in from session page, warning appended.
- When main page `speaker` is not `None`: session page speaker is ignored.
- When main page `speaker` was extracted via positional fallback and session page uses labelled format: main page speaker is kept (not overridden), no format-mismatch error.
- When a Biography section is present alongside an Abstract section: only the Abstract text is extracted; biography is excluded (including any inline images in the biography section).
- When preamble text (e.g. italic introductory paragraph) appears between speaker info and Abstract heading: it is excluded from the abstract.
- When abstract text is just `"TBA"`: `abstract` is set to `None`.
- When the event is cancelled on the main page but session page has no cancellation indicator: `cancelled` remains `True`.
- When an empty heading appears between the abstract and the next section: it is skipped and does not truncate abstract extraction.
- Abstract extraction stops at the "About Physics colloquium" boilerplate heading, excluding it and all subsequent text.
- Returned `Event` has a new `warnings` tuple; the input event's warnings are not mutated (tuple immutability guarantees this at the type level).
- Soft-404 detection: when the session page lacks expected structural elements (no title heading, no date field), `enriched` is `False` and a warning is appended.

**ICS generator test scenarios:**
- Produces valid `.ics` output (parseable by the `icalendar` library itself).
- `SUMMARY` format with all fields: `"Title — Speaker (Affiliation)"`.
- `SUMMARY` with missing affiliation: `"Title — Speaker"`.
- `SUMMARY` with missing speaker and affiliation: `"Title"`.
- `[CANCELLED]` prefix applied when `cancelled = True`.
- `[TIME TBC]` suffix applied when `time_unconfirmed = True` and `cancelled = False`.
- `[TIME TBC]` suffix suppressed when `time_unconfirmed = True` and `cancelled = True`.
- `STATUS:CANCELLED` set when `cancelled = True`.
- `LOCATION` omitted when `venue` is `None`.
- `DESCRIPTION` contains abstract and session URL separated by a blank line when abstract is available.
- `DESCRIPTION` contains only session URL when abstract is `None`.
- UIDs are stable, correctly formatted, and use `date` not `start`.
- Same-day events with different `session_id` values produce distinct UIDs.
- Timezone is `Australia/Brisbane` throughout.
- `VTIMEZONE` component is present with `STANDARD` sub-component, `DTSTART:19700101T000000`, `TZOFFSETFROM:+1000`, `TZOFFSETTO:+1000`, `TZNAME:AEST`, offset `+10:00`, and no `DAYLIGHT` sub-component.
- `DTSTAMP` is present on all events and uses the value passed in by the caller.
- `DTSTAMP` lines in the output are not folded (i.e. the full `DTSTAMP:...` value appears on a single line). This is a precondition for the CI workflow's simpler content-aware diff. **If this test fails with the resolved `icalendar` version**, the CI diff must use the Python-based unfolding approach (Feature 12, step 6) rather than the `grep -v` fallback. See Feature 4 for details.
- `SEQUENCE:0` is present on all events.
- Past events (within cutoff) appear in the output with correct UIDs.
- Events with non-ASCII characters in speaker name, title, and abstract are encoded correctly (UTF-8, with ICS text escaping applied).
- Newlines within abstracts are preserved as ICS `\n` sequences.
- Raises `ValueError` if any event has `start=None`.

---

### Feature 9: Validator Unit Tests (`tests/test_validator.py`)

All validator tests pass a fixed `reference_date` to avoid coupling to the real clock.

- Empty event list → FAIL.
- Event with empty title → FAIL.
- Event with missing `date` → FAIL.
- Event with `start=None` → FAIL.
- Non-cancelled event with `time_unconfirmed = True` → WARN.
- Cancelled event with `time_unconfirmed = True` → no WARN (excluded from this check).
- Zero upcoming events (`date >= reference_date`), but past events present → WARN (not FAIL).
- Event with date just beyond `PAST_EVENT_CUTOFF_DAYS` → WARN.
- Event with date `MAX_FUTURE_DAYS + 1` days in the future → WARN.
- Duplicate UIDs → FAIL.
- Valid, normal event list (mix of upcoming and recent past) → PASS.
- Event with invalid session URL → FAIL.
- Fewer than `MIN_PLAUSIBLE_EVENTS` events → WARN.
- More than `MAX_PLAUSIBLE_EVENTS` events → WARN.
- Low enrichment rate (e.g. 0 out of 10 events enriched) → WARN.
- Enrichment rate check skipped when fewer than 5 events (too few for meaningful rate).

---

### Feature 10: Integration Test (`tests/test_integration.py`)

A single happy-path integration test that wires the full pipeline together with fixture data. The integration test uses its own dedicated main page fixture (which may be a superset of unit test fixture content, or a separate file) and dedicated session page fixtures. Do not reuse unit test fixtures directly — the integration test needs a self-contained set of fixtures that exercises the complete pipeline path.

1. Stub fetcher returns a main page fixture (with a mix of upcoming and past entries, including one entry missing speaker, one entry missing time, one entry with start but no end-time range, two entries on the same date with different session IDs and different times, one entry using the multi-speaker `Name: Institution` format, and one cancelled entry) and session page fixtures for a curated subset (4–5 entries covering: normal enrichment, speaker resolution, time resolution from no-time event, end-time resolution from assumed-end event). Unmapped session URLs return `None`.
2. Runs the orchestrator pipeline: parse → enrich → post-enrichment fixup → validate → generate ICS.
3. Asserts: the generated `.ics` is parseable, contains the expected number of events, UIDs are correct and distinct (including for same-day events), enriched fields (venue, abstract) are present where expected, speaker resolved from session page where main page lacked it, multi-speaker entry has concatenated speaker/affiliation strings, end time resolved from session page where main page assumed it, default time applied to the event whose session page fetch failed, all events have non-`None` start/end, cancelled event has `STATUS:CANCELLED` and `[CANCELLED]` prefix.

This test catches integration bugs between pipeline stages that unit tests miss.

---

### Feature 11: Live Canary Test (`tests/test_live.py`)

Marked with `@pytest.mark.live`. Not run by default (`pytest` without flags skips it).

**Behaviour:**
1. Fetches the real main listing page.
2. Runs the main page parser (both tabs).
3. Selects a session page to fetch: the first *upcoming* event (by date, earliest first) whose title is not `"TBA"` and which is not cancelled. If no such upcoming event exists, falls back to the first *past* non-TBA non-cancelled event. If none exist, falls back to the first event in the list.
4. Fetches that session page.
5. Runs the session page parser.
6. Runs the validator on the result.
7. Asserts:
   - The validator passes (no FAIL-severity violations).
   - At least one event was extracted from the main page.
   - The enriched session page has `enriched = True`.
   - The enriched event has a non-`None` venue (provides targeted regression detection beyond what the validator's statistical thresholds catch on a single event).

**Note on time control:** The canary test uses the real `date.today()` (no `freezegun`, no fixed `reference_date`). This is intentional — the canary test validates that the live website works with the parser *today*, not at a frozen point in time. This is distinct from the unit tests, which all use `REFERENCE_DATE`.

**Purpose:** Detects when the website structure has changed and the parser needs updating. Run on a separate weekly CI schedule (see Feature 12) in addition to being available for manual invocation.

---

### Feature 12: GitHub Actions Workflow (`.github/workflows/update-calendar.yml`)

**Triggers:**
- Cron schedule: daily (`0 3 * * *` — 3am UTC / 1pm AEST).
- Manual dispatch (`workflow_dispatch`) for on-demand runs.

A separate workflow (`.github/workflows/canary.yml`) runs the live canary test on a weekly schedule (`0 4 * * 1` — Monday 4am UTC). This provides automated detection of UQ website structure changes independently of the main calendar update.

**Concurrency:** The workflow uses `concurrency: { group: calendar-update, cancel-in-progress: true }` to prevent race conditions when a manual `workflow_dispatch` fires while the daily cron is running (or vice versa). The in-progress run is cancelled in favour of the new one.

**Job timeout:** The workflow job sets `timeout-minutes: 30`. Under normal operation the pipeline completes in under 5 minutes, but if UQ's server becomes unresponsive and slow-drips bytes (defeating the per-request `REQUEST_TIMEOUT`), the job could hang indefinitely. The 30-minute ceiling prevents runaway CI minutes.

**Steps:**
1. Checkout repository.
2. Install `uv`.
3. `uv sync` (installs Python + dependencies from `uv.lock`).
4. Run snapshot unit tests (`uv run pytest -m "not live"`). If tests fail → abort.
5. Run orchestrator (`uv run python -m src.main`). Capture stdout to a file (e.g. `pipeline-summary.txt`) for use in commit message construction. **Important:** Capture stdout only (not stderr) — the orchestrator writes machine-readable output to stdout and log messages to stderr. Use `> pipeline-summary.txt` (not `2>&1`). Use `continue-on-error: true` on this step (with a step `id`, e.g. `id: run_pipeline`) so that partial successes (exit code 1 from a single series failure) do not prevent committing the successful series' output files.
6. **Content-aware diff:** Compare the new `.ics` files against the committed versions, ignoring `DTSTAMP` lines. If no content difference exists (only `DTSTAMP` changed), skip the commit for that file. **Recommended approach:** Use a small inline script that passes the filename safely via argument rather than shell interpolation:
   ```bash
   content_changed=false
   for ics_file in docs/*.ics; do
     if [ ! -f "$ics_file" ]; then continue; fi
     # Compare against the committed version, stripping DTSTAMP lines.
     # Uses Python to unfold ICS lines before stripping, for robustness.
     if ! python3 -c "
   import sys, re, subprocess
   f = sys.argv[1]
   def strip_dtstamp(text):
       # Unfold ICS continuation lines (RFC 5545: CRLF + space/tab)
       text = re.sub(r'\r?\n[ \t]', '', text)
       # Normalize line endings before comparison
       text = text.replace('\r\n', '\n').replace('\r', '\n')
       return '\n'.join(l for l in text.splitlines() if not l.startswith('DTSTAMP'))
   try:
       old = subprocess.run(['git', 'show', f'HEAD:{f}'], capture_output=True, text=True)
       if old.returncode != 0: sys.exit(1)  # file is new
       if strip_dtstamp(old.stdout) != strip_dtstamp(open(f).read()):
           sys.exit(1)
   except Exception:
       sys.exit(1)
   " "$ics_file" 2>/dev/null; then
       content_changed=true
       git add "$ics_file"
     fi
   done
   ```
   **Fallback (simpler but fragile):** If the DTSTAMP-not-folded unit test (Feature 8) passes with the resolved `icalendar` version, the simpler `grep -v '^DTSTAMP:'` approach also works. The risk is that a future `icalendar` library update could change folding behavior, causing every CI run to show a false "content changed" result. The Python-based unfolding approach above is robust against this.
7. If any `.ics` files have real content changes:
   - Configure git identity (`git config user.name "github-actions[bot]"` and `git config user.email "github-actions[bot]@users.noreply.github.com"`).
   - Construct the commit message from the orchestrator's stdout summary (captured in step 5). Example: `"Update calendars: Physics colloquium updated"` or `"Update calendars: Physics colloquium updated, Maths Seminar FAILED"`.
   - Commit and push the updated `.ics` files to the `main` branch.
8. Check the orchestrator's exit code from step 5. With `continue-on-error: true`, the original result is available via `steps.run_pipeline.outcome` (not `steps.run_pipeline.conclusion`, which is always `success` when `continue-on-error` is set). **Note:** The `outcome` vs `conclusion` distinction is well-defined in the GitHub Actions docs but is a common source of CI bugs — verify this works in an actual workflow run. If `outcome` is `failure`, fail the workflow step (after committing any successful output) so that GitHub sends a notification email:
   ```yaml
   - name: Fail workflow if orchestrator had errors
     if: steps.run_pipeline.outcome == 'failure'
     run: exit 1
   ```

**GitHub Pages** is configured to serve from the `docs/` directory on the `main` branch. This is a natively supported GitHub Pages source and requires no separate deploy action. The `.ics` URLs will be:

```
https://{username}.github.io/{repo}/physics-colloquium.ics
```

The `docs/` directory also contains a static `index.html` (see Feature 13) served as the landing page.

Output files are written to `docs/` rather than `output/`.

**Note on CDN caching:** GitHub Pages uses a Fastly CDN that typically caches with `max-age=600` (10 minutes). Subscribers may not see the latest `.ics` content immediately after the CI workflow commits. For a daily-updated seminar calendar this is acceptable, but operators should be aware of this lag when debugging "stale calendar" reports.

**Security:**
- Uses the built-in `GITHUB_TOKEN` for commits. No personal tokens or secrets.
- All GitHub Actions referenced by commit SHA, not mutable tags. A `dependabot.yml` configuration is included to keep action SHAs updated for security patches.
- Python version pinned to `3.12` in the workflow (passed to `astral-sh/setup-uv` or used with `uv python install 3.12`). `uv` version also pinned explicitly in the `astral-sh/setup-uv` action's `version` input.

**Required GitHub Actions (pinned by SHA):**
- `actions/checkout`
- `astral-sh/setup-uv`

No other third-party actions are required. The commit-and-push logic uses shell commands with the built-in `GITHUB_TOKEN`.

---

### Feature 13: Index Page (`docs/index.html`)

A minimal static HTML page served as the GitHub Pages landing page. Provides:

- Project name and one-line description.
- Links to each `.ics` file with subscription instructions for Google Calendar, Apple Calendar, and Outlook.
- A note about refresh frequency (Google: ~12–24 hours; Apple: configurable; Outlook: varies).
- A note that events older than one year are automatically removed from the calendar feed.
- A note about worst-case update latency: if UQ updates the website shortly after the daily CI run (1pm AEST), it could be up to ~35 hours before the change appears in a subscriber's calendar (next CI run + CDN cache + Google Calendar refresh). For same-day cancellations, subscribers should also check the UQ website directly.

This file is maintained manually (not auto-generated). When a new series is added to `SERIES`, a corresponding link should be added to `index.html`.

---

## Folder Structure

```
uq-seminar-calendar/
├── src/
│   ├── __init__.py
│   ├── constants.py          # Shared constants (see below)
│   ├── models.py             # Event dataclass
│   ├── parsers.py            # Main page + session page parsers
│   ├── calendar.py           # ICS generator
│   ├── fetcher.py            # HTTP layer (only network-touching module) + Fetcher Protocol
│   ├── validator.py          # Runtime validation
│   └── main.py               # Orchestrator / pipeline entrypoint (includes __main__ block)
├── tests/
│   ├── fixtures/             # Minimal hand-crafted HTML fixtures (one per test scenario)
│   │   └── README.md         # Documents fixture creation conventions
│   ├── conftest.py           # Shared fixtures: frozen date, stub fetcher, event factory
│   ├── test_parsers.py
│   ├── test_calendar.py
│   ├── test_validator.py
│   ├── test_integration.py   # Full pipeline integration test with fixture data
│   └── test_live.py          # Canary test (marked @pytest.mark.live)
├── docs/
│   ├── index.html            # Landing page with subscription instructions
│   ├── .nojekyll             # Prevents GitHub Pages Jekyll processing (ensures files starting
│   │                         # with underscores or dots are served correctly; do not delete)
│   └── .gitkeep              # Ensures directory exists before first .ics is generated
├── .github/
│   ├── workflows/
│   │   ├── update-calendar.yml   # Daily calendar update
│   │   └── canary.yml            # Weekly live canary test
│   └── dependabot.yml            # Keeps GitHub Actions SHAs updated
├── .gitignore                # See below
├── pyproject.toml            # Project metadata, dependencies, pytest config
├── uv.lock                   # Locked dependency versions (generated by `uv lock`, committed)
├── README.md
└── LICENSE                   # MIT
```

### `README.md` content

The README should include:

1. **Project name and description:** One-paragraph summary of what the project does (scrapes UQ SMP seminar pages, generates subscribable `.ics` calendar feeds, publishes via GitHub Pages).
2. **Subscribe:** Links to each published `.ics` URL and brief instructions for Google Calendar, Apple Calendar, and Outlook (can link to `docs/index.html` for full details).
3. **How it works:** Brief description of the pipeline (fetch → parse → enrich → validate → generate ICS → publish). Mention the daily CI schedule.
4. **Development setup:** `uv sync` to install dependencies, `uv run python -m src.main` to run locally, `uv run python -m src.main --dry-run` for a test run without writing files. Note: a full run takes ~30–60 seconds due to rate limiting (~40–50 session page fetches at 0.5s delay each). Use `--dry-run` to test parsing logic without writing files.
5. **Running tests:** `uv run pytest` for unit tests, `uv run pytest -m live` for the live canary test.
6. **Adding a new series:** Reference the Operator Runbook section or repeat the 5-step process.
7. **License:** MIT.

### `.gitignore`

```
__pycache__/
*.pyc
.pytest_cache/
.venv/
*.tmp
dist/
*.egg-info/
.python-version
```

### `src/constants.py` specification

Shared constants used across multiple modules. Centralises magic numbers and configuration values that would otherwise be scattered or duplicated. `SERIES` configuration remains in `main.py` since it is the orchestrator's concern.

```python
# src/constants.py

import re

# --- Scraping ---
REQUEST_TIMEOUT: int = 15              # seconds per request
REQUEST_DELAY: float = 0.5            # seconds between consecutive fetches (global)
MAX_RETRIES: int = 1                  # additional attempts after initial failure (2 total)
USER_AGENT: str = "UQ-Seminar-Calendar/1.0 (github.com/OWNER/REPO)"  # TODO: replace before deployment
ALLOWED_DOMAIN: str = "smp.uq.edu.au"
ALLOWED_REDIRECT_DOMAIN: str = "uq.edu.au"  # broader scope for following redirects
BASE_URL: str = "https://smp.uq.edu.au"

# --- URL patterns ---
SESSION_URL_PATTERN: re.Pattern = re.compile(r"/event/session/(\d+)")

# --- Calendar generation ---
# An event is within the cutoff window if:
#   event.date >= reference_date - timedelta(days=PAST_EVENT_CUTOFF_DAYS)
# An event exactly PAST_EVENT_CUTOFF_DAYS old IS included.
PAST_EVENT_CUTOFF_DAYS: int = 365     # include past events within this window
PRODID: str = "-//UQ Seminar Calendar//EN"
UID_DOMAIN: str = "uq-seminar-calendar"
TIMEZONE: str = "Australia/Brisbane"
UTC_OFFSET: str = "+10:00"
DEFAULT_START_HOUR: int = 11          # for time_unconfirmed events
DEFAULT_DURATION_HOURS: int = 1

# --- Validator thresholds ---
MIN_PLAUSIBLE_EVENTS: int = 10        # with 365-day lookback, fewer than 10 is suspicious
MAX_PLAUSIBLE_EVENTS: int = 100       # Live page has ~45-50 events within the cutoff window
                                       # (as of March 2026). Note: the Past tab contains the
                                       # entire history (100+ entries back to 2017), but the
                                       # cutoff filter reduces this to ~45-50 before validation.
                                       # If UQ runs weekly colloquia across both semesters
                                       # (~40 weeks × 1/week = ~40 events/year), this has
                                       # ample headroom. Revisit if multiple-per-week
                                       # scheduling becomes common.
MIN_ENRICHMENT_RATE: float = 0.5      # fraction (0.0–1.0); check skipped if <5 events
MAX_FUTURE_DAYS: int = 365

# --- Output ---
OUTPUT_DIR: str = "docs"
LOG_LEVEL: str = "INFO"               # override via LOG_LEVEL env var or --log-level CLI flag
```

### `pyproject.toml` specification

In addition to project metadata, include the following:

```toml
[project]
name = "uq-seminar-calendar"
version = "1.0.0"
description = "Scrapes UQ SMP seminar pages and generates .ics calendar files"
requires-python = ">=3.12"
dependencies = [
    "beautifulsoup4>=4.12,<5",
    "lxml>=5.0,<6",
    "requests>=2.31,<3",
    "icalendar>=5.0,<7",
]

[dependency-groups]
dev = [
    "pytest>=7.0,<9",
    "freezegun>=1.2,<2",
]

[tool.pytest.ini_options]
markers = [
    "live: marks tests that hit the live UQ website (deselect with '-m \"not live\"')",
]
```

**Version bounds rationale:** `icalendar` is pinned with a ceiling because the CI content-aware diff (Feature 12) depends on DTSTAMP lines not being folded by the library. A major version bump could change line-folding behavior. The `uv.lock` file provides exact reproducibility; the bounds here prevent `uv lock` from accidentally pulling a breaking release. **Note:** If someone runs `uv lock --upgrade`, this could silently change the `icalendar` version within the bound. The DTSTAMP-not-folded unit test (Feature 8) serves as a guard — it will fail if the new version changes folding behavior, prompting the CI diff strategy to be updated.

**`uv.lock` bootstrapping:** After creating `pyproject.toml`, run `uv lock` to generate `uv.lock`. This file is committed to the repository and should be regenerated (via `uv lock`) whenever dependencies in `pyproject.toml` change. CI uses `uv sync` which installs from the lockfile, ensuring reproducible builds. **Initial setup note:** The `uv.lock` file must exist before the first CI run. After cloning the repository and creating `pyproject.toml`, run `uv lock` locally and commit the resulting `uv.lock` before pushing. Without this, `uv sync` in CI will fail.

### `.github/dependabot.yml`

```yaml
version: 2
updates:
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
```

### `conftest.py` specification

The shared test configuration provides:

- **Frozen reference date:** A module-level constant `REFERENCE_DATE = date(2026, 3, 23)`. This date was chosen because session 17611 (a real upcoming event with full data) falls on this date, making it a natural boundary case for upcoming vs. past event logic. All fixture dates are defined relative to this date. `REFERENCE_DATE` is fixed and does not need to be updated as time passes — it is used only in unit tests (never in production). The specific date was chosen based on the live page state in March 2026 and remains valid indefinitely as a test reference point. **The canary test (Feature 11) does not use `REFERENCE_DATE`** — it uses the real date by design.
- **Stub fetcher:** A class implementing the `Fetcher` protocol that maps URLs to fixture file contents (as `bytes`). Returns `None` for unmapped URLs.
- **Sample event factory:** A helper function `make_event(**overrides)` that returns `Event` objects with sensible defaults, allowing individual fields to be overridden via keyword arguments. The factory derives `start` and `end` from `date` to ensure temporal consistency. The full set of defaults:

```python
def make_event(**overrides) -> Event:
    """Factory with complete defaults. Override any field by keyword."""
    from zoneinfo import ZoneInfo
    BRISBANE = ZoneInfo("Australia/Brisbane")
    
    d = overrides.get("date", REFERENCE_DATE + timedelta(days=7))
    defaults = {
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
```

  Pass `start=None, end=None` explicitly to create an event with no time (pre-fixup state). Pass `warnings=("existing warning",)` to test warning accumulation. Each call creates a new `defaults` dict, so returned events never share state. **Note:** If `date` is overridden, `start` and `end` are automatically derived from the new date unless they are also explicitly overridden.

---

## Configuration

Series definitions are kept in `src/main.py` as a module-level constant. All other constants are in `src/constants.py` (see Folder Structure section for the full listing).

```python
# src/main.py

SERIES = [
    {
        "name": "Physics colloquium",
        "url": "https://smp.uq.edu.au/event/99/physics-colloquium",
        "output": "physics-colloquium.ics",
    },
    # Future series added here.
    # If a new series uses a different HTML template, add a "parser" key
    # (e.g. "parser": "maths_seminar") and implement the corresponding
    # parser function. The default is the standard SMP tab-based parser.
]
```

**Series naming convention:** The `name` field should match the website's own capitalization. As of March 2026, the website uses "Physics colloquium" (lowercase "c"). This value propagates into `X-WR-CALNAME`, the `SUMMARY` format's series attribution (if added), logging output, and the CI commit message.

---

## Patterns and Principles

### Stateless snapshots
Each run generates the `.ics` from scratch based on the current website. No database, no state file, no diffing against previous runs. Events removed from the website disappear from the calendar. Events added appear. This is the simplest correct approach.

### Main page is authoritative for data it has
The main listing page defines which events exist, when they are, and who is speaking. Session pages add venue and abstract, and fill in fields the main page left empty (time, speaker, affiliation). Session pages also resolve assumed end times when the main page provided only a start with no range (see Feature 3, "End-time resolution"). Session pages never create events, never override fields that the main page explicitly provided. A session page that contradicts the main page date has its enrichment discarded with a warning. "Authoritative" means the main page wins when there is a conflict, not that it preempts data it never provided.

### Graceful degradation
If a session page fetch fails or contradicts the main page, the event still appears in the calendar with baseline information. No single enrichment failure affects other events.

### Fail-safe output
The existing `.ics` is only replaced after the new one passes runtime validation. A broken parser, a down website, or corrupt data never results in publishing an empty or invalid calendar. In multi-series runs, each series is independent: a failure in one does not prevent successful series from updating.

### Dependency injection
The orchestrator accepts a fetcher function as a parameter. Production uses the real HTTP fetcher. Tests use stubs that return fixture HTML. No monkeypatching or mocking libraries required.

### Immutable data flow
Each pipeline stage takes data in and returns new data out. No shared mutable state between stages. The `Event` dataclass is `frozen=True` with `tuple`-based warnings to enforce this at the type level.

### Separation of I/O and logic
Only `fetcher.py` touches the network. Only `main.py` touches the filesystem. All other modules are pure functions that take data in and return data out. This makes everything except the fetcher and orchestrator trivially testable. The one exception: the fetcher maintains module-level mutable state for rate limiting (`_last_request_time`) and connection reuse (`requests.Session`). This is strictly I/O-layer state and does not leak into the logic modules.

### Tab content is server-rendered
Both the "Upcoming sessions" and "Past sessions" tabs render their content in the initial HTML response (server-side rendered). The parser does not depend on JavaScript execution. If this changes (e.g. UQ switches to lazy-loading tabs), the canary test will detect it as a zero-past-events result.

### Deterministic test dates
All tests that depend on the current date use a frozen reference date (`freezegun`) and/or pass a `reference_date` parameter. Fixture HTML files contain dates relative to this reference. Tests never depend on `date.today()` being a particular value, ensuring they pass regardless of when they are run. The live canary test is the sole exception — it intentionally uses the real date.

---

## Subscriber Instructions

### Google Calendar
1. Open Google Calendar → Settings → "Other calendars" → "From URL".
2. Paste: `https://{username}.github.io/{repo}/physics-colloquium.ics`
3. Click "Add calendar".

Note: Google Calendar refreshes subscribed calendars approximately every 12–24 hours. In the worst case (UQ updates their website shortly after the daily CI run at 1pm AEST), it could take up to ~35 hours for a change to appear in your Google Calendar.

### Apple Calendar
1. File → New Calendar Subscription.
2. Paste the `.ics` URL.
3. Set refresh frequency (e.g. "Every day").

### Outlook
1. Add calendar → "From Internet" / "Subscribe from web".
2. Paste the `.ics` URL.

---

## Operator Runbook

Quick reference for common operational scenarios.

### Subscriber reports stale events
A subscriber sees an old title or time even though the website has been updated.

1. Check the CI workflow — has it run since the website change? (Daily at 3am UTC / 1pm AEST.)
2. Check the last commit — does the `.ics` file contain the updated content?
3. If the `.ics` is correct: it's a client-side cache issue. Google Calendar refreshes every 12–24 hours. Apple Calendar depends on the subscriber's configured refresh interval. Outlook varies.
4. If the subscriber has waited >24 hours and still sees stale data: the `SEQUENCE:0` limitation may be the cause. Some clients (notably older Outlook) don't pick up content changes for an existing UID without a `SEQUENCE` increment. This is especially common for TBA → real title changes, which happen frequently. **Remediation:** the subscriber should unsubscribe from the calendar and resubscribe. This forces a clean import.

### Canary test fails
The weekly canary test (`canary.yml`) has failed, indicating the UQ website structure may have changed.

1. Open the live page in a browser and inspect with devtools.
2. Compare the actual DOM structure against the spec (Features 2 and 3).
3. Common causes: tab container ID changed, CSS class names renamed, speaker/affiliation format changed, new wrapper elements added, tab navigation removed entirely.
4. Update the parser code to match the new structure. Update fixtures. Run unit tests.

### CI commits empty diff / DTSTAMP-only changes
The CI workflow ran but made no commit.

This is expected behaviour. The content-aware diff (Feature 12, step 6) correctly detected that only `DTSTAMP` changed and skipped the commit. No action needed.

### CI fails with exit code 1
The orchestrator exited with code 1, meaning at least one series failed.

1. Check the CI logs for `ERROR`-level messages. These indicate which series failed and why.
2. Common causes: main page fetch failed (UQ server down or URL changed), validation FAIL (parser returned empty/invalid data).
3. If the URL has permanently changed (e.g. 301 redirect logged): update the `url` in the `SERIES` config.
4. If the server was temporarily down: the next daily run will succeed. The existing `.ics` remains intact.
5. If an SSL error was logged: check whether UQ's certificate has expired or changed. This may require waiting for UQ IT to fix their certificate.

### Verifying robots.txt compliance
Before first deployment, check `https://smp.uq.edu.au/robots.txt` to confirm that `/event/` paths are not disallowed. As of March 2026, the robots.txt does not block these paths. Re-check if UQ deploys bot detection or the fetcher starts receiving 403 responses.

### Adding a new series
1. Verify the new series page uses the same Foundation tab layout as the Physics Colloquium page.
2. Add an entry to `SERIES` in `src/main.py`.
3. Add a corresponding link to `docs/index.html`.
4. Run the pipeline manually (`uv run python -m src.main`) and verify the output.
5. If the series uses a different HTML template, implement a new parser function and add a `"parser"` key to the series config.
6. **Note:** A new series with less than a full year of history may trigger the `MIN_PLAUSIBLE_EVENTS` warning. This is expected and can be suppressed by temporarily lowering the threshold or by waiting for the series to accumulate enough events.

### Scraper blocked (HTTP 403 or bot detection)
UQ may deploy rate limiting, bot detection (e.g. Cloudflare), or return HTTP 403 for automated requests.

1. Check the CI logs for the HTTP status code. The fetcher logs it on failure.
2. If 403: the `USER_AGENT` string in `src/constants.py` identifies the scraper by name and links to the GitHub repository. This transparency is intentional — it allows UQ IT to identify the traffic and contact the operator if needed.
3. If Cloudflare or a CAPTCHA challenge is returned: the fetcher will receive an HTML page that is not the expected content. The main page parser will find zero entry blocks and log an error. The validator will FAIL on the empty event list.
4. **Remediation:** Contact UQ SMP IT (or the series coordinator) to request an exemption or discuss acceptable scraping practices. The pipeline makes ~45 requests per daily run with a 0.5s delay between each — this is a very light load.
5. As a temporary workaround, the pipeline can be run manually from a different IP, or the `.ics` files can be generated locally and committed directly.

### Low enrichment rate warning
The validator reports that fewer than 50% of events have `enriched = True`.

1. Check the CI logs for individual session page fetch failures. If many session pages returned errors (5xx, timeouts), this is likely a transient server issue — the next run should recover.
2. If session pages are returning HTTP 200 but enrichment is failing, this may indicate a **soft-404** (CMS returning a generic page) or a **session page template change**. Check the session page parser's warnings for "soft-404" or "date mismatch" messages.
3. Open a session page in the browser and compare its structure against Feature 3's expected HTML structure.

---

## Future Extensions

- **Additional seminar series:** Add entries to the `SERIES` config. If new series use the same UQ SMP page template, the existing parsers work unchanged. Different HTML structures require new parser functions, but the rest of the pipeline (ICS generation, validation, CI/CD) stays the same.
- **Per-series subscribe URLs:** Each series gets its own `.ics` file. Subscribers choose which to follow.
- **Combined calendar:** Optionally generate an `all-seminars.ics` that merges all series. Note: if implemented, the UID format must be extended to include the series name to avoid collisions across series that share a session ID (see Feature 4, "Cross-series UID note").
- **Configurable past event window:** `PAST_EVENT_CUTOFF_DAYS` can be adjusted per-series if different series warrant different retention windows.
- **Conditional HTTP requests:** The fetcher could use `If-None-Match` / `If-Modified-Since` headers to avoid re-downloading unchanged pages, reducing load on UQ's servers.
- **Per-series validator thresholds:** `MIN_PLAUSIBLE_EVENTS` and `MAX_PLAUSIBLE_EVENTS` could be made configurable per-series (in the `SERIES` dict) to accommodate series with different posting frequencies.
- **SEQUENCE tracking for TBA updates:** A lightweight JSON state file (`{UID: content_hash}`) could be maintained between runs, allowing `SEQUENCE` to increment when an event's content changes. This would improve update propagation to clients (especially Outlook) for the common TBA-to-real-title transition. This breaks the pure stateless design but may be worth the trade-off if stale-title complaints become frequent.
- **`--series` CLI flag:** Allow running a single series by name for faster debugging cycles (e.g. `python -m src.main --series "Physics colloquium"`).
