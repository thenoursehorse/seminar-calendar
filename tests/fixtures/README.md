# Test Fixtures

All fixture dates are relative to `REFERENCE_DATE = date(2026, 3, 23)` defined in `conftest.py`.

Fixture dates must fall within `PAST_EVENT_CUTOFF_DAYS` (365) of REFERENCE_DATE or they will be discarded.

## Main page fixture date assignments

| Purpose | Date | Session ID |
|---|---|---|
| Upcoming (near future) | 2026-03-30 | 10001 |
| Same day as reference | 2026-03-23 | 10002 |
| Second same-day event | 2026-03-23 | 10003 |
| Cancelled event | 2026-03-30 | 10004 |
| No time (save-the-date) | 2026-03-30 | 10005 |
| Single time no range | 2026-03-30 | 10006 |
| Unlabelled speaker | 2026-03-30 | 10007 |
| Multi-speaker format | 2026-03-30 | 10008 |
| Plural labelled speaker | 2026-03-30 | 10009 |
| Inline abstract | 2026-03-30 | 10010 |
| Dedup test (upcoming copy) | 2026-02-21 | 10011 |
| No speaker/affiliation | 2026-03-30 | 10012 |
| Recent past (~30 days) | 2026-02-21 | 10020 |
| Older past (~200 days) | 2025-09-05 | 10021 |
| Dedup test (past copy) | 2026-02-21 | 10011 |
| Beyond cutoff (~400 days) | 2025-02-17 | 10022 |

The Contacts section (Karen Kheruntsyan) is included above the tabs to test scoping.
