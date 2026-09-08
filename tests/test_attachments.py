import base64

import pytest

import attachments
from attachments import (
    KIND_IMAGE, KIND_TEXT, data_uri, is_image, message_payload,
    read_attachment, save_attachments,
)

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg=="
)


def test_is_image_is_case_insensitive():
    assert is_image("photo.PNG") and not is_image("notes.txt")


def test_data_uri_encodes_base64():
    assert data_uri("image/png", b"ab") == "data:image/png;base64,YWI="


def test_reading_an_image(tmp_path):
    path = tmp_path / "pic.png"
    path.write_bytes(PNG)
    att = read_attachment(path)
    assert att["kind"] == KIND_IMAGE and att["mime"] == "image/png"
    assert att["data"] == PNG


def test_reading_a_text_file(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text("# hello", encoding="utf-8")
    att = read_attachment(path)
    assert att["kind"] == KIND_TEXT and att["data"] == b"# hello"


def test_oversized_files_are_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(attachments, "MAX_BYTES", 4)
    path = tmp_path / "big.txt"
    path.write_text("way too long")
    with pytest.raises(ValueError, match="limit"):
        read_attachment(path)


def test_binary_non_images_are_refused(tmp_path):
    path = tmp_path / "blob.bin"
    path.write_bytes(b"\xff\xfe\x00\x01")
    with pytest.raises(ValueError, match="UTF-8"):
        read_attachment(path)


def test_missing_file_is_refused(tmp_path):
    with pytest.raises(ValueError):
        read_attachment(tmp_path / "nope.txt")


def test_long_text_is_truncated_not_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(attachments, "MAX_TEXT_CHARS", 10)
    path = tmp_path / "long.txt"
    path.write_text("x" * 100)
    assert len(read_attachment(path)["data"]) == 10


def test_payload_stays_a_plain_string_without_attachments(db, chat):
    mid = db.add_message(chat, "user", "hello")
    assert message_payload(db, db.get_message(mid)) == {
        "role": "user", "content": "hello"
    }


def test_image_payload_becomes_a_content_array(db, chat):
    mid = db.add_message(chat, "user", "what is this?")
    save_attachments(db, mid, [
        {"kind": KIND_IMAGE, "name": "p.png", "mime": "image/png", "data": PNG}
    ])
    payload = message_payload(db, db.get_message(mid))
    assert payload["content"][0] == {"type": "text", "text": "what is this?"}
    assert payload["content"][1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )


def test_text_attachments_are_appended_as_fenced_blocks(db, chat):
    mid = db.add_message(chat, "user", "review this")
    save_attachments(db, mid, [
        {"kind": KIND_TEXT, "name": "a.py", "mime": "text/x-python",
         "data": b"print(1)"}
    ])
    payload = message_payload(db, db.get_message(mid))
    assert isinstance(payload["content"], str)
    assert "```py" in payload["content"] and "print(1)" in payload["content"]
    assert "a.py" in payload["content"]
