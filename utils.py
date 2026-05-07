import re
import html as _html

try:
    from pygments import highlight as _pyg_hl
    from pygments.lexers import get_lexer_by_name as _get_lexer, TextLexer as _TextLexer
    from pygments.formatters import HtmlFormatter as _HtmlFormatter
    _PYGMENTS = True
except ImportError:
    _PYGMENTS = False


def _highlight_code(lang: str, code: str) -> str:
    """Return inline-styled highlighted HTML spans for code, or escaped plain text."""
    if not _PYGMENTS:
        return _html.escape(code)
    try:
        lexer = _get_lexer(lang, stripall=True) if lang else _TextLexer()
    except Exception:
        lexer = _TextLexer()
    formatter = _HtmlFormatter(noclasses=True, nowrap=True, style="monokai")
    return _pyg_hl(code, lexer, formatter).rstrip("\n")


def _code_block_html(lang: str, code: str) -> str:
    """Return a fully styled HTML block for a fenced code section."""
    inner = _highlight_code(lang, code)

    header = ""
    if lang:
        safe_lang = _html.escape(lang)
        header = (
            f'<p style="margin:0;padding:4px 14px;'
            f'background:#11162a;color:#64748b;font-size:11px;'
            f'font-family:\'Segoe UI\',Arial,sans-serif;'
            f'border-bottom:1px solid #2d3748;">'
            f'{safe_lang}</p>'
        )

    return (
        f'<div style="background:#0d1117;border-radius:8px;'
        f'border:1px solid #2d3748;margin:10px 0;">'
        f'{header}'
        f'<pre style="margin:0;padding:12px 16px;background:transparent;'
        f'font-family:\'Cascadia Code\',\'Fira Code\',Consolas,monospace;'
        f'font-size:13px;line-height:1.5;white-space:pre-wrap;'
        f'word-break:break-word;color:#f8f8f2;">'
        f'{inner}'
        f'</pre>'
        f'</div>'
    )


def _post_process_code_blocks(html_text: str) -> str:
    """Replace <pre><code class="language-X">…</code></pre> with styled+highlighted blocks."""

    def _replace(m: re.Match) -> str:
        class_attr = m.group(1)  # e.g.: ' class="language-python"'
        raw_code = _html.unescape(m.group(2))
        lang = ""
        # Prefer explicit language- prefix (fenced_code extension)
        lm = re.search(r"language-(\w+)", class_attr)
        if lm:
            lang = lm.group(1).lower()
        else:
            # Plain class name (e.g. class="python")
            lm = re.search(r'class="(\w+)"', class_attr)
            if lm:
                lang = lm.group(1).lower()
        if lang in ("text", "plain", "none"):
            lang = ""
        return _code_block_html(lang, raw_code)

    return re.sub(
        r"<pre><code([^>]*)>(.*?)</code></pre>",
        _replace,
        html_text,
        flags=re.DOTALL,
    )


def text_to_html(text: str) -> str:
    """Convert Markdown text to HTML for display in QTextEdit."""
    try:
        import markdown

        html_out = markdown.markdown(
            text,
            extensions=["fenced_code", "tables", "nl2br", "sane_lists"],
        )
        return _post_process_code_blocks(html_out)
    except ImportError:
        return _simple_md(text)


def _simple_md(text: str) -> str:
    """Minimal Markdown → HTML fallback when the markdown library is missing."""
    parts = re.split(r"(```[\w]*\n[\s\S]*?```)", text)
    out = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            m = re.match(r"```(\w*)\n([\s\S]*?)```", part)
            lang = m.group(1) if m else ""
            code = m.group(2) if m else part
            out.append(_code_block_html(lang, code))
        else:
            p = _html.escape(part)
            # inline code
            p = re.sub(
                r"`([^`]+)`",
                r'<code style="background:#1e2d3d;padding:2px 6px;'
                r'border-radius:4px;color:#e2e8f0;font-family:Consolas,monospace;'
                r'font-size:13px;">\1</code>',
                p,
            )
            # bold + italic
            p = re.sub(r"\*\*\*(.+?)\*\*\*", r"<b><i>\1</i></b>", p, flags=re.DOTALL)
            p = re.sub(r"\*\*(.+?)\*\*",     r"<b>\1</b>",         p, flags=re.DOTALL)
            p = re.sub(r"\*(.+?)\*",          r"<i>\1</i>",         p, flags=re.DOTALL)
            # headers
            p = re.sub(r"^### (.+)$", r'<h3 style="color:#e2e8f0;margin:8px 0 4px;">\1</h3>', p, flags=re.MULTILINE)
            p = re.sub(r"^## (.+)$",  r'<h2 style="color:#e2e8f0;margin:8px 0 4px;">\1</h2>', p, flags=re.MULTILINE)
            p = re.sub(r"^# (.+)$",   r'<h1 style="color:#e2e8f0;margin:8px 0 4px;">\1</h1>', p, flags=re.MULTILINE)
            # newlines
            p = p.replace("\n", "<br>")
            out.append(p)
    return "".join(out)
