"""Export and import chats as Markdown or JSON.

JSON is the round-trippable format (it carries roles, token counts, timestamps and
per-chat overrides); Markdown is a human-readable, one-way export.
"""

from __future__ import annotations

import json
from pathlib import Path

JSON_VERSION = 1

_ROLE_HEADING = {
    "user": "You",
    "assistant": "Assistant",
    "system": "System",
}


def _chat_to_dict(db, chat_id: int) -> dict:
    chat = db.get_chat(chat_id)
    if chat is None:
        raise ValueError(f"chat {chat_id} not found")
    keys = chat.keys()

    def field(name):
        return chat[name] if name in keys else None

    messages = [
        {
            "role": m["role"],
            "content": m["content"],
            "prompt_tokens": m["prompt_tokens"] if "prompt_tokens" in m.keys() else 0,
            "completion_tokens": (
                m["completion_tokens"] if "completion_tokens" in m.keys() else 0
            ),
            "created_at": m["created_at"] if "created_at" in m.keys() else None,
        }
        for m in db.get_messages(chat_id)
    ]
    return {
        "version": JSON_VERSION,
        "title": chat["title"],
        "created_at": field("created_at"),
        "model": field("model"),
        "system_prompt": field("system_prompt"),
        "temperature": field("temperature"),
        "messages": messages,
    }


def export_json(db, chat_id: int, path: str | Path) -> None:
    data = _chat_to_dict(db, chat_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def export_markdown(db, chat_id: int, path: str | Path) -> None:
    data = _chat_to_dict(db, chat_id)
    lines = [f"# {data['title']}", ""]
    if data.get("created_at"):
        lines.append(f"*Created: {data['created_at']}*")
        lines.append("")
    for m in data["messages"]:
        lines.append(f"## {_ROLE_HEADING.get(m['role'], m['role'].title())}")
        lines.append("")
        lines.append(m["content"])
        lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")


def import_json(db, path: str | Path) -> int:
    """Create a new chat from a PyQOA JSON export. Returns the new chat id."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
        raise ValueError("Not a valid PyQOA chat export (missing 'messages' list).")

    title = str(data.get("title") or "Imported Chat")
    chat_id = db.create_chat(title)

    model = data.get("model")
    system_prompt = data.get("system_prompt")
    temperature = data.get("temperature")
    if model or system_prompt or temperature is not None:
        db.update_chat_overrides(chat_id, model, system_prompt, temperature)

    for m in data["messages"]:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        if role not in ("user", "assistant", "system") or content is None:
            continue
        db.add_message(
            chat_id,
            role,
            str(content),
            int(m.get("prompt_tokens") or 0),
            int(m.get("completion_tokens") or 0),
        )
    return chat_id
