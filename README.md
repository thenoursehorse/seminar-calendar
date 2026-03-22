# UQ Seminar Calendar

Scrapes UQ School of Mathematics and Physics seminar pages, generates subscribable `.ics` calendar feeds, and publishes them via GitHub Pages. Updated daily.

## Subscribe

**Physics Colloquium:** `https://thenoursehorse.github.io/seminar-calendar/physics-colloquium.ics`

See the [landing page](https://thenoursehorse.github.io/seminar-calendar/) for subscription instructions.

## How it works

Fetch → Parse → Enrich → Validate → Generate ICS → Publish. Runs daily via GitHub Actions at 1pm AEST.

## Development

```bash
uv sync                              # install dependencies
uv run python -m src.main --dry-run  # test run without writing files
uv run python -m src.main            # full run (~30-60s due to rate limiting)
```

## Tests

```bash
uv run pytest                  # unit + integration tests
uv run pytest -m live          # live canary test (hits UQ website)
```

## Adding a new series

1. Verify the series page uses the same tab layout.
2. Add an entry to `SERIES` in `src/main.py`.
3. Add a link to `docs/index.html`.
4. Run `uv run python -m src.main` and verify output.

## Note

Completely vibe coded. See spec.md for original vibe code generated programming specification.
