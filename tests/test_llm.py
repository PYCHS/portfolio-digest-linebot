import json

import pytest

from src.llm import API_URL, analyze_news
from src.models import NewsItem

ITEMS = [
    NewsItem("Pfizer", "Pfizer posts trial win", "seekingalpha.com"),
    NewsItem("Google", "Alphabet fined in EU", "reuters.com"),
]


def _ok_response():
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "items": [
                            {
                                "index": 0,
                                "summary_zh": "輝瑞新藥試驗成功",
                                "impact": "利多",
                                "reason_zh": "營收改善有利償債能力",
                                "alert": False,
                            },
                            {
                                "index": 1,
                                "summary_zh": "Alphabet 遭歐盟罰款",
                                "impact": "利空",
                                "reason_zh": "罰款金額對信用影響有限",
                                "alert": False,
                            },
                        ],
                        "overall_zh": "今日新聞對債息安全無實質影響。",
                    },
                    ensure_ascii=False,
                ),
            }
        ]
    }


def test_success_maps_analysis_onto_items(requests_mock):
    requests_mock.post(API_URL, json=_ok_response())
    out, overall, exc = analyze_news(ITEMS, api_key="k")
    assert exc == []
    assert overall == "今日新聞對債息安全無實質影響。"
    assert out[0].summary_zh == "輝瑞新藥試驗成功"
    assert out[0].impact == "利多"
    assert out[1].impact == "利空"
    # Original fields survive
    assert out[0].summary == "Pfizer posts trial win"
    assert not out[0].is_alert


def test_alert_flag_from_llm_sets_is_alert(requests_mock):
    body = _ok_response()
    payload = json.loads(body["content"][0]["text"])
    payload["items"][1]["alert"] = True
    body["content"][0]["text"] = json.dumps(payload, ensure_ascii=False)
    requests_mock.post(API_URL, json=body)
    out, _, _ = analyze_news(ITEMS, api_key="k")
    assert out[1].is_alert


def test_string_false_alert_does_not_trigger_false_alarm(requests_mock):
    body = _ok_response()
    payload = json.loads(body["content"][0]["text"])
    payload["items"][1]["alert"] = "false"
    body["content"][0]["text"] = json.dumps(payload, ensure_ascii=False)
    requests_mock.post(API_URL, json=body)

    out, _, _ = analyze_news(ITEMS, api_key="k")

    assert out[1].is_alert is False


def test_code_fenced_json_is_tolerated(requests_mock):
    body = _ok_response()
    body["content"][0]["text"] = "```json\n" + body["content"][0]["text"] + "\n```"
    requests_mock.post(API_URL, json=body)
    out, overall, exc = analyze_news(ITEMS, api_key="k")
    assert exc == []
    assert out[0].impact == "利多"


def test_invalid_impact_falls_back_to_neutral(requests_mock):
    body = _ok_response()
    payload = json.loads(body["content"][0]["text"])
    payload["items"][0]["impact"] = "超級大利多"
    body["content"][0]["text"] = json.dumps(payload, ensure_ascii=False)
    requests_mock.post(API_URL, json=body)
    out, _, _ = analyze_news(ITEMS, api_key="k")
    assert out[0].impact == "中性"


def test_api_error_returns_originals_with_exception(requests_mock):
    requests_mock.post(API_URL, status_code=500, json={"error": "boom"})
    out, overall, exc = analyze_news(ITEMS, api_key="k")
    assert out == ITEMS
    assert overall is None
    assert len(exc) == 1 and exc[0].startswith("llm:")


def test_garbage_model_output_returns_originals(requests_mock):
    requests_mock.post(
        API_URL, json={"content": [{"type": "text", "text": "not json at all"}]}
    )
    out, overall, exc = analyze_news(ITEMS, api_key="k")
    assert out == ITEMS
    assert overall is None
    assert len(exc) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [("items", None), ("overall_zh", ["not", "a", "paragraph"])],
)
def test_wrong_output_types_return_originals(requests_mock, field, value):
    body = _ok_response()
    payload = json.loads(body["content"][0]["text"])
    payload[field] = value
    body["content"][0]["text"] = json.dumps(payload, ensure_ascii=False)
    requests_mock.post(API_URL, json=body)

    out, overall, exc = analyze_news(ITEMS, api_key="k")

    assert out == ITEMS
    assert overall is None
    assert exc == ["llm: TypeError"]


def test_empty_news_short_circuits_without_network(requests_mock):
    out, overall, exc = analyze_news([], api_key="k")
    assert out == [] and overall is None and exc == []
    assert not requests_mock.request_history


def test_sends_traditional_chinese_system_prompt(requests_mock):
    requests_mock.post(API_URL, json=_ok_response())
    analyze_news(ITEMS, api_key="k", context="家族持有輝瑞公司債")
    body = requests_mock.request_history[0].json()
    assert "家族持有輝瑞公司債" in body["system"]
    assert body["model"]
    sent_items = json.loads(body["messages"][0]["content"])
    assert sent_items[0]["headline"] == "Pfizer posts trial win"


# ---- M11 greeting ----

from datetime import date

from datetime import timedelta

from src.llm import (
    GREETING_JOKES,
    QUOTES,
    fallback_greeting,
    generate_greeting,
    quote_of_the_day,
)


def test_fallback_greeting_is_deterministic_and_well_formed():
    d1 = date(2026, 8, 4)  # Tuesday
    assert fallback_greeting(d1) == fallback_greeting(d1)
    lines = fallback_greeting(d1).splitlines()
    assert len(lines) == 3
    assert "早安" in lines[0] and "星期二" in lines[0]
    assert lines[1] == quote_of_the_day(d1)
    assert lines[2] in GREETING_JOKES


def test_quotes_rotate_over_the_full_list():
    start = date(2026, 8, 4)
    picked = {quote_of_the_day(start + timedelta(days=i)) for i in range(len(QUOTES))}
    assert picked == set(QUOTES)


def test_quotes_all_carry_an_attribution():
    # A quote without a source is exactly the 心靈雞湯 this list exists to avoid.
    assert all(q.startswith("「") and "」——" in q for q in QUOTES)


def test_greeting_parts_recombine_rather_than_repeating_as_a_block():
    start = date(2026, 8, 4)
    greetings = {fallback_greeting(start + timedelta(days=i)) for i in range(60)}
    assert len(greetings) == 60


def test_generate_greeting_injects_the_quote_and_keeps_model_text(requests_mock):
    today = date(2026, 8, 4)
    quote = quote_of_the_day(today)
    requests_mock.post(
        API_URL,
        json={
            "content": [
                {"type": "text", "text": f"☀️ 早安，星期二 🧡\n{quote}\n😄 今日笑話：..."}
            ]
        },
    )
    text, exc = generate_greeting(today, api_key="k")
    assert exc == []
    assert text.startswith("☀️ 早安")
    assert quote in text
    body = requests_mock.request_history[0].json()
    content = body["messages"][0]["content"]
    assert "星期二" in content  # 2026-08-04 is a Tuesday
    assert quote in content  # the model is told which quote to use


def test_generate_greeting_splices_quote_back_when_model_drops_it(requests_mock):
    today = date(2026, 8, 4)
    requests_mock.post(
        API_URL,
        json={"content": [{"type": "text", "text": "☀️ 早安！\n加油！\n😄 今日笑話：..."}]},
    )
    text, exc = generate_greeting(today, api_key="k")
    assert exc == []
    assert quote_of_the_day(today) in text
    assert text.splitlines()[1] == quote_of_the_day(today)


def test_generate_greeting_api_error_falls_back(requests_mock):
    requests_mock.post(API_URL, status_code=429, json={"error": "rate"})
    today = date(2026, 8, 4)
    text, exc = generate_greeting(today, api_key="k")
    assert text == fallback_greeting(today)
    assert len(exc) == 1 and exc[0].startswith("llm greeting:")
