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
# Headroom for the per-item JSON plus a 150-250 character overall paragraph.
MAX_TOKENS = 2500

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

最後給 overall_zh：把今天所有新聞整合成「一整段」繁體中文短文（150–250字），這是家族群組唯一會讀到的新聞內容，所以要能獨立看懂：
- 依重要性排序敘述，重要的先講，不重要的一句帶過或直接省略
- 寫成連貫的段落，不要條列、不要編號、不要分行
- 直接寫結論與對我們持倉的實質影響，不要逐則複述英文標題、不要附網址或出處
- 若當天新聞對我們都沒有實質影響，就直接說明沒有重大事項，不必硬湊字數

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

GREETING_SYSTEM_PROMPT = """你是一個家族投資群組的小助理。請產生今天的早安問候，繁體中文，格式恰好三行：
第1行：跟大家說早安（提到今天星期幾），語氣自然、簡短，可以帶一兩個溫暖的表情符號
第2行：使用者會給你「今日名言」。請原封不動輸出那一整行（含「——作者」），不要改字、不要換成別的名言、不要加上你自己的註解
第3行：以「😄 今日笑話：」開頭，講一個簡短的笑話（可以是冷笑話或諧音梗，避免太常見的老梗，不要嘲諷特定族群）

只輸出這三行文字，不要其他說明。不要寫「加油」「你可以的」「充滿希望的一天」這類空泛口號。"""

# Curated quotes with real attribution — deliberately understated rather than
# motivational-poster material. Rotated by date; the same list feeds both the
# LLM path (injected verbatim, so attribution can't be hallucinated) and the
# offline fallback.
QUOTES = [
    "「你能掌控的是自己的心智，而不是外在的事件；認清這一點，你就會找到力量。」——馬可・奧理略《沉思錄》",
    "「別再爭論一個好人該是什麼樣子，去成為一個。」——馬可・奧理略《沉思錄》",
    "「我們聽到的一切都是意見，不是事實；我們看到的一切都是視角，不是真相。」——馬可・奧理略《沉思錄》",
    "「我們在想像中受的苦，往往多過在現實中受的苦。」——塞內卡",
    "「知道自己為何而活的人，幾乎能忍受任何一種生活。」——尼采",
    "「在隆冬，我終於知道，我身上有一個不可戰勝的夏天。」——卡繆",
    "「想像力比知識更重要。」——愛因斯坦",
    "「面對陽光，陰影就會落在你身後。」——海倫・凱勒",
    "「走得最慢的人，只要不迷失方向，也比漫無目的地徘徊的人走得快。」——萊辛",
    "「我不怕練過一萬種踢法的人，我怕的是把一種踢法練了一萬次的人。」——李小龍",
    "「盛年不重來，一日難再晨。及時當勉勵，歲月不待人。」——陶淵明〈雜詩〉",
    "「三軍可奪帥也，匹夫不可奪志也。」——《論語・子罕》",
    "「富貴不能淫，貧賤不能移，威武不能屈。」——《孟子・滕文公下》",
    "「不積跬步，無以至千里；不積小流，無以成江海。」——《荀子・勸學》",
    "「人間有味是清歡。」——蘇軾〈浣溪沙〉",
    "「行到水窮處，坐看雲起時。」——王維〈終南別業〉",
    "「千磨萬擊還堅勁，任爾東西南北風。」——鄭燮〈竹石〉",
    "「怕什麼真理無窮，進一寸有一寸的歡喜。」——胡適",
    "「讀書好比串門兒——隱身的串門兒。」——楊絳〈讀書苦樂〉",
    "「希望是附麗於存在的，有存在，便有希望，有希望，便是光明。」——魯迅",
    "「當你穿過了暴風雨，你已不再是走進暴風雨時的那個人。」——村上春樹《海邊的卡夫卡》",
    "「賺大錢的訣竅不在於買進賣出，而在於等待。」——查理・蒙格",
    "「成功的投資需要時間、紀律與耐心。」——華倫・巴菲特",
    "「建立聲譽需要二十年，毀掉它只要五分鐘。」——華倫・巴菲特",
]

# Openers and jokes rotate on their own cycles. Lengths are coprime with
# len(QUOTES) so the three parts recombine for months before repeating.
GREETING_OPENERS = [
    "☀️ 早安，星期{w}。新的一天開始了 🧡",
    "🌤️ 早安，星期{w}。今天也好好過 🌱",
    "🌞 星期{w}，早安 🧡",
    "☀️ 早安，星期{w}了 🌱",
    "🌅 早安，星期{w}的早晨 🧡",
]

GREETING_JOKES = [
    "😄 今日笑話：為什麼債券最守信用？因為它每半年都準時「付出真心（息）」。",
    "😄 今日笑話：老闆問我為什麼上班在笑，我說我在複利——快樂也會複利的！",
    "😄 今日笑話：存款對我說：你不理財，財不理你。我說：可是我很理你啊，你都不變多。",
    "😄 今日笑話：為什麼匯率最會演戲？因為它每天都在「升」「貶」不定。",
    "😄 今日笑話：我問理專睡前都做什麼？他說：數息（利息）啊，比數羊有效多了。",
    "😄 今日笑話：為什麼 FCN 最有禮貌？因為它每個月都準時來「打招呼（配息）」。",
    "😄 今日笑話：投資人最喜歡的天氣？「牛」毛細雨。",
]


def quote_of_the_day(today: "Date") -> str:
    """Deterministic pick from QUOTES — same date, same quote."""
    return QUOTES[today.toordinal() % len(QUOTES)]


def fallback_greeting(today: "Date") -> str:
    """Deterministic offline greeting — same date, same text."""
    ordinal = today.toordinal()
    opener = GREETING_OPENERS[ordinal % len(GREETING_OPENERS)]
    joke = GREETING_JOKES[ordinal % len(GREETING_JOKES)]
    return "\n".join(
        [
            opener.format(w=WEEKDAYS_ZH[today.weekday()]),
            quote_of_the_day(today),
            joke,
        ]
    )


def _ensure_quote(text: str, quote: str) -> str:
    """Splice the day's quote back in if the model paraphrased or dropped it.

    The quote is the one part we don't want the model improvising on — a
    misattributed 名言 is worse than no 名言 — so this is a cheap guarantee
    rather than a reason to discard an otherwise good greeting.
    """
    if quote in text:
        return text
    lines = [ln for ln in text.splitlines() if ln.strip()]
    lines.insert(1 if len(lines) >= 2 else len(lines), quote)
    return "\n".join(lines)


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
    quote = quote_of_the_day(today)
    body = {
        "model": model,
        "max_tokens": GREETING_MAX_TOKENS,
        "system": GREETING_SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": f"今天是 {today.isoformat()}，星期{weekday}。\n今日名言：{quote}",
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
        return _ensure_quote(text, quote), []
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
