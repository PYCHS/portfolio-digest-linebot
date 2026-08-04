"""M10 — LLM news-impact analysis (Traditional Chinese).

Sends the day's headlines to the Anthropic Messages API in one call and maps
the result back onto the NewsItem list. Any failure — network, auth, quota,
malformed model output — degrades gracefully: the original items are returned
untouched and one exception string is surfaced in Notes, so the digest never
depends on the LLM being up.
"""
from __future__ import annotations

import dataclasses
import json
import logging
from datetime import date as Date

import requests

from .models import NewsItem

log = logging.getLogger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-haiku-4-5"
DEFAULT_TIMEOUT = 45.0
MAX_TOKENS = 1500

VALID_IMPACTS = {"利多", "利空", "中性", "無影響"}

DEFAULT_CONTEXT = (
    "家族投資公司持有多家公司的美元公司債（重點是發行公司的信用風險與"
    "付息能力，股價漲跌本身影響不大），以及連結個股的 FCN 結構型商品"
    "（連結股票大跌、接近下檔保護價時才有實質影響）。"
)

SYSTEM_PROMPT = """你是一間家族投資公司的分析助理。使用者會給你今天抓到的新聞標題清單（JSON）。

持倉背景：{context}

請針對每一則新聞，從「這個家族的持倉」角度，用繁體中文判斷：
1. summary_zh：一句話中文摘要（20字內）
2. impact：只能是「利多」「利空」「中性」「無影響」其一
3. reason_zh：一句話說明為什麼對我們有／沒有影響（30字內，站在債券持有人／FCN持有人角度）
4. alert：布林值。只有在可能影響債息安全的重大信用事件（違約、降評、重大訴訟賠償、破產疑慮），或 FCN 連結股票暴跌時才是 true

最後給 overall_zh：一句話總結今天新聞對整體投資組合的影響（40字內）。

只輸出 JSON，不要任何其他文字，格式：
{{"items":[{{"index":0,"summary_zh":"...","impact":"...","reason_zh":"...","alert":false}}],"overall_zh":"..."}}"""


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1 :]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


def analyze_news(
    news: list[NewsItem],
    *,
    api_key: str,
    model: str = DEFAULT_MODEL,
    context: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[list[NewsItem], str | None, list[str]]:
    """Return (enriched items, overall_zh, exceptions).

    On any failure the original `news` list is returned with overall None.
    """
    if not news:
        return news, None, []

    payload_items = [
        {"index": i, "issuer": n.issuer_id, "headline": n.summary, "source": n.source}
        for i, n in enumerate(news)
    ]
    body = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROMPT.format(context=context or DEFAULT_CONTEXT),
        "messages": [
            {
                "role": "user",
                "content": json.dumps(payload_items, ensure_ascii=False),
            }
        ],
    }

    try:
        resp = requests.post(
            API_URL,
            json=body,
            headers={
                "x-api-key": api_key,
                "anthropic-version": API_VERSION,
                "content-type": "application/json",
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["content"][0]["text"]
        parsed = json.loads(_strip_fences(text))
        raw_items = parsed["items"]
        overall = parsed.get("overall_zh") or None
    except (
        requests.RequestException,
        json.JSONDecodeError,
        KeyError,
        IndexError,
        TypeError,
        ValueError,
    ) as e:
        log.warning("llm: analysis failed: %s", type(e).__name__)
        return news, None, [f"llm: {type(e).__name__}"]

    by_index: dict[int, dict] = {}
    for item in raw_items:
        if isinstance(item, dict) and isinstance(item.get("index"), int):
            by_index[item["index"]] = item

    enriched: list[NewsItem] = []
    for i, n in enumerate(news):
        item = by_index.get(i)
        if not item:
            enriched.append(n)
            continue
        impact = str(item.get("impact") or "").strip()
        if impact not in VALID_IMPACTS:
            impact = "中性"
        summary_zh = str(item.get("summary_zh") or "").strip() or None
        reason_zh = str(item.get("reason_zh") or "").strip() or None
        enriched.append(
            dataclasses.replace(
                n,
                summary_zh=summary_zh,
                impact=impact,
                impact_reason=reason_zh,
                is_alert=n.is_alert or bool(item.get("alert")),
            )
        )
    return enriched, overall, []


# ---------------------------------------------------------------------------
# M11 — Daily morning greeting (早安 + 勉勵 + 笑話)

GREETING_MAX_TOKENS = 400
WEEKDAYS_ZH = ["一", "二", "三", "四", "五", "六", "日"]

GREETING_SYSTEM_PROMPT = """你是一個溫暖的家族投資群組小助理。請產生今天的早安問候，繁體中文，格式恰好三行：
第1行：跟大家說早安（提到今天星期幾），加上溫暖的表情符號 ☀️🌱💪🧡 之類
第2行：一句勉勵大家工作與生活的話（真誠不老套，可以呼應星期幾，例如週一打氣、週五快週末）
第3行：以「😄 今日笑話：」開頭，講一個簡短好笑的笑話（可以是冷笑話或諧音梗，避免太常見的老梗，不要嘲諷特定族群）

只輸出這三行文字，不要其他說明。"""

# Rotating offline greetings — used when no API key is set or the API call
# fails. Picked deterministically by date so the family still gets a fresh
# line each day without any network dependency.
FALLBACK_GREETINGS = [
    "☀️ 早安！新的一天，大家辛苦了 🧡\n今天也請帶著從容的心情，好好工作、好好生活 💪\n😄 今日笑話：為什麼債券最守信用？因為它每半年都準時「付出真心（息）」。",
    "🌤️ 早安呀！願大家今天順順利利 🌱\n工作再忙，也記得抬頭喝口水、深呼吸一下 🧡\n😄 今日笑話：老闆問我為什麼上班在笑，我說我在複利——快樂也會複利的！",
    "☀️ 早安！今天也是值得期待的一天 💪\n穩穩的，就像我們的配息一樣，慢慢累積就很可觀 🧡\n😄 今日笑話：存款對我說：你不理財，財不理你。我說：可是我很理你啊，你都不變多。",
    "🌞 早安，親愛的家人們 🧡\n照顧好自己，就是最好的長期投資 🌱\n😄 今日笑話：為什麼匯率最會演戲？因為它每天都在「升」「貶」不定。",
    "☀️ 早安！又是充滿希望的一天 💪\n不論今天遇到什麼，家人永遠是彼此的靠山 🧡\n😄 今日笑話：我問理專睡前都做什麼？他說：數息（利息）啊，比數羊有效多了。",
    "🌅 早安！祝大家今天心情像升息的定存一樣穩穩向上 🧡\n累的時候休息一下沒關係，我們走的是長期路線 🌱\n😄 今日笑話：為什麼 FCN 最有禮貌？因為它每個月都準時來「打招呼（配息）」。",
    "☀️ 週末愉快，早安 🧡\n好好休息、陪陪家人，充飽電再出發 🌱\n😄 今日笑話：投資人最喜歡的天氣？「牛」毛細雨。",
]


def fallback_greeting(today: "Date") -> str:
    """Deterministic offline greeting — same date, same text."""
    return FALLBACK_GREETINGS[today.toordinal() % len(FALLBACK_GREETINGS)]


def generate_greeting(
    today: "Date",
    *,
    api_key: str,
    model: str = DEFAULT_MODEL,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[str, list[str]]:
    """Return (greeting text, exceptions).

    Always returns usable text: any failure falls back to the offline
    rotation and reports one exception string.
    """
    weekday = WEEKDAYS_ZH[today.weekday()]
    body = {
        "model": model,
        "max_tokens": GREETING_MAX_TOKENS,
        "system": GREETING_SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": f"今天是 {today.isoformat()}，星期{weekday}。",
            }
        ],
    }
    try:
        resp = requests.post(
            API_URL,
            json=body,
            headers={
                "x-api-key": api_key,
                "anthropic-version": API_VERSION,
                "content-type": "application/json",
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        text = resp.json()["content"][0]["text"].strip()
        if not text:
            raise ValueError("empty greeting")
        return text, []
    except (
        requests.RequestException,
        json.JSONDecodeError,
        KeyError,
        IndexError,
        TypeError,
        ValueError,
    ) as e:
        log.warning("llm: greeting failed: %s", type(e).__name__)
        return fallback_greeting(today), [f"llm greeting: {type(e).__name__}"]
