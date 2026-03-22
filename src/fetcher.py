import logging
import time
from typing import Protocol
from urllib.parse import urlparse

import requests

from src.constants import (
    ALLOWED_DOMAIN,
    ALLOWED_REDIRECT_DOMAIN,
    MAX_RETRIES,
    REQUEST_DELAY,
    REQUEST_TIMEOUT,
    USER_AGENT,
)

log = logging.getLogger(__name__)

_last_request_time: float | None = None
_session: requests.Session | None = None


class Fetcher(Protocol):
    def __call__(self, url: str) -> bytes | None: ...


def fetch_page(url: str) -> bytes | None:
    if not _is_allowed_domain(url, ALLOWED_DOMAIN):
        log.warning("URL %s not under allowed domain %s", url, ALLOWED_DOMAIN)
        return None

    session = _get_session()
    _enforce_rate_limit()

    for attempt in range(1 + MAX_RETRIES):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=False)

            # handle redirects manually
            if resp.is_redirect or resp.is_permanent_redirect:
                target = resp.headers.get("Location", "")
                if _is_allowed_domain(target, ALLOWED_REDIRECT_DOMAIN):
                    resp = session.get(
                        target, timeout=REQUEST_TIMEOUT, allow_redirects=False
                    )
                    if resp.is_redirect or resp.is_permanent_redirect:
                        log.warning("Double redirect from %s — not following", url)
                        return None
                else:
                    log.warning("Redirect to disallowed domain: %s -> %s", url, target)
                    return None

            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After", "2")
                try:
                    delay = min(max(2, int(retry_after)), 60)
                except ValueError:
                    delay = 2
                if attempt < MAX_RETRIES:
                    log.warning("HTTP 429 for %s, retrying in %ds", url, delay)
                    time.sleep(delay)
                    continue
                log.error("HTTP 429 for %s after %d attempts", url, attempt + 1)
                return None

            if resp.status_code >= 500:
                if attempt < MAX_RETRIES:
                    log.warning("HTTP %d for %s, retrying", resp.status_code, url)
                    time.sleep(2)
                    continue
                log.error(
                    "HTTP %d for %s after %d attempts",
                    resp.status_code,
                    url,
                    attempt + 1,
                )
                return None

            if resp.status_code >= 400:
                log.error("HTTP %d for %s", resp.status_code, url)
                return None

            return resp.content

        except requests.Timeout:
            if attempt < MAX_RETRIES:
                log.warning("Timeout for %s, retrying", url)
                time.sleep(2)
                continue
            log.error("Timeout for %s after %d attempts", url, attempt + 1)
            return None
        except requests.exceptions.SSLError:
            log.warning("SSL error for %s — not retrying", url)
            return None
        except requests.ConnectionError:
            if attempt < MAX_RETRIES:
                log.warning("Connection error for %s, retrying", url)
                time.sleep(2)
                continue
            log.error("Connection error for %s after %d attempts", url, attempt + 1)
            return None
        except requests.TooManyRedirects:
            log.error("Too many redirects for %s", url)
            return None

    return None


def _reset_rate_limiter() -> None:
    global _last_request_time  # noqa: PLW0603
    _last_request_time = None


def _get_session() -> requests.Session:
    global _session  # noqa: PLW0603
    if _session is None:
        _session = requests.Session()
        _session.headers["User-Agent"] = USER_AGENT
    return _session


def _is_allowed_domain(url: str, domain: str) -> bool:
    host = urlparse(url).hostname or ""
    return host == domain or host.endswith(f".{domain}")


def _enforce_rate_limit() -> None:
    global _last_request_time  # noqa: PLW0603
    now = time.monotonic()
    if _last_request_time is not None:
        elapsed = now - _last_request_time
        if elapsed < REQUEST_DELAY:
            time.sleep(REQUEST_DELAY - elapsed)
    _last_request_time = time.monotonic()
