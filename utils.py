import re
import html as _html

import theme

try:
    from pygments import highlight as _pyg_hl
    from pygments.lexers import get_lexer_by_name as _get_lexer, TextLexer as _TextLexer
    from pygments.formatters import HtmlFormatter as _HtmlFormatter
    _PYGMENTS = True
except ImportError:
    _PYGMENTS = False

try:
    import markdown as _markdown
    _MARKDOWN = True
except ImportError:
    _MARKDOWN = False


def _highlight_code(lang: str, code: str) -> str:
    """Return inline-styled highlighted HTML spans for code, or escaped plain text."""
    if not _PYGMENTS:
        return _html.escape(code)
    try:
        lexer = _get_lexer(lang, stripall=True) if lang else _TextLexer()
    except Exception:
        lexer = _TextLexer()
    formatter = _HtmlFormatter(noclasses=True, nowrap=True, style=theme.PYGMENTS_STYLE)
    return _pyg_hl(code, lexer, formatter).rstrip("\n")


def _code_block_html(lang: str, code: str, copy_index: int | None = None) -> str:
    """Return a fully styled HTML block for a fenced code section.

    When `copy_index` is given, the header shows a "Copy" link whose href encodes
    that index (``pyqoacopy:N``); the message widget intercepts the click and copies
    the corresponding raw code to the clipboard.
    """
    inner = _highlight_code(lang, code)

    bits = []
    if lang:
        bits.append(_html.escape(lang))
    if copy_index is not None:
        bits.append(
            f'<a href="pyqoacopy:{copy_index}" '
            f'style="color:{theme.CODE_COPY_FG};text-decoration:none;">⧉ Copy</a>'
        )
    header = ""
    if bits:
        header = (
            f'<p style="margin:0;padding:5px 14px;'
            f'background:{theme.CODE_HEADER_BG};color:{theme.CODE_HEADER_FG};font-size:11px;'
            f'font-family:{theme.FONT_STACK};'
            f'border-bottom:1px solid {theme.CODE_BORDER};">'
            f'{"  ·  ".join(bits)}</p>'
        )

    return (
        f'<div style="background:{theme.CODE_BG};border-radius:{theme.RADIUS_SM}px;'
        f'border:1px solid {theme.CODE_BORDER};margin:10px 0;">'
        f'{header}'
        f'<pre style="margin:0;padding:12px 16px;background:transparent;'
        f'font-family:{theme.MONO_STACK};'
        f'font-size:13px;line-height:1.5;white-space:pre-wrap;'
        f'word-break:break-word;color:{theme.CODE_FG};">'
        # Wrap in <font color> so un-tokenised code text gets the code foreground as an
        # explicit character format (Qt's rich-text engine honours this), independent
        # of the document's body text colour, which follows the light/dark theme.
        f'<font color="{theme.CODE_FG}">{inner}</font>'
        f'</pre>'
        f'</div>'
    )


def _post_process_code_blocks(html_text: str, codes: list[str]) -> str:
    """Replace <pre><code class="language-X">…</code></pre> with styled+highlighted blocks.

    Each block's raw code is appended to `codes`; its position is used as the
    copy index encoded into the block's "Copy" link.
    """

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
        idx = len(codes)
        codes.append(raw_code)
        return _code_block_html(lang, raw_code, copy_index=idx)

    return re.sub(
        r"<pre><code([^>]*)>(.*?)</code></pre>",
        _replace,
        html_text,
        flags=re.DOTALL,
    )


def render_markdown(text: str) -> tuple[str, list[str]]:
    """Convert Markdown to display HTML and return (html, code_block_sources)."""
    codes: list[str] = []
    if not _MARKDOWN:
        return _simple_md(text, codes), codes
    html_out = _markdown.markdown(
        text,
        extensions=["fenced_code", "tables", "nl2br", "sane_lists"],
    )
    return _post_process_code_blocks(html_out, codes), codes


def text_to_html(text: str) -> str:
    """Convert Markdown text to HTML for display in QTextEdit."""
    return render_markdown(text)[0]


def _simple_md(text: str, codes: list[str] | None = None) -> str:
    """Minimal Markdown → HTML fallback when the markdown library is missing."""
    if codes is None:
        codes = []
    parts = re.split(r"(```[\w]*\n[\s\S]*?```)", text)
    out = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            m = re.match(r"```(\w*)\n([\s\S]*?)```", part)
            lang = m.group(1) if m else ""
            code = m.group(2) if m else part
            idx = len(codes)
            codes.append(code)
            out.append(_code_block_html(lang, code, copy_index=idx))
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
