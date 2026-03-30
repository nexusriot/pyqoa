import html
import re


def text_to_html(text: str) -> str:
    """Convert plain text (with Markdown) to HTML for display in QTextBrowser."""
    try:
        import markdown

        return markdown.markdown(
            text,
            extensions=["fenced_code", "tables", "nl2br", "sane_lists"],
        )
    except ImportError:
        return _simple_md(text)


def _simple_md(text: str) -> str:
    """Minimal Markdown → HTML fallback (no external deps)."""
    # Split out fenced code blocks first so we don't mangle them
    parts = re.split(r"(```[\s\S]*?```)", text)
    out = []
    for i, part in enumerate(parts):
        if i % 2 == 1:  # fenced code block
            inner = re.sub(r"^```\w*\n?", "", part)
            inner = re.sub(r"\n?```$", "", inner)
            inner = html.escape(inner)
            out.append(
                f'<pre style="background:#0d1117;padding:10px;border-radius:6px;'
                f'white-space:pre-wrap;"><code>{inner}</code></pre>'
            )
        else:
            p = html.escape(part)
            # inline code
            p = re.sub(
                r"`([^`]+)`",
                r'<code style="background:#0d1117;padding:2px 5px;border-radius:3px;">\1</code>',
                p,
            )
            # bold + italic
            p = re.sub(r"\*\*\*(.+?)\*\*\*", r"<b><i>\1</i></b>", p, flags=re.DOTALL)
            p = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", p, flags=re.DOTALL)
            p = re.sub(r"\*(.+?)\*", r"<i>\1</i>", p, flags=re.DOTALL)
            # headers
            p = re.sub(r"^### (.+)$", r"<h3>\1</h3>", p, flags=re.MULTILINE)
            p = re.sub(r"^## (.+)$", r"<h2>\1</h2>", p, flags=re.MULTILINE)
            p = re.sub(r"^# (.+)$", r"<h1>\1</h1>", p, flags=re.MULTILINE)
            # newlines → <br>
            p = p.replace("\n", "<br>")
            out.append(p)
    return "".join(out)
