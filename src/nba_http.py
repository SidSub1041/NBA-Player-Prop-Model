"""
Resilience layer for every nba_api call.

stats.nba.com is slow or silently unreachable from many cloud networks
(GitHub Actions runners included): requests hang until the read timeout.
Importing this module patches nba_api's HTTP layer so that every endpoint:

  * uses a configurable timeout      (NBA_TIMEOUT, default 45 s)
  * retries transient failures       (NBA_RETRIES, default 2)
  * can route through a proxy        (NBA_PROXY, e.g. http://user:pass@host:port)
  * trips a circuit breaker after repeated failures so a blocked host
    fails instantly for the rest of the run instead of stretching it
    across hours (main.py then aborts without writing a board)

Import it before any endpoint is used:  `import src.nba_http`
"""

import logging
import os
import time

import requests
from nba_api.library import http as nba_http

logger = logging.getLogger(__name__)

TIMEOUT = float(os.environ.get("NBA_TIMEOUT", "45"))
RETRIES = int(os.environ.get("NBA_RETRIES", "2"))
PROXY = os.environ.get("NBA_PROXY") or None
BREAKER_THRESHOLD = 3     # consecutive failures before failing fast

_consecutive_failures = 0
_original_send = nba_http.NBAHTTP.send_api_request


def breaker_open() -> bool:
    return _consecutive_failures >= BREAKER_THRESHOLD


def _resilient_send(
    self,
    endpoint,
    parameters,
    referer=None,
    proxy=None,
    headers=None,
    timeout=None,
    raise_exception_on_error=False,
):
    global _consecutive_failures

    if proxy is None and PROXY:
        proxy = PROXY

    if breaker_open():
        # The host is down for this run: fail immediately so the caller can
        # abort instead of spending the day on doomed requests.
        raise requests.exceptions.ConnectionError(
            f"stats.nba.com unreachable (circuit breaker open) — {endpoint}"
        )

    attempts, call_timeout = RETRIES + 1, max(float(timeout or 0), TIMEOUT)
    last_error = None
    response = None
    for attempt in range(1, attempts + 1):
        try:
            response = _original_send(
                self,
                endpoint,
                parameters,
                referer=referer,
                proxy=proxy,
                headers=headers,
                timeout=call_timeout,
                raise_exception_on_error=raise_exception_on_error,
            )
            valid = response.valid_json() if hasattr(response, "valid_json") else True
            if valid:
                _consecutive_failures = 0
                return response
            # A block page (HTML / "An error has occurred") counts as a failure
            last_error = None
            logger.warning(f"stats.nba.com {endpoint}: non-JSON response (attempt {attempt}/{attempts})")
        except (requests.exceptions.InvalidURL, requests.exceptions.InvalidSchema,
                requests.exceptions.MissingSchema):
            raise  # caller bug / bad NBA_PROXY — never retry
        except requests.exceptions.RequestException as e:
            last_error = e
            logger.warning(
                f"stats.nba.com {endpoint}: {type(e).__name__} (attempt {attempt}/{attempts})"
            )
        _consecutive_failures += 1
        if breaker_open():
            logger.error(
                f"stats.nba.com unreachable ({_consecutive_failures} consecutive failures) "
                f"— failing fast for the rest of this run"
            )
            break
        if attempt < attempts:
            time.sleep(3 * attempt)
    if last_error is not None:
        raise last_error
    return response  # non-JSON after all attempts; the endpoint raises a clear error


def install():
    if getattr(nba_http.NBAHTTP, "_resilient_installed", False):
        return
    nba_http.NBAHTTP.send_api_request = _resilient_send
    nba_http.NBAHTTP._resilient_installed = True
    if PROXY:
        logger.info("nba_api calls routed through NBA_PROXY")


install()
