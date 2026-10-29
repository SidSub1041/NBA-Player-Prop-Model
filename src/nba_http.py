"""
Resilience layer for every nba_api call.

stats.nba.com is slow or silently unreachable from many cloud networks
(GitHub Actions runners included): requests hang until the read timeout.
Importing this module patches nba_api's HTTP layer so that every endpoint:

  * uses a configurable timeout      (NBA_TIMEOUT, default 45 s)
  * retries transient failures       (NBA_RETRIES, default 2)
  * can route through a proxy        (NBA_PROXY, e.g. http://user:pass@host:port)
  * trips a circuit breaker, PER ENDPOINT, after repeated failures so a
    blocked or slow endpoint fails instantly for the rest of the run instead
    of stretching it across hours. Optional endpoints (Synergy playtypes)
    degrade gracefully; an essential one tripping makes main.py abort without
    writing a board.

Import it before any endpoint is used:  `import src.nba_http`
"""

import logging
import os
import time
from contextlib import contextmanager

import requests
from nba_api.library import http as nba_http

logger = logging.getLogger(__name__)

TIMEOUT = float(os.environ.get("NBA_TIMEOUT", "45"))
RETRIES = int(os.environ.get("NBA_RETRIES", "2"))
PROXY = os.environ.get("NBA_PROXY") or None
BREAKER_THRESHOLD = 3     # consecutive failures (per endpoint) before failing fast
# Endpoints the board can do without: their breaker never aborts a run.
# playergamelogs (the league-wide game-log table, src/gamelogs.py) falls back
# to the disk cache or to per-player PlayerGameLog calls; leaguedashptstats (the
# passing tables, src/passing.py) only holds assists back when it is down.
OPTIONAL_ENDPOINTS = {"synergyplaytypes", "playergamelogs", "playerindex", "leaguedashptstats"}

_failures: dict[str, int] = {}     # endpoint -> consecutive failures
_original_send = nba_http.NBAHTTP.send_api_request


def breaker_open(endpoint: str | None = None) -> bool:
    """Is this endpoint failing fast? With no endpoint: is any ESSENTIAL endpoint?"""
    if endpoint is not None:
        return _failures.get(endpoint, 0) >= BREAKER_THRESHOLD
    return any(n >= BREAKER_THRESHOLD for ep, n in _failures.items() if ep not in OPTIONAL_ENDPOINTS)


def reset_breakers() -> None:
    _failures.clear()


@contextmanager
def optional_calls():
    """
    Requests made inside never decide the run: whatever they do to an
    essential endpoint's failure count (src/passing.py asks the same team
    endpoints the zone rankings and pace use) is undone on the way out, so a
    blocked passing-only request costs its feature, never the board. Inside,
    a breaker that opens still fails the remaining calls fast.
    """
    saved = {ep: n for ep, n in _failures.items() if ep not in OPTIONAL_ENDPOINTS}
    try:
        yield
    finally:
        for ep in list(_failures):
            if ep not in OPTIONAL_ENDPOINTS:
                if ep in saved:
                    _failures[ep] = saved[ep]
                else:
                    del _failures[ep]


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
    if proxy is None and PROXY:
        proxy = PROXY

    if breaker_open(endpoint):
        # This endpoint is down for the run: fail immediately so the caller can
        # move on (or abort) instead of spending the day on doomed requests.
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
                _failures[endpoint] = 0
                return response
            # A block page (HTML / "An error has occurred") counts as a failure.
            # Say what came back: a proxy that is out of traffic or rejecting
            # credentials answers every request with its own HTML page.
            last_error = None
            logger.warning(f"stats.nba.com {endpoint}: non-JSON response (attempt {attempt}/{attempts}) — {_describe(response)}")
        except (requests.exceptions.InvalidURL, requests.exceptions.InvalidSchema,
                requests.exceptions.MissingSchema):
            raise  # caller bug / bad NBA_PROXY — never retry
        except requests.exceptions.RequestException as e:
            last_error = e
            logger.warning(
                f"stats.nba.com {endpoint}: {type(e).__name__} (attempt {attempt}/{attempts})"
            )
        _failures[endpoint] = _failures.get(endpoint, 0) + 1
        if breaker_open(endpoint):
            logger.error(
                f"stats.nba.com {endpoint}: {_failures[endpoint]} consecutive failures — "
                f"failing fast for the rest of this run"
                + (" (optional endpoint: the board continues without it)" if endpoint in OPTIONAL_ENDPOINTS else "")
            )
            break
        if attempt < attempts:
            time.sleep(3 * attempt)
    if last_error is not None:
        raise last_error
    return response  # non-JSON after all attempts; the endpoint raises a clear error


def _describe(response) -> str:
    """HTTP status plus a glimpse of a non-JSON body, for the log."""
    status = getattr(response, "_status_code", None) or getattr(response, "status_code", None)
    body = getattr(response, "_response", None)
    if body is None and hasattr(response, "get_response"):
        try:
            body = response.get_response()
        except Exception:
            body = None
    text = " ".join(str(body or "").split())[:160]
    hint = ""
    low = text.lower()
    if status in (402, 407) or "traffic" in low or "bandwidth" in low or "quota" in low or "payment" in low:
        hint = " [looks like the PROXY itself answered: out of traffic / payment / auth — check the proxy dashboard]"
    elif "<html" in low or "<!doctype" in low:
        hint = " [HTML page instead of data: a block or proxy page]"
    return f"HTTP {status}: {text!r}{hint}"


def install():
    if getattr(nba_http.NBAHTTP, "_resilient_installed", False):
        return
    nba_http.NBAHTTP.send_api_request = _resilient_send
    nba_http.NBAHTTP._resilient_installed = True
    if PROXY:
        logger.info("nba_api calls routed through NBA_PROXY")


install()
