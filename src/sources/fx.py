from __future__ import annotations

import time
from datetime import date as Date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import requests

from ..models import FxResult

DEFAULT_BASE_URL = "https://api.frankfurter.app"
DEFAULT_TIMEOUT = 5.0
RATE_QUANTUM = Decimal("0.0001")
PCT_QUANTUM = Decimal("0.01")
# Mirrors news.py: a meaningful UA is good hygiene (some providers 403 the
# default python-requests UA), and a single short-backoff retry stops a
# one-off network blip on the *latest* fetch from blanking the whole FX
# section. Retry covers transient transport errors only — a bad/garbled
# response is a data problem that won't fix itself on a re-request.
USER_AGENT = "portfolio-digest-linebot/0.1"
FETCH_MAX_ATTEMPTS = 2
FETCH_RETRY_BACKOFF_SEC = 0.5


_FETCH_ERRORS = (requests.RequestException, KeyError, ValueError, InvalidOperation)


def _fetch(base_url: str, segment: str, timeout: float) -> tuple[Date, Decimal]:
    last_exc: requests.RequestException | None = None
    for attempt in range(FETCH_MAX_ATTEMPTS):
        try:
            resp = requests.get(
                f"{base_url}/{segment}",
                params={"from": "USD", "to": "CHF"},
                timeout=timeout,
                headers={"User-Agent": USER_AGENT},
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            last_exc = exc
            if attempt + 1 < FETCH_MAX_ATTEMPTS:
                time.sleep(FETCH_RETRY_BACKOFF_SEC)
            continue
        # Parse outside the retry: KeyError / ValueError / InvalidOperation
        # here mean the API gave us something unusable, which a retry won't
        # cure — let them propagate to the caller's _FETCH_ERRORS handler.
        data = resp.json()
        rate = Decimal(str(data["rates"]["CHF"]))
        if not rate.is_finite():
            raise ValueError(f"non-finite rate: {data['rates']['CHF']!r}")
        return Date.fromisoformat(data["date"]), rate
    assert last_exc is not None  # loop above guarantees this
    raise last_exc


def fetch_fx(
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[FxResult | None, list[str]]:
    """Fetch USD/CHF rate from Frankfurter, derive CHF/USD and DoD%.

    Returns (FxResult, []) on success. The DoD% may be None when no prior
    business-day rate is available; the prior-day fetch failing softly is
    not treated as a full failure of the FX source.
    Returns (None, [reason]) only when the latest-rate fetch fails.
    """
    try:
        date_t, usd_chf = _fetch(base_url, "latest", timeout)
    except _FETCH_ERRORS as e:
        return None, [f"fx: latest fetch failed: {type(e).__name__}"]

    target = (date_t - timedelta(days=1)).isoformat()
    dod_pct: Decimal | None = None
    try:
        date_y, prior_rate = _fetch(base_url, target, timeout)
        if date_y != date_t and prior_rate != 0:
            dod_pct = (
                (usd_chf - prior_rate) / prior_rate * Decimal(100)
            ).quantize(PCT_QUANTUM, rounding=ROUND_HALF_UP)
    except _FETCH_ERRORS:
        pass

    usd_chf_q = usd_chf.quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)
    chf_usd_q = (Decimal(1) / usd_chf_q).quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)
    return (
        FxResult(
            usd_chf=usd_chf_q,
            chf_usd=chf_usd_q,
            usd_chf_dod_pct=dod_pct,
            as_of=date_t,
        ),
        [],
    )
