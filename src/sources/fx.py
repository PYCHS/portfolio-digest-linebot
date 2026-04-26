from __future__ import annotations

from datetime import date as Date, timedelta
from decimal import Decimal, ROUND_HALF_UP

import requests

from ..models import FxResult

DEFAULT_BASE_URL = "https://api.frankfurter.app"
DEFAULT_TIMEOUT = 5.0
RATE_QUANTUM = Decimal("0.0001")
PCT_QUANTUM = Decimal("0.01")


def _fetch(base_url: str, segment: str, timeout: float) -> tuple[Date, Decimal]:
    resp = requests.get(
        f"{base_url}/{segment}",
        params={"from": "USD", "to": "CHF"},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    rate = Decimal(str(data["rates"]["CHF"]))
    return Date.fromisoformat(data["date"]), rate


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
    except (requests.RequestException, KeyError, ValueError) as e:
        return None, [f"fx: latest fetch failed: {type(e).__name__}"]

    target = (date_t - timedelta(days=1)).isoformat()
    dod_pct: Decimal | None = None
    try:
        date_y, prior_rate = _fetch(base_url, target, timeout)
        if date_y != date_t and prior_rate != 0:
            dod_pct = (
                (usd_chf - prior_rate) / prior_rate * Decimal(100)
            ).quantize(PCT_QUANTUM, rounding=ROUND_HALF_UP)
    except (requests.RequestException, KeyError, ValueError):
        pass

    usd_chf_q = usd_chf.quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)
    chf_usd_q = (Decimal(1) / usd_chf_q).quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)
    return FxResult(usd_chf=usd_chf_q, chf_usd=chf_usd_q, usd_chf_dod_pct=dod_pct), []
