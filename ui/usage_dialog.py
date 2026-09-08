"""Usage dashboard: what has been spent, per day and per model.

Everything shown here comes from the token counts already stored on each
message, so no extra bookkeeping is needed.
"""

import os
import sys

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTextBrowser, QPushButton,
    QComboBox,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pricing
import theme

RANGES = {"Last 7 days": 7, "Last 30 days": 30, "Last 90 days": 90}


class UsageDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Usage")
        self.setMinimumSize(640, 520)
        self.setStyleSheet(
            f"QDialog{{background:{theme.PANEL};color:{theme.TEXT};}}"
            f"QLabel{{color:{theme.MUTED};}}"
            f"QComboBox,QPushButton{{background:{theme.SURFACE};color:{theme.TEXT};"
            f"border:1px solid {theme.BORDER};border-radius:6px;padding:5px 12px;}}"
            f"QComboBox QAbstractItemView{{background:{theme.SURFACE};"
            f"color:{theme.TEXT};selection-background-color:{theme.ACCENT};}}"
        )

        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(18, 14, 18, 14)

        top = QHBoxLayout()
        title = QLabel("Token usage")
        title.setStyleSheet(
            f"color:{theme.TEXT};font-size:{theme.FS_LG}px;font-weight:700;"
        )
        top.addWidget(title)
        top.addStretch()
        self.range_combo = QComboBox()
        self.range_combo.addItems(list(RANGES))
        self.range_combo.setCurrentText("Last 30 days")
        self.range_combo.currentTextChanged.connect(lambda _: self.refresh())
        top.addWidget(self.range_combo)
        root.addLayout(top)

        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(False)
        self.view.setStyleSheet(
            f"QTextBrowser{{background:{theme.BG};border:1px solid {theme.BORDER};"
            f"border-radius:{theme.RADIUS_SM}px;padding:10px;}}"
        )
        root.addWidget(self.view, stretch=1)

        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(close)
        root.addLayout(row)

        self.refresh()

    def refresh(self):
        self.view.setHtml(self._build_html())

    def _build_html(self) -> str:
        days = RANGES.get(self.range_combo.currentText(), 30)
        messages, prompt, completion = self.db.usage_totals()
        by_model = self.db.usage_by_model()
        by_day = self.db.usage_by_day(days)

        total_cost = 0.0
        model_rows = []
        for row in by_model:
            name = row["model"] or "(not recorded)"
            cost = pricing.estimate_cost(
                row["model"] or "", row["prompt_tokens"], row["completion_tokens"]
            )
            if cost:
                total_cost += cost
            model_rows.append(
                f"<tr><td>{name}</td>"
                f"<td class='n'>{row['messages']:,}</td>"
                f"<td class='n'>{row['prompt_tokens']:,}</td>"
                f"<td class='n'>{row['completion_tokens']:,}</td>"
                f"<td class='n'>{'$%.4f' % cost if cost else '—'}</td></tr>"
            )

        peak = max((r["prompt_tokens"] + r["completion_tokens"] for r in by_day),
                   default=0)
        day_rows = []
        for row in by_day:
            total = row["prompt_tokens"] + row["completion_tokens"]
            width = int(round(100 * total / peak)) if peak else 0
            day_rows.append(
                f"<tr><td class='day'>{row['day']}</td>"
                f"<td class='bar'><span style='background:{theme.ACCENT};'>"
                f"{'&nbsp;' * max(1, width)}</span></td>"
                f"<td class='n'>{total:,}</td></tr>"
            )
        if not day_rows:
            day_rows.append(
                f"<tr><td colspan='3' class='muted'>No activity in the last "
                f"{days} days.</td></tr>"
            )

        return f"""
<html><head><style>
body {{ font-family:{theme.FONT_STACK}; color:{theme.TEXT};
        font-size:{theme.FS_MD}px; }}
h2 {{ font-size:{theme.FS_BASE}px; color:{theme.MUTED}; margin:14px 0 6px; }}
table {{ border-collapse:collapse; width:100%; }}
td, th {{ border-bottom:1px solid {theme.BORDER}; padding:5px 8px;
          text-align:left; }}
th {{ color:{theme.MUTED}; font-weight:600; }}
.n {{ text-align:right; }}
.day {{ color:{theme.MUTED}; white-space:nowrap; }}
.bar span {{ font-size:{theme.FS_XS}px; }}
.muted {{ color:{theme.FAINT}; }}
.big {{ font-size:{theme.FS_TITLE}px; font-weight:700; color:{theme.TEXT}; }}
</style></head><body>
<p><span class="big">{prompt + completion:,}</span> tokens across
   {messages:,} messages &nbsp;·&nbsp; estimated cost
   <b>${total_cost:.4f}</b></p>
<p class="muted">{prompt:,} prompt + {completion:,} completion.
   Costs are estimates from a built-in price table and exclude models it does
   not know.</p>
<h2>By model</h2>
<table>
<tr><th>Model</th><th class="n">Messages</th><th class="n">Prompt</th>
    <th class="n">Completion</th><th class="n">Est. cost</th></tr>
{''.join(model_rows) or "<tr><td colspan='5' class='muted'>Nothing yet.</td></tr>"}
</table>
<h2>Last {days} days</h2>
<table>{''.join(day_rows)}</table>
</body></html>
"""
