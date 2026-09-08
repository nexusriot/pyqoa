"""Export and import chats.

JSON is the round-trippable format (roles, token counts, timestamps, per-chat
overrides and attachments); Markdown, HTML and PDF are human-readable one-way
exports. `export_all` and `backup` cover the whole database at once.
"""

from __future__ import annotations

import base64
import json
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QMarginsF, QSizeF
from PyQt6.QtGui import QPageSize, QPdfWriter, QTextDocument

import attachments
import utils

JSON_VERSION = 2

_ROLE_HEADING = {
    "user": "You",
    "assistant": "Assistant",
    "system": "System",
}


def safe_filename(title: str, fallback: str = "chat") -> str:
    """Sanitise a chat title for use as a file name."""
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in title or "")
    safe = " ".join(safe.split()).strip()
    return (safe or fallback)[:80]


def _chat_to_dict(db, chat_id: int, include_attachments: bool = True) -> dict:
    chat = db.get_chat(chat_id)
    if chat is None:
        raise ValueError(f"chat {chat_id} not found")
    keys = chat.keys()

    def field(name):
        return chat[name] if name in keys else None

    messages = []
    for m in db.get_messages(chat_id):
        mkeys = m.keys()
        entry = {
            "role": m["role"],
            "content": m["content"],
            "prompt_tokens": m["prompt_tokens"] if "prompt_tokens" in mkeys else 0,
            "completion_tokens": (
                m["completion_tokens"] if "completion_tokens" in mkeys else 0
            ),
            "created_at": m["created_at"] if "created_at" in mkeys else None,
            "model": m["model"] if "model" in mkeys else None,
        }
        if include_attachments:
            atts = [
                {
                    "kind": a["kind"],
                    "name": a["name"],
                    "mime": a["mime"],
                    "data": base64.b64encode(a["data"] or b"").decode("ascii"),
                }
                for a in db.get_attachments(m["id"])
            ]
            if atts:
                entry["attachments"] = atts
        messages.append(entry)

    return {
        "version": JSON_VERSION,
        "title": chat["title"],
        "created_at": field("created_at"),
        "model": field("model"),
        "system_prompt": field("system_prompt"),
        "temperature": field("temperature"),
        "folder": field("folder"),
        "messages": messages,
    }


def export_json(db, chat_id: int, path: str | Path) -> None:
    data = _chat_to_dict(db, chat_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def export_markdown(db, chat_id: int, path: str | Path) -> None:
    data = _chat_to_dict(db, chat_id, include_attachments=False)
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


_HTML_CSS = """
body{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
     max-width:820px;margin:2rem auto;padding:0 1.2rem;line-height:1.55;
     color:#1c1e21;background:#fff;}
h1{font-size:1.6rem;border-bottom:1px solid #e3e5e8;padding-bottom:.5rem;}
.msg{border:1px solid #e3e5e8;border-radius:10px;padding:.9rem 1.1rem;
     margin:1rem 0;background:#fafbfc;}
.msg.user{background:#eef4ff;border-color:#cfdcf7;}
.role{font-size:.75rem;text-transform:uppercase;letter-spacing:.06em;
      color:#6a7280;font-weight:700;margin-bottom:.4rem;}
.meta{color:#8b929c;font-size:.75rem;margin-top:.6rem;}
.codeblock{border:1px solid #e0e3e8;border-radius:8px;margin:.7rem 0;
     background:#f8f9fb;overflow:hidden;}
.codeblock .lang{display:block;padding:.3rem .8rem;background:#eef0f4;
     font-size:.7rem;text-transform:uppercase;letter-spacing:.06em;color:#6a7280;}
pre{overflow-x:auto;border-radius:8px;padding:.8rem;background:transparent;
    margin:0;white-space:pre-wrap;word-break:break-word;}
code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.88em;}
table{border-collapse:collapse;}td,th{border:1px solid #d7dae0;padding:.35rem .6rem;}
"""


def chat_to_html(db, chat_id: int) -> str:
    """Render a whole chat as a standalone HTML document."""
    data = _chat_to_dict(db, chat_id, include_attachments=False)
    parts = [
        "<!DOCTYPE html>", '<html lang="en">', "<head>",
        '<meta charset="utf-8">',
        f"<title>{utils.escape_html(data['title'])}</title>",
        f"<style>{_HTML_CSS}</style>", "</head>", "<body>",
        f"<h1>{utils.escape_html(data['title'])}</h1>",
    ]
    if data.get("created_at"):
        parts.append(
            f"<p class='meta'>Created {utils.escape_html(str(data['created_at']))}</p>"
        )
    for m in data["messages"]:
        role = m["role"]
        heading = _ROLE_HEADING.get(role, role.title())
        body = utils.text_to_html(m["content"], for_export=True)
        meta = ""
        if m.get("prompt_tokens") or m.get("completion_tokens"):
            meta = (
                f"<div class='meta'>{m.get('model') or ''} · "
                f"{m.get('prompt_tokens', 0)} prompt + "
                f"{m.get('completion_tokens', 0)} completion tokens</div>"
            )
        parts.append(
            f"<div class='msg {role}'><div class='role'>{heading}</div>"
            f"{body}{meta}</div>"
        )
    parts.extend(["</body>", "</html>"])
    return "\n".join(parts)


def export_html(db, chat_id: int, path: str | Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(chat_to_html(db, chat_id))


def export_pdf(db, chat_id: int, path: str | Path) -> None:
    """Render the HTML export through Qt's print pipeline into a PDF."""
    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageMargins(QMarginsF(15, 15, 15, 15))
    writer.setResolution(96)

    doc = QTextDocument()
    doc.setHtml(chat_to_html(db, chat_id))
    doc.setPageSize(QSizeF(writer.width(), writer.height()))
    doc.print(writer)


EXPORTERS = {
    "md": (export_markdown, ".md", "Markdown (*.md)"),
    "json": (export_json, ".json", "JSON (*.json)"),
    "html": (export_html, ".html", "HTML (*.html)"),
    "pdf": (export_pdf, ".pdf", "PDF (*.pdf)"),
}


def export_all(db, directory: str | Path, fmt: str = "json") -> list[Path]:
    """Export every chat into `directory`, one file per chat."""
    if fmt not in EXPORTERS:
        raise ValueError(f"unknown export format '{fmt}'")
    fn, suffix, _ = EXPORTERS[fmt]
    out_dir = Path(directory)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for chat in db.get_chats(include_archived=True):
        name = f"{chat['id']:04d}-{safe_filename(chat['title'])}{suffix}"
        path = out_dir / name
        fn(db, chat["id"], path)
        written.append(path)
    return written


def backup(settings, db, path: str | Path) -> Path:
    """Write a zip holding the database and settings (API keys are stripped)."""
    path = Path(path)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    safe_settings = settings.as_dict()
    safe_settings["api_key"] = ""
    safe_settings["memory_embed_key"] = ""
    safe_settings["profiles"] = [
        {**p, "api_key": ""} for p in safe_settings.get("profiles", [])
    ]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "backup.json",
            json.dumps(
                {"app": "PyQOA", "created_at": stamp, "settings": safe_settings},
                indent=2, ensure_ascii=False,
            ),
        )
        db_path = Path(db.db_path)
        if db_path.exists():
            # Copy first: zipping a live SQLite file can capture a torn page.
            tmp = path.with_suffix(".dbcopy")
            shutil.copy2(db_path, tmp)
            try:
                zf.write(tmp, "chats.db")
            finally:
                tmp.unlink(missing_ok=True)
    return path


def import_json(db, path: str | Path) -> int:
    """Create a new chat from a PyQOA JSON export. Returns the new chat id."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
        raise ValueError("Not a valid PyQOA chat export (missing 'messages' list).")

    title = str(data.get("title") or "Imported Chat")
    chat_id = db.create_chat(title, folder=data.get("folder") or None)

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
        message_id = db.add_message(
            chat_id,
            role,
            str(content),
            int(m.get("prompt_tokens") or 0),
            int(m.get("completion_tokens") or 0),
            model=m.get("model"),
        )
        for att in m.get("attachments") or []:
            if not isinstance(att, dict):
                continue
            try:
                raw = base64.b64decode(att.get("data") or "")
            except Exception:
                continue
            db.add_attachment(
                message_id,
                att.get("kind") or attachments.KIND_TEXT,
                str(att.get("name") or "attachment"),
                str(att.get("mime") or ""),
                raw,
            )
    return chat_id
