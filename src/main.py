import argparse
import logging
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from src.calendar import generate_ics
from src.constants import (
    DEFAULT_DURATION_HOURS,
    DEFAULT_START_HOUR,
    LOG_LEVEL,
    OUTPUT_DIR,
)
from src.fetcher import Fetcher, fetch_page
from src.models import Event
from src.parsers import parse_main_page, parse_session_page
from src.validator import validate

log = logging.getLogger(__name__)

BRISBANE = ZoneInfo("Australia/Brisbane")

SERIES = [
    {
        "name": "Physics colloquium",
        "url": "https://smp.uq.edu.au/event/99/physics-colloquium",
        "output": "physics-colloquium.ics",
    },
]


def run_pipeline(
    series_configs: Sequence[Mapping[str, str]],
    reference_date: date | None = None,
    fetcher: Fetcher = fetch_page,
    dry_run: bool = False,
) -> bool:
    dtstamp = datetime.now(timezone.utc).replace(second=0, microsecond=0)

    if reference_date is None:
        ref_date = date.today()
    elif isinstance(reference_date, datetime):
        ref_date = reference_date.date()
    else:
        ref_date = reference_date

    all_ok = True
    results: list[tuple[str, bool]] = []

    for series_config in series_configs:
        name = series_config["name"]
        url = series_config["url"]
        output_file = series_config["output"]
        output_path = os.path.join(OUTPUT_DIR, output_file)

        log.info("Processing series: %s", name)

        # 1. fetch main page
        html = fetcher(url)
        if html is None:
            log.error("Failed to fetch main page for %s: %s", name, url)
            all_ok = False
            results.append((name, False))
            continue

        # 2. parse main page
        events = parse_main_page(html, name, reference_date=ref_date)

        # 3. enrich each event
        enriched_events: list[Event] = []
        for event in events:
            session_html = fetcher(event.session_url)
            if session_html is not None:
                event = parse_session_page(session_html, event)
            else:
                log.debug(
                    "Session %s: enrichment skipped (fetch failed)",
                    event.session_id,
                )

            # post-enrichment fixup
            event = _apply_default_time(event)
            enriched_events.append(event)

        # 4. validate
        result = validate(enriched_events, reference_date=ref_date)
        if not result.passed:
            for f in result.failures:
                log.error("FAIL: %s: %s", name, f)
            all_ok = False
            results.append((name, False))
            continue

        for w in result.warnings:
            log.warning("WARN: %s: %s", name, w)

        # 5. generate ICS
        ics_bytes = generate_ics(enriched_events, name, dtstamp)

        enriched_count = sum(1 for e in enriched_events if e.enriched)
        warning_count = sum(len(e.warnings) for e in enriched_events)
        log.info(
            "%s: %d events, %d enriched, %d warnings",
            name,
            len(enriched_events),
            enriched_count,
            warning_count,
        )

        # 6. write or dry-run
        if dry_run:
            if os.path.exists(output_path):
                with open(output_path, encoding="utf-8") as f:
                    existing = f.read()
                new = ics_bytes.decode("utf-8")
                if _strip_dtstamp(existing) != _strip_dtstamp(new):
                    log.info("DRY RUN: content would change for %s", name)
                else:
                    log.info("DRY RUN: no content change for %s", name)
            else:
                log.info("DRY RUN: would create new file for %s", name)
            results.append((name, True))
            continue

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        tmp_path = os.path.join(OUTPUT_DIR, f".{output_file}.tmp")
        try:
            with open(tmp_path, "wb") as f:
                f.write(ics_bytes)
            os.replace(tmp_path, output_path)
        except Exception:
            log.exception("Failed to write %s", output_path)
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            all_ok = False
            results.append((name, False))
            continue

        results.append((name, True))

    # machine-readable summary to stdout
    for name, ok in results:
        if ok:
            print(f"UPDATED: {name}")
        else:
            print(f"FAILED: {name}")

    return all_ok


def main() -> None:
    parser = argparse.ArgumentParser(description="UQ SMP Seminar Calendar Generator")
    parser.add_argument(
        "--dry-run", action="store_true", help="Run without writing files"
    )
    parser.add_argument("--log-level", default=None, help="Override log level")
    args = parser.parse_args()

    level = args.log_level or os.environ.get("LOG_LEVEL", LOG_LEVEL)
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    ok = run_pipeline(SERIES, dry_run=args.dry_run)
    sys.exit(0 if ok else 1)


def _apply_default_time(event: Event) -> Event:
    if event.start is not None:
        return event
    default_start = datetime(
        event.date.year,
        event.date.month,
        event.date.day,
        DEFAULT_START_HOUR,
        0,
        0,
        tzinfo=BRISBANE,
    )
    return replace(
        event,
        start=default_start,
        end=default_start + timedelta(hours=DEFAULT_DURATION_HOURS),
        time_unconfirmed=True,
        warnings=(
            *event.warnings,
            f"Session {event.session_id}: no time found on main page "
            f"or session page — defaulted to "
            f"{DEFAULT_START_HOUR}:00-"
            f"{DEFAULT_START_HOUR + DEFAULT_DURATION_HOURS}:00",
        ),
    )


def _strip_dtstamp(text: str) -> str:
    unfolded = re.sub(r"\r?\n[ \t]", "", text)
    normalized = unfolded.replace("\r\n", "\n").replace("\r", "\n")
    skip = {"DTSTAMP", "CREATED", "LAST-MODIFIED"}
    return "\n".join(
        line
        for line in normalized.splitlines()
        if not any(line.startswith(prefix) for prefix in skip)
    )


if __name__ == "__main__":
    main()
