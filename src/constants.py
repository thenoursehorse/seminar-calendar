import re

# scraping
REQUEST_TIMEOUT: int = 15
REQUEST_DELAY: float = 0.5
MAX_RETRIES: int = 1
USER_AGENT: str = "UQ-Seminar-Calendar/1.0 (github.com/henryphilipps/seminar-calendar)"
ALLOWED_DOMAIN: str = "smp.uq.edu.au"
ALLOWED_REDIRECT_DOMAIN: str = "uq.edu.au"
BASE_URL: str = "https://smp.uq.edu.au"

# URL patterns
SESSION_URL_PATTERN: re.Pattern[str] = re.compile(r"/event/session/(\d+)")

# calendar generation
PAST_EVENT_CUTOFF_DAYS: int = 365
PRODID: str = "-//UQ Seminar Calendar//EN"
UID_DOMAIN: str = "uq-seminar-calendar"
TIMEZONE: str = "Australia/Brisbane"
DEFAULT_START_HOUR: int = 11
DEFAULT_DURATION_HOURS: int = 1

# validator thresholds
MIN_PLAUSIBLE_EVENTS: int = 10
MAX_PLAUSIBLE_EVENTS: int = 100
MIN_ENRICHMENT_RATE: float = 0.5
MAX_FUTURE_DAYS: int = 365

# output
OUTPUT_DIR: str = "docs"
LOG_LEVEL: str = "INFO"
