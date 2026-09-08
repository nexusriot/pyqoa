"""File attachments: reading them off disk, storing them, and rendering them
into the multi-part `content` arrays that vision-capable models expect.

Images become `image_url` parts carrying a base64 data URI; text files are
appended to the message body as fenced blocks, which every model understands.
"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

MAX_BYTES = 8 * 1024 * 1024          # per attachment
MAX_TEXT_CHARS = 200_000             # text files are truncated, not rejected

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
IMAGE_MIMES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
}

KIND_IMAGE = "image"
KIND_TEXT = "text"

FILE_FILTER = (
    "Attachments (*.png *.jpg *.jpeg *.gif *.webp *.bmp *.txt *.md *.py *.js "
    "*.ts *.json *.yaml *.yml *.csv *.log *.html *.css *.sh *.c *.cpp *.go "
    "*.rs *.java *.sql *.toml *.ini);;Images (*.png *.jpg *.jpeg *.gif *.webp "
    "*.bmp);;All files (*)"
)


def is_image(path: str | Path) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTS


def read_attachment(path: str | Path) -> dict:
    """Read a file into an attachment dict, or raise ValueError."""
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"{p.name}: not a file")
    size = p.stat().st_size
    if size > MAX_BYTES:
        raise ValueError(
            f"{p.name}: {size // 1024} KB exceeds the "
            f"{MAX_BYTES // (1024 * 1024)} MB attachment limit"
        )
    if is_image(p):
        mime = IMAGE_MIMES.get(p.suffix.lower()) or "application/octet-stream"
        return {
            "kind": KIND_IMAGE, "name": p.name, "mime": mime,
            "data": p.read_bytes(),
        }
    raw = p.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{p.name}: not a UTF-8 text file") from exc
    mime = mimetypes.guess_type(p.name)[0] or "text/plain"
    return {
        "kind": KIND_TEXT, "name": p.name, "mime": mime,
        "data": text[:MAX_TEXT_CHARS].encode("utf-8"),
    }


def data_uri(mime: str, data: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def save_attachments(db, message_id: int, items: list[dict]) -> None:
    for item in items:
        db.add_attachment(
            message_id, item["kind"], item["name"],
            item.get("mime", ""), item.get("data"),
        )


def _text_block(name: str, data: bytes) -> str:
    body = (data or b"").decode("utf-8", errors="replace")
    suffix = Path(name).suffix.lstrip(".")
    return f"\n\n--- attached file: {name} ---\n```{suffix}\n{body}\n```"


def message_payload(db, row) -> dict:
    """Convert a stored message row into an API message.

    Returns plain string content when the message has no attachments, so simple
    conversations keep the simple wire format.
    """
    content = row["content"]
    role = row["role"]
    try:
        atts = db.get_attachments(row["id"])
    except Exception:
        atts = []
    if not atts:
        return {"role": role, "content": content}

    text = content
    images = []
    for att in atts:
        if att["kind"] == KIND_IMAGE and att["data"]:
            images.append({
                "type": "image_url",
                "image_url": {"url": data_uri(att["mime"], att["data"])},
            })
        else:
            text += _text_block(att["name"], att["data"])
    if not images:
        return {"role": role, "content": text}
    parts: list[dict] = []
    if text:
        parts.append({"type": "text", "text": text})
    parts.extend(images)
    return {"role": role, "content": parts}
