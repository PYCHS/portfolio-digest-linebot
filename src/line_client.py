"""Thin wrapper around the LINE Messaging API push-message endpoint."""
from __future__ import annotations

import time
from uuid import uuid4

import requests

DEFAULT_BASE_URL = "https://api.line.me"
PUSH_PATH = "/v2/bot/message/push"
DEFAULT_TIMEOUT = 10.0
LINE_TEXT_LIMIT = 5000  # LINE's per-text-message character cap
LINE_MESSAGE_LIMIT = 5  # LINE's per-push message-object cap
MAX_ATTEMPTS = 2
RETRY_BACKOFF_SEC = 0.5


def _redact_secrets(text: object, *secrets: str) -> str:
    """Remove caller-provided credentials and identifiers from error details."""
    text = str(text)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return text


def _split_text(text: str) -> list[str]:
    """Split a digest into LINE-sized messages without dropping characters.

    Prefer a newline boundary so section headings and list items remain
    readable. A single overlong line is hard-split as a last resort.
    """
    chunks: list[str] = []
    while len(text) > LINE_TEXT_LIMIT:
        parts_needed = (len(text) + LINE_TEXT_LIMIT - 1) // LINE_TEXT_LIMIT
        min_cut = len(text) - (parts_needed - 1) * LINE_TEXT_LIMIT
        newline = text.rfind("\n", max(0, min_cut - 1), LINE_TEXT_LIMIT)
        if newline < 0:
            cut = LINE_TEXT_LIMIT
        else:
            cut = newline + 1  # Keep it so joining chunks reproduces the input.
        chunks.append(text[:cut])
        text = text[cut:]
    chunks.append(text)
    return chunks


def push_message(
    *,
    text: str,
    group_id: str,
    access_token: str,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[bool, str | None]:
    """POST one digest to a LINE group, split into at most five messages.

    Returns (True, None) on success, (False, error_string) on failure.
    The error string never includes the access token or group id.
    """
    text_chunks = _split_text(text)
    if len(text_chunks) > LINE_MESSAGE_LIMIT:
        return False, (
            f"message too long ({len(text)} chars needs {len(text_chunks)} parts; "
            f"max {LINE_MESSAGE_LIMIT})"
        )

    payload = {
        "to": group_id,
        "messages": [{"type": "text", "text": chunk} for chunk in text_chunks],
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        # LINE uses this UUID to make retries idempotent. Generate it before
        # the first request and reuse it for every attempt in this call.
        "X-Line-Retry-Key": str(uuid4()),
    }

    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = requests.post(
                f"{base_url}{PUSH_PATH}",
                json=payload,
                headers=headers,
                timeout=timeout,
            )
        except requests.RequestException as e:
            if attempt + 1 < MAX_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_SEC * (2**attempt))
                continue
            return False, f"network error: {type(e).__name__}"

        if resp.status_code == 200:
            return True, None
        # A 409 with this header means LINE accepted an earlier attempt with
        # the same retry key, so the delivery should be treated as successful.
        if resp.status_code == 409 and resp.headers.get(
            "X-Line-Accepted-Request-Id"
        ):
            return True, None
        if 500 <= resp.status_code < 600 and attempt + 1 < MAX_ATTEMPTS:
            time.sleep(RETRY_BACKOFF_SEC * (2**attempt))
            continue
        break

    detail = ""
    try:
        body = resp.json()
        if isinstance(body, dict):
            detail = body.get("message", "") or ""
    except ValueError:
        detail = resp.text or ""
    detail = _redact_secrets(detail, access_token, group_id)[:200]
    return False, f"HTTP {resp.status_code}: {detail}".rstrip(": ")
