"""Thin wrapper around the LINE Messaging API push-message endpoint."""
from __future__ import annotations

import requests

DEFAULT_BASE_URL = "https://api.line.me"
PUSH_PATH = "/v2/bot/message/push"
DEFAULT_TIMEOUT = 10.0
LINE_TEXT_LIMIT = 5000  # LINE's per-text-message character cap


def _redact_secrets(text: object, *secrets: str) -> str:
    """Remove caller-provided credentials and identifiers from error details."""
    text = str(text)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return text


def push_message(
    *,
    text: str,
    group_id: str,
    access_token: str,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[bool, str | None]:
    """POST a single text message to a LINE group.

    Returns (True, None) on success, (False, error_string) on failure.
    The error string never includes the access token or group id.
    """
    if len(text) > LINE_TEXT_LIMIT:
        return False, f"message too long ({len(text)} > {LINE_TEXT_LIMIT} chars)"

    payload = {"to": group_id, "messages": [{"type": "text", "text": text}]}
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(
            f"{base_url}{PUSH_PATH}",
            json=payload,
            headers=headers,
            timeout=timeout,
        )
    except requests.RequestException as e:
        return False, f"network error: {type(e).__name__}"

    if resp.status_code == 200:
        return True, None

    detail = ""
    try:
        body = resp.json()
        if isinstance(body, dict):
            detail = body.get("message", "") or ""
    except ValueError:
        detail = resp.text or ""
    detail = _redact_secrets(detail, access_token, group_id)[:200]
    return False, f"HTTP {resp.status_code}: {detail}".rstrip(": ")
