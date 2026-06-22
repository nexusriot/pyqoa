"""Central design tokens and reusable stylesheet snippets for PyQOA.

Two palettes (``dark`` and ``light``) share the same set of token names. The active
palette's values are exposed as module-level attributes (``theme.BG``, ``theme.TEXT``,
…); :func:`apply` rebinds them when the theme changes. Widgets read these tokens
instead of hard-coding hex values, so a theme switch is just "rebind tokens, rebuild
the styled widgets".
"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPalette, QColor, QGuiApplication

# Palettes (same keys, different values).
_DARK = {
    "BG":          "#0e0f13",   # app base / chat canvas
    "PANEL":       "#15171c",   # sidebar, headers, input bar
    "SURFACE":     "#1b1e25",   # bubbles, inputs, cards
    "SURFACE_HI":  "#23272f",   # hover / elevated
    "SURFACE_SEL": "#1d2740",   # selected / active tint
    "BORDER":      "#2a2f3a",   # subtle separators
    "BORDER_HI":   "#3a414e",   # stronger / focused border

    "TEXT":        "#ececf1",   # primary text
    "MUTED":       "#9aa4b2",   # secondary text
    "FAINT":       "#6b7480",   # tertiary (timestamps, hints)

    "ACCENT":      "#4d7cfe",   # primary accent
    "ACCENT_HI":   "#3b6ae6",   # accent hover
    "ACCENT_DEEP": "#2f55c0",   # accent pressed

    "USER":        "#5b9bff",   # user role accent
    "ASSISTANT":   "#34d399",   # assistant role accent
    "AVATAR_FG":   "#0b0d12",   # text/glyph drawn on a coloured avatar

    "DANGER":      "#f87171",
    "DANGER_BG":   "#3a1416",
    "DANGER_BD":   "#7f1d1d",

    # Code blocks (dark surface + dark Pygments style for the dark theme).
    "CODE_BG":        "#0d1117",
    "CODE_HEADER_BG": "#161b22",
    "CODE_BORDER":    "#2a2f3a",
    "CODE_HEADER_FG": "#8b949e",
    "CODE_FG":        "#f8f8f2",
    "CODE_COPY_FG":   "#79c0ff",
    "PYGMENTS_STYLE": "monokai",
}

_LIGHT = {
    "BG":          "#f6f7f9",
    "PANEL":       "#ffffff",
    "SURFACE":     "#ffffff",
    "SURFACE_HI":  "#eceef2",
    "SURFACE_SEL": "#e6efff",
    "BORDER":      "#e2e5ea",
    "BORDER_HI":   "#cdd3dc",

    "TEXT":        "#1c2430",
    "MUTED":       "#5b6473",
    "FAINT":       "#8a93a3",

    "ACCENT":      "#2f6bff",
    "ACCENT_HI":   "#225ae0",
    "ACCENT_DEEP": "#1b49bd",

    "USER":        "#2f6bff",
    "ASSISTANT":   "#0f9d70",
    "AVATAR_FG":   "#ffffff",

    "DANGER":      "#dc2626",
    "DANGER_BG":   "#fdecec",
    "DANGER_BD":   "#f1b4b4",

    # Code blocks (light surface + light Pygments style for the light theme), so
    # un-tokenised code text inherits the matching dark body colour and stays legible.
    "CODE_BG":        "#f3f5f8",
    "CODE_HEADER_BG": "#e9edf2",
    "CODE_BORDER":    "#dfe3e9",
    "CODE_HEADER_FG": "#5b6473",
    "CODE_FG":        "#1c2430",
    "CODE_COPY_FG":   "#2f6bff",
    "PYGMENTS_STYLE": "default",
}

_PALETTES = {"dark": _DARK, "light": _LIGHT}

# Active palette tokens — defined explicitly (from the dark palette) so they are
# statically visible; apply() rebinds them when the theme changes.
BG          = _DARK["BG"]
PANEL       = _DARK["PANEL"]
SURFACE     = _DARK["SURFACE"]
SURFACE_HI  = _DARK["SURFACE_HI"]
SURFACE_SEL = _DARK["SURFACE_SEL"]
BORDER      = _DARK["BORDER"]
BORDER_HI   = _DARK["BORDER_HI"]
TEXT        = _DARK["TEXT"]
MUTED       = _DARK["MUTED"]
FAINT       = _DARK["FAINT"]
ACCENT      = _DARK["ACCENT"]
ACCENT_HI   = _DARK["ACCENT_HI"]
ACCENT_DEEP = _DARK["ACCENT_DEEP"]
USER        = _DARK["USER"]
ASSISTANT   = _DARK["ASSISTANT"]
AVATAR_FG   = _DARK["AVATAR_FG"]
DANGER      = _DARK["DANGER"]
DANGER_BG   = _DARK["DANGER_BG"]
DANGER_BD   = _DARK["DANGER_BD"]
CODE_BG        = _DARK["CODE_BG"]
CODE_HEADER_BG = _DARK["CODE_HEADER_BG"]
CODE_BORDER    = _DARK["CODE_BORDER"]
CODE_HEADER_FG = _DARK["CODE_HEADER_FG"]
CODE_FG        = _DARK["CODE_FG"]
CODE_COPY_FG   = _DARK["CODE_COPY_FG"]
PYGMENTS_STYLE = _DARK["PYGMENTS_STYLE"]

# Constant tokens (identical across themes).
RADIUS      = 12
RADIUS_SM   = 8
RADIUS_LG   = 16

FONT_STACK  = "'Segoe UI','Helvetica Neue','Inter',Arial,sans-serif"
MONO_STACK  = "'Cascadia Code','Fira Code','JetBrains Mono',Consolas,monospace"

# Comfortable reading width for the message column on wide windows.
CONTENT_MAX_WIDTH = 820

NAME = "dark"


def available() -> list[str]:
    return list(_PALETTES)


def system_scheme() -> str:
    """The OS colour preference as "dark"/"light" (falls back to "dark").

    Uses QStyleHints.colorScheme() (Qt 6.5+); degrades to "dark" when the API is
    missing or the platform reports no preference.
    """
    app = QGuiApplication.instance()
    if app is not None and hasattr(app.styleHints(), "colorScheme"):
        try:
            scheme = app.styleHints().colorScheme()
            if scheme == Qt.ColorScheme.Light:
                return "light"
            if scheme == Qt.ColorScheme.Dark:
                return "dark"
        except Exception:
            pass
    return "dark"


def resolve(pref: str) -> str:
    """Map a preference ("system"/"light"/"dark") to a concrete palette name."""
    if pref in ("light", "dark"):
        return pref
    return system_scheme()  # "system" or anything unrecognised


def apply(name: str = "dark") -> str:
    """Activate a palette by name, rebinding the module-level colour tokens."""
    name = name if name in _PALETTES else "dark"
    globals().update(_PALETTES[name])
    globals()["NAME"] = name
    return name


def qpalette() -> QPalette:
    """A QPalette derived from the active tokens (set on the QApplication)."""
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window,          QColor(BG))
    p.setColor(QPalette.ColorRole.WindowText,      QColor(TEXT))
    p.setColor(QPalette.ColorRole.Base,            QColor(SURFACE))
    p.setColor(QPalette.ColorRole.AlternateBase,   QColor(SURFACE_HI))
    p.setColor(QPalette.ColorRole.ToolTipBase,     QColor(SURFACE_HI))
    p.setColor(QPalette.ColorRole.ToolTipText,     QColor(TEXT))
    p.setColor(QPalette.ColorRole.Text,            QColor(TEXT))
    p.setColor(QPalette.ColorRole.Button,          QColor(SURFACE))
    p.setColor(QPalette.ColorRole.ButtonText,      QColor(TEXT))
    p.setColor(QPalette.ColorRole.BrightText,      QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.Link,            QColor(ACCENT))
    p.setColor(QPalette.ColorRole.Highlight,       QColor(ACCENT))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor(FAINT))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,       QColor(FAINT))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(FAINT))
    return p


def scrollbar_qss() -> str:
    """Thin, rounded scrollbars used app-wide."""
    return f"""
    QScrollBar:vertical {{
        background: transparent; width: 10px; margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {BORDER_HI}; border-radius: 5px; min-height: 28px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {FAINT}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    QScrollBar:horizontal {{
        background: transparent; height: 10px; margin: 2px;
    }}
    QScrollBar::handle:horizontal {{
        background: {BORDER_HI}; border-radius: 5px; min-width: 28px;
    }}
    QScrollBar::handle:horizontal:hover {{ background: {FAINT}; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
    """


def global_qss() -> str:
    """App-wide base styling (set on the QApplication; re-applied on theme change)."""
    return f"""
    * {{ font-family: {FONT_STACK}; }}
    QToolTip {{
        background: {SURFACE_HI}; color: {TEXT};
        border: 1px solid {BORDER_HI}; border-radius: {RADIUS_SM}px;
        padding: 5px 9px;
    }}
    QMenu {{
        background: {PANEL}; color: {TEXT};
        border: 1px solid {BORDER}; border-radius: {RADIUS_SM}px; padding: 4px;
    }}
    QMenu::item {{ padding: 6px 22px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {ACCENT}; color: white; }}
    QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}
    QMenuBar {{ background: {PANEL}; color: {TEXT}; border-bottom: 1px solid {BORDER}; }}
    QMenuBar::item {{ padding: 6px 12px; background: transparent; border-radius: 6px; }}
    QMenuBar::item:selected {{ background: {SURFACE_HI}; }}
    {scrollbar_qss()}
    """


def primary_button_qss(radius: int = RADIUS_SM) -> str:
    """Filled accent button."""
    return f"""
    QPushButton {{
        background: {ACCENT}; color: white;
        border: none; border-radius: {radius}px;
        font-size: 14px; font-weight: 600;
    }}
    QPushButton:hover  {{ background: {ACCENT_HI}; }}
    QPushButton:pressed{{ background: {ACCENT_DEEP}; }}
    QPushButton:disabled{{ background: {SURFACE_HI}; color: {FAINT}; }}
    """
