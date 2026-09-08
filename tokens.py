"""Token counting.

Uses `tiktoken` when it is installed *and* its encoding files are reachable;
otherwise falls back to a character heuristic. Counting must never be the reason
a message fails to send, so every failure degrades silently to the estimate.
"""

from __future__ import annotations

try:
    import tiktoken
    _TIKTOKEN_IMPORTED = True
except Exception:
    tiktoken = None
    _TIKTOKEN_IMPORTED = False

# ~4 characters per token is the usual rule of thumb for English prose.
_CHARS_PER_TOKEN = 4

# Rough per-message framing overhead (role marker + separators), as used by
# OpenAI's own cookbook for chat models.
_PER_MESSAGE_OVERHEAD = 4

_encoders: dict[str, object] = {}
_failed: set[str] = set()


def exact_counting_available() -> bool:
    return _TIKTOKEN_IMPORTED


def _encoder(model: str):
    """Return a tiktoken encoder for `model`, or None if unavailable."""
    if not _TIKTOKEN_IMPORTED:
        return None
    key = (model or "").strip() or "cl100k_base"
    if key in _failed:
        return None
    if key in _encoders:
        return _encoders[key]
    enc = None
    try:
        enc = tiktoken.encoding_for_model(key)
    except Exception:
        try:
            enc = tiktoken.get_encoding("cl100k_base")
        except Exception:
            enc = None
    if enc is None:
        _failed.add(key)
        return None
    _encoders[key] = enc
    return enc


def count_tokens(text: str, model: str = "") -> int:
    if not text:
        return 0
    enc = _encoder(model)
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    return max(1, (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN)


def count_message(message: dict, model: str = "") -> int:
    """Tokens for one chat message, including its framing overhead.

    Handles both plain string content and the multi-part content arrays used for
    attachments; image parts are charged a flat estimate.
    """
    content = message.get("content")
    total = _PER_MESSAGE_OVERHEAD + count_tokens(message.get("role", ""), model)
    if isinstance(content, str):
        total += count_tokens(content, model)
    elif isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                total += count_tokens(part.get("text", ""), model)
            else:
                total += 800  # flat estimate for an image part
    return total


def count_messages(messages: list[dict], model: str = "") -> int:
    return sum(count_message(m, model) for m in messages) + 2
