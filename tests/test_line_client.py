import requests

from src.line_client import LINE_TEXT_LIMIT, push_message

PUSH_URL = "https://api.line.me/v2/bot/message/push"


def test_happy_path_returns_true(requests_mock):
    requests_mock.post(PUSH_URL, status_code=200, json={})
    ok, err = push_message(text="hello", group_id="C123", access_token="tok")
    assert ok is True
    assert err is None


def test_authorization_header_is_bearer_token(requests_mock):
    requests_mock.post(PUSH_URL, status_code=200, json={})
    push_message(text="hi", group_id="C1", access_token="my-secret-token")
    assert requests_mock.last_request.headers["Authorization"] == "Bearer my-secret-token"


def test_request_body_has_correct_shape(requests_mock):
    requests_mock.post(PUSH_URL, status_code=200, json={})
    push_message(text="hi", group_id="GROUP-X", access_token="t")
    body = requests_mock.last_request.json()
    assert body == {"to": "GROUP-X", "messages": [{"type": "text", "text": "hi"}]}


def test_401_returns_failure_with_line_error_message(requests_mock):
    requests_mock.post(
        PUSH_URL,
        status_code=401,
        json={"message": "Authentication failed"},
    )
    ok, err = push_message(text="hi", group_id="C1", access_token="bad")
    assert ok is False
    assert "401" in err
    assert "Authentication failed" in err


def test_500_returns_failure_with_status(requests_mock):
    requests_mock.post(PUSH_URL, status_code=500, text="Internal Server Error")
    ok, err = push_message(text="hi", group_id="C1", access_token="t")
    assert ok is False
    assert "500" in err


def test_network_error_returns_failure_without_status(requests_mock):
    requests_mock.post(PUSH_URL, exc=requests.exceptions.ConnectTimeout)
    ok, err = push_message(text="hi", group_id="C1", access_token="t")
    assert ok is False
    assert "network" in err.lower()


def test_message_over_limit_rejected_without_network_call(requests_mock):
    # Deliberately no mock registration — if the function tried to send,
    # requests-mock would raise NoMockAddress. We verify nothing is sent.
    long_text = "x" * (LINE_TEXT_LIMIT + 1)
    ok, err = push_message(text=long_text, group_id="C1", access_token="t")
    assert ok is False
    assert "too long" in err
    assert requests_mock.request_history == []


def test_error_message_does_not_leak_token_or_group_id(requests_mock):
    requests_mock.post(PUSH_URL, status_code=403, json={"message": "Forbidden"})
    ok, err = push_message(
        text="hi", group_id="C-supersecret", access_token="tok-supersecret"
    )
    assert ok is False
    assert "supersecret" not in err
