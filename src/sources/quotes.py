"""Live market quotes for the holdings (M13).

M12 shipped prices as a hand-maintained CSV because nothing free seemed to
carry these bonds. Two sources turned out to work after all:

* **Public.com** for the US issues. The URL is derivable — a US ISIN minus
  its ``US`` prefix and check digit is the CUSIP — so no per-bond config is
  needed, and the page states coupon and maturity next to the price.
* **E.SUN Bank's offshore-bond table** for the Nan Shan issue, a XS-prefixed
  eurobond that US sources do not cover. One request returns every bond the
  bank quotes, each row carrying its own quote date.

Both are scraped HTML, which will eventually break. That is designed for
rather than hoped against: every quote is checked against the coupon and
maturity in positions.csv before it is trusted, so a redesigned page or a
wrong-bond redirect yields *no* quote instead of a plausible-looking wrong
one, and the digest falls back to prices.csv with its older date on show.
"""
from __future__ import annotations

import csv
import re
import time
from datetime import date as Date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests

from ..models import PricePoint

PUBLIC_URL = "https://public.com/bonds/{cusip}"
ESUN_URL = "https://wealth.esunbank.com/zh-tw/offshore-bond/price"
DEFAULT_TIMEOUT = 20.0
FETCH_MAX_ATTEMPTS = 2
FETCH_RETRY_BACKOFF_SEC = 0.5
# Public.com blocks the default python-requests UA.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# A clean price outside this band is not a bond quote — it is a parse that
# latched onto the wrong number. Our own book runs 88-131.
MIN_PRICE = Decimal("20")
MAX_PRICE = Decimal("300")
COUPON_TOLERANCE = Decimal("0.005")

# Public.com renders label/value pairs through CSS modules, so the class name
# carries a build hash in the middle but keeps a stable __label / __value
# suffix. Keying off the visible label text survives a redeploy. The label
# must be the cell's whole text (">Price<", never ">Ask Price<"); the value
# is then the first one following it, several hundred bytes of tooltip
# button and inline SVG later.
_PUBLIC_LABEL = r'__label"[^>]*>\s*{label}\s*<'
_PUBLIC_VALUE = re.compile(r'__value"[^>]*>\$?([^<]{1,25})<')
_PUBLIC_VALUE_WINDOW = 4000
_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_TAGS = re.compile(r"<[^>]+>")
# E.SUN puts the quote date in the price cell: "95.56 (2026/08/26)".
_ESUN_PRICE = re.compile(r"([\d.]+)\s*(?:\((\d{4}/\d{2}/\d{2})\))?")


class _Target:
    """One holding we want a quote for, with the facts that identify it."""

    __slots__ = ("isin", "name", "coupon", "maturity")

    def __init__(self, isin: str, name: str, coupon: Decimal | None, maturity: Date | None):
        self.isin = isin
        self.name = name
        self.coupon = coupon
        self.maturity = maturity


def _parse_date(raw: str) -> Date | None:
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _parse_decimal(raw: str) -> Decimal | None:
    try:
        val = Decimal(raw.strip().rstrip("%").replace(",", "").replace("$", ""))
    except (InvalidOperation, ValueError, AttributeError):
        return None
    return val if val.is_finite() else None


def read_targets(path: Path) -> tuple[list[_Target], list[str]]:
    """Pull the quotable holdings out of positions.csv.

    Only US and XS ISINs go out to a source; the FCN's CH code has no public
    quote to look up.
    """
    if not path.exists():
        return [], ["quotes: positions file not found"]
    try:
        with path.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
    except OSError as e:
        return [], [f"quotes: positions read error: {e}"]

    targets: list[_Target] = []
    for row in rows:
        isin = (row.get("isin_or_code") or "").strip().upper()
        if not isin.startswith(("US", "XS")):
            continue
        targets.append(
            _Target(
                isin=isin,
                name=(row.get("issuer_or_name") or "").strip(),
                coupon=_parse_decimal(row.get("coupon_rate_pct") or ""),
                maturity=_parse_date(row.get("maturity") or ""),
            )
        )
    return targets, []


def _get(session: requests.Session, url: str, timeout: float) -> str | None:
    last: requests.RequestException | None = None
    for attempt in range(FETCH_MAX_ATTEMPTS):
        try:
            resp = session.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
        except requests.RequestException as exc:
            last = exc
            if attempt + 1 < FETCH_MAX_ATTEMPTS:
                time.sleep(FETCH_RETRY_BACKOFF_SEC)
            continue
        if resp.encoding is None:
            resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text
    raise last if last else requests.RequestException("no attempt made")


def _identity_matches(t: _Target, coupon: Decimal | None, maturity: Date | None) -> str | None:
    """Return why the source did not prove it is describing our bond.

    A missing source value is a failed identity check, not permission to skip
    that check. Otherwise a partial page redesign could leave the price
    parseable while silently removing the fields that protect us from using a
    plausible quote for the wrong security.
    """
    if t.coupon is not None:
        if coupon is None:
            return "coupon unavailable"
        if abs(coupon - t.coupon) > COUPON_TOLERANCE:
            return f"coupon {coupon} != {t.coupon}"
    if t.maturity is not None:
        if maturity is None:
            return "maturity unavailable"
        if maturity != t.maturity:
            return f"maturity {maturity} != {t.maturity}"
    return None


def _public_field(html: str, label: str) -> str | None:
    """Read the value rendered next to `label` on a Public.com bond page."""
    m = re.search(_PUBLIC_LABEL.format(label=re.escape(label)), html, re.I)
    if m is None:
        return None
    value = _PUBLIC_VALUE.search(html, m.end(), m.end() + _PUBLIC_VALUE_WINDOW)
    return value.group(1).strip() if value else None


def _fetch_public(
    t: _Target, today: Date, session: requests.Session, timeout: float
) -> tuple[PricePoint | None, str | None]:
    # US0123456789 -> 0123456789 minus the trailing check digit = CUSIP.
    cusip = t.isin[2:-1].lower()
    html = _get(session, PUBLIC_URL.format(cusip=cusip), timeout)
    if html is None:
        return None, f"quotes {t.isin}: no response"

    price = _parse_decimal(_public_field(html, "Price") or "")
    if price is None:
        return None, f"quotes {t.isin}: price not found on public.com"
    if not (MIN_PRICE <= price <= MAX_PRICE):
        return None, f"quotes {t.isin}: price {price} out of range"

    mismatch = _identity_matches(
        t,
        _parse_decimal(_public_field(html, "Coupon") or ""),
        _parse_date(_public_field(html, "Maturity") or ""),
    )
    if mismatch:
        return None, f"quotes {t.isin}: bond identity check failed ({mismatch})"
    # The page carries no timestamp; it is a live quote, so the run date is
    # the honest answer.
    return PricePoint(price=price, as_of=today), None


def _fetch_esun(
    targets: list[_Target], session: requests.Session, timeout: float
) -> tuple[dict[str, PricePoint], list[str]]:
    """One table request covers every XS holding.

    The redemption quote is the one that matters for a mark: it is what the
    bank would pay to take the bond back, i.e. what the position is worth to
    us, not what buying more would cost.
    """
    out: dict[str, PricePoint] = {}
    exceptions: list[str] = []
    html = _get(session, ESUN_URL, timeout)
    if html is None:
        return out, ["quotes: no response from esunbank"]

    for t in targets:
        idx = html.find(t.isin)
        if idx < 0:
            exceptions.append(f"quotes {t.isin}: not listed by esunbank")
            continue
        row = html[html.rfind("<tr", 0, idx) : html.find("</tr>", idx)]
        cells = [_TAGS.sub("", c).strip() for c in _TD.findall(row)]
        try:
            k = cells.index(t.isin)
            coupon_raw, maturity_raw, redemption_raw = cells[k + 1], cells[k + 2], cells[k + 5]
        except (ValueError, IndexError):
            exceptions.append(f"quotes {t.isin}: unexpected esunbank row layout")
            continue

        m = _ESUN_PRICE.match(redemption_raw)
        price = _parse_decimal(m.group(1)) if m else None
        if price is None:
            exceptions.append(f"quotes {t.isin}: unreadable esunbank price")
            continue
        if not (MIN_PRICE <= price <= MAX_PRICE):
            exceptions.append(f"quotes {t.isin}: price {price} out of range")
            continue

        mismatch = _identity_matches(t, _parse_decimal(coupon_raw), _parse_date(maturity_raw))
        if mismatch:
            exceptions.append(
                f"quotes {t.isin}: bond identity check failed ({mismatch})"
            )
            continue

        # Each row states the date its quote was struck — better than assuming
        # today, since the table does not move at weekends.
        out[t.isin] = PricePoint(price=price, as_of=_parse_date(m.group(2) or ""))
    return out, exceptions


def fetch_quotes(
    positions_path: Path,
    today: Date,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    session: requests.Session | None = None,
) -> tuple[dict[str, PricePoint], list[str]]:
    """Fetch what we can; report what we could not.

    A per-holding failure costs only that holding — the caller layers the
    result over prices.csv, so anything missing here keeps its stored value
    and the older date that goes with it.
    """
    targets, exceptions = read_targets(positions_path)
    if not targets:
        return {}, exceptions

    owned = session is None
    session = session or requests.Session()
    quotes: dict[str, PricePoint] = {}
    try:
        for t in (x for x in targets if x.isin.startswith("US")):
            try:
                quote, err = _fetch_public(t, today, session, timeout)
            except requests.RequestException as exc:
                quotes.pop(t.isin, None)
                exceptions.append(f"quotes {t.isin}: {type(exc).__name__}")
                continue
            if quote is not None:
                quotes[t.isin] = quote
            elif err:
                exceptions.append(err)

        xs = [x for x in targets if x.isin.startswith("XS")]
        if xs:
            try:
                found, exc = _fetch_esun(xs, session, timeout)
                quotes.update(found)
                exceptions.extend(exc)
            except requests.RequestException as exc:
                exceptions.append(f"quotes esunbank: {type(exc).__name__}")
    finally:
        if owned:
            session.close()

    return quotes, exceptions
