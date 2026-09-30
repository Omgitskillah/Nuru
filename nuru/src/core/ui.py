"""Desktop chat window for Nuru (PyQt6).

The window only draws. Talking to the model happens in core.session, on a background thread,
and what comes back is folded into markdown by core.reply_markdown.
"""

import functools
import math
import re
import sys
import threading
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from pathlib import Path

from PyQt6.QtCore import QByteArray, QObject, QSettings, QPointF, QRectF, QSize, QSizeF, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtGui import (
    QBrush,
    QCloseEvent,
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QKeyEvent,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QPolygonF,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFormat,
    QTextFrameFormat,
    QTextLength,
    QTextTable,
)
from PyQt6.QtWidgets import (
    QApplication,
    QListWidget,
    QListWidgetItem,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core import titles
from core.reply_markdown import Code, Prose, Reply, format_tool_call, split_fences
from core.session import (
    Event,
    LayaDecision,
    Progress,
    RetryWithoutThinking,
    Session,
    Steered,
    Stopped,
    Thinking,
    ToolCall,
    ToolResult,
)

# Monochrome palette taken from the lantern logo: its ink color (#242021), plus tints of it on white
INK = "#242021"
BG = "#FAFAFA"
BUBBLE = "#F0F0F0"
BORDER = "#E0DFDF"
TEXT = INK
MUTED = "#878586"
QUOTE = "#615E5F"
SEND = INK
SEND_HOVER = "#3D393A"
PANEL = "#F3F3F3"  # the history panel, a shade off the window's background
LAYA = "#5B6B8C"  # the developer view's rows: set apart from the model's own working
NOTICE_BG = "#FFFAF0"  # a turn Nuru ended itself: warm, not an error's red
NOTICE_BORDER = "#E9D8B4"
NOTICE_TEXT = "#8A6D2F"
OK_DOT, WARN_DOT, BAD_DOT, OFF_DOT = "#3A9D5D", "#C9962B", "#C0453A", "#B5B3B4"  # the status bar's dots
# A state's dot: green ready/loaded, amber working or starting, grey idle or off, red down or failed
# (anything not listed). The README's "The window" section has the same key.
STATE_DOTS = {"ready": OK_DOT, "loaded": OK_DOT, "loading": WARN_DOT, "not started": WARN_DOT,
              "embedding": WARN_DOT, "idle": OFF_DOT, "off": OFF_DOT, "no vault": OFF_DOT}

COLUMN_WIDTH = 880
BUBBLE_WIDTH = 600  # your messages wrap before the full column width so they read as bubbles
SWING = 8  # degrees the lantern rocks to either side
LOGO_PATH = Path(__file__).parent / "assets" / "lantern.png"  # transparent PNG
# The status bar's icons: Lucide (lucide.dev, v0.469.0, ISC licence - assets/icons/LICENSE-lucide.txt).
# Line icons drawn in `currentColor`, so each is tinted with its item's state color.
ICONS_DIR = Path(__file__).parent / "assets" / "icons"
ICON_PX = 14
ICON_PADDING = 0.1  # white kept clear around the lantern in the app icon, as a share of its height
# How macOS shapes an app icon: a rounded square filling 824 of the 1024-point canvas, the rest of
# it left clear, and corners that curve into the sides rather than meeting them at an arc. That
# shape is a superellipse - |x|^n + |y|^n = r^n - with n around 5.
ICON_SHAPE = 824 / 1024
ICON_CURVE = 5

# Where a link in the model's text may lead. Anything else (file:, custom app schemes) is ignored:
# opening one would launch whatever it names.
LINK_SCHEMES = frozenset({"http", "https", "mailto"})

STYLE = f"""
#chat {{ background: {BG}; }}
QScrollBar:vertical {{ background: transparent; width: 14px; margin: 4px 3px; }}
QScrollBar::handle:vertical {{ background: rgba(36, 32, 33, 46); border-radius: 4px; min-height: 32px; }}
QScrollBar::handle:vertical:hover {{ background: rgba(36, 32, 33, 82); }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QFrame#composer, QFrame#reply {{ background: white; border: 1px solid {BORDER}; border-radius: 6px; }}
QFrame#replyHeader {{ background: white; border: 1px solid {BORDER}; border-radius: 5px; }}
QPushButton#replyToggle {{ background: transparent; border: none; padding: 0; text-align: left; color: {MUTED}; font-size: 11px; }}
QPushButton#replyToggle:hover {{ color: {TEXT}; }}
QLabel#workRow {{ color: {MUTED}; }}
QLabel#layaRow {{ color: {LAYA}; }}
QFrame#notice {{ background: {NOTICE_BG}; border: 1px solid {NOTICE_BORDER}; border-radius: 6px; }}
QLabel#noticeTitle {{ color: {NOTICE_TEXT}; font-size: 11px; }}
QFrame#statusBar {{ background: {BG}; border-top: 1px solid {BORDER}; }}
QFrame#history {{ background: {PANEL}; border-right: 1px solid {BORDER}; }}
QLabel#historyTitle {{ color: {MUTED}; font-size: 11px; }}
QListWidget#historyList {{ background: transparent; border: none; outline: none; font-size: 13px; color: {TEXT}; }}
QListWidget#historyList::item {{ padding: 5px 8px; border-radius: 4px; }}
QListWidget#historyList::item:selected {{ background: {BUBBLE}; color: {TEXT}; }}
QListWidget#historyList::item:hover {{ background: rgba(36, 32, 33, 12); }}
QPushButton#iconButton {{ background: transparent; border: none; border-radius: 3px; padding: 2px; }}
QPushButton#iconButton:hover {{ background: rgba(36, 32, 33, 18); }}
QLabel#statusItem {{ color: {MUTED}; font-size: 11px; }}
QPushButton#devToggle {{ background: transparent; border: 1px solid {BORDER}; border-radius: 3px; color: {MUTED}; font-size: 11px; padding: 1px 7px; }}
QPushButton#devToggle:checked {{ background: {INK}; color: white; border-color: {INK}; }}
QFrame#userBubble {{ background: {BUBBLE}; border-radius: 5px; }}
QFrame#codeBlock {{ background: {BUBBLE}; border-radius: 4px; }}
QLabel#userText, QLabel#greeting, QLabel#codeText {{ color: {TEXT}; }}
QLabel#codeLanguage {{ color: {MUTED}; font-size: 12px; }}
QPushButton#copy {{ background: transparent; border: none; color: {MUTED}; font-size: 12px; padding: 2px 6px; }}
QPushButton#copy:hover {{ color: {TEXT}; }}
QPushButton#send {{ background: {SEND}; border: none; border-radius: 5px; }}
QPushButton#send:hover {{ background: {SEND_HOVER}; }}
QPushButton#send:disabled {{ background: rgba(36, 32, 33, 80); }}
"""

SERIF = ["Charter", "Georgia"]
MONO = ["Menlo", "Monaco"]


def font(px: int, families: list[str] | None = None, fallback: QFont.StyleHint = QFont.StyleHint.AnyStyle) -> QFont:
    f = QFont()
    if families:
        f.setFamilies(families)
    f.setStyleHint(fallback)  # what to use on a machine that has none of the families
    f.setPixelSize(px)
    return f


def time_greeting(hour: int) -> str:
    return "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"


def open_link(url: QUrl) -> None:
    if url.scheme() in LINK_SCHEMES:
        QDesktopServices.openUrl(url)


def style_table(table: QTextTable) -> None:
    """Nuru-style table: full width, no grid, just a hairline under each row."""
    fmt = table.format()
    fmt.setBorder(0)
    fmt.setBorderCollapse(True)
    fmt.setCellSpacing(0)
    fmt.setCellPadding(6)
    fmt.setWidth(QTextLength(QTextLength.Type.PercentageLength, 100))
    table.setFormat(fmt)

    for row in range(table.rows()):
        for col in range(table.columns()):
            cell = table.cellAt(row, col)
            cell_fmt = cell.format().toTableCellFormat()
            cell_fmt.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignMiddle)
            cell_fmt.setBottomBorder(1)
            cell_fmt.setBottomBorderStyle(QTextFrameFormat.BorderStyle.BorderStyle_Solid)
            cell_fmt.setBottomBorderBrush(QBrush(QColor(BORDER)))
            cell.setFormat(cell_fmt)

    # Qt applies a column's alignment (---: for right) to body cells only; give the header the same
    if table.rows() > 1:
        for col in range(table.columns()):
            align = QTextBlockFormat()
            align.setAlignment(table.cellAt(1, col).firstCursorPosition().blockFormat().alignment())
            table.cellAt(0, col).firstCursorPosition().mergeBlockFormat(align)


def style_document(doc: QTextDocument) -> None:
    """Restyle what setMarkdown produced, since Qt bakes the formatting in and ignores CSS."""
    block = doc.begin()
    while block.isValid():
        cursor = QTextCursor(block)
        fmt = QTextBlockFormat()
        fmt.setLineHeight(130, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        is_rule = block.blockFormat().hasProperty(QTextFormat.Property.BlockTrailingHorizontalRulerWidth)
        if not block.text() and not is_rule:
            # The importer leaves an empty block ahead of a leading rule; collapse it
            fmt.setLineHeight(1, QTextBlockFormat.LineHeightTypes.FixedHeight.value)
            fmt.setBottomMargin(0)
        elif block.blockFormat().headingLevel():
            fmt.setTopMargin(20 if block.previous().isValid() else 0)
            fmt.setBottomMargin(6)
        elif block.textList() is None and cursor.currentTable() is None:
            fmt.setBottomMargin(12)
        cursor.mergeBlockFormat(fmt)

        # Qt draws no quote bar, so mute the text to set quotes apart from the list above them
        if block.blockFormat().hasProperty(QTextFormat.Property.BlockQuoteLevel):
            quote = QTextCharFormat()
            quote.setForeground(QBrush(QColor(QUOTE)))
            cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
            cursor.mergeCharFormat(quote)

        block = block.next()

    for frame in doc.rootFrame().childFrames():
        if isinstance(frame, QTextTable):
            style_table(frame)


def centered(widget: QWidget) -> QHBoxLayout:
    """A row that centers `widget` and caps its width at COLUMN_WIDTH."""
    widget.setMaximumWidth(COLUMN_WIDTH)
    row = QHBoxLayout()
    row.setContentsMargins(8, 0, 8, 0)
    row.addStretch(1)
    row.addWidget(widget, 100)
    row.addStretch(1)
    return row


class Worker(QObject):
    """Runs `job` on a background thread and hands what it yields back to the GUI thread as signals.

    A daemon thread, not a QThread: closing the window mustn't wait on a model that is still
    thinking, and Qt aborts the whole app if a QThread is destroyed while it runs. Exactly one of
    `succeeded` and `failed` is emitted, after every `yielded`.

    (Not called `event` or `thread`: those are QObject methods, and shadowing them is asking for trouble.)
    """

    yielded = pyqtSignal(object)  # each item `job` yields
    succeeded = pyqtSignal()
    failed = pyqtSignal(str)  # `job` raised; carries what it said

    def __init__(self, job: Callable[[], Iterable | None]) -> None:
        super().__init__()
        self.job = job
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def wait(self, timeout: float) -> None:
        self._thread.join(timeout)

    def _run(self) -> None:
        try:
            for item in self.job() or ():
                self.yielded.emit(item)
        except Exception as e:  # PyQt6 aborts the app over an exception nobody caught
            self.failed.emit(f"{type(e).__name__}: {e}")
        else:
            self.succeeded.emit()


class StatusPoller(QObject):
    """Asks the session for its status (Laya, the embedding model, the expert) on a thread of its
    own - it talks to the Laya server and Ollama - and hands the answer back on the GUI thread.
    One ask at a time; a tick that comes while one is out is skipped."""

    ready = pyqtSignal(dict)

    def __init__(self) -> None:
        super().__init__()
        self.busy = False

    def poll(self, session: Session | None) -> None:
        if session is None or self.busy or not hasattr(session, "status"):
            return
        self.busy = True

        def run() -> None:
            try:
                status = session.status()
            except Exception as e:  # the bar says so; nothing here may take the window down
                status = {"error": str(e)}
            self.busy = False
            self.ready.emit(status)

        threading.Thread(target=run, name="nuru-status", daemon=True).start()


def _pixel_ratio() -> float:
    app = QApplication.instance()
    screen = app.primaryScreen() if app is not None else None
    return max(2.0, screen.devicePixelRatio()) if screen is not None else 2.0


@functools.lru_cache(maxsize=256)
def icon_pixmap(name: str, color: str, px: int = ICON_PX, angle: int = 0) -> QPixmap:
    """A status-bar icon `px` points square, drawn in `color` and turned `angle` degrees - sharp on
    a Retina screen. `lantern` is Nuru's own logo, tinted; anything else is a Lucide SVG."""
    ratio = _pixel_ratio()
    side = round(px * ratio)
    canvas = QPixmap(side, side)
    canvas.fill(QColor(Qt.GlobalColor.transparent))
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if angle:
        painter.translate(side / 2, side / 2)
        painter.rotate(angle)
        painter.translate(-side / 2, -side / 2)
    if name == "lantern":
        lantern = QPixmap(str(LOGO_PATH)).scaledToHeight(side, Qt.TransformationMode.SmoothTransformation)
        painter.drawPixmap(QPointF((side - lantern.width()) / 2, 0), lantern)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)  # the logo's shape, in color
        painter.fillRect(canvas.rect(), QColor(color))
    else:
        svg = (ICONS_DIR / f"{name}.svg").read_text().replace("currentColor", color)
        QSvgRenderer(QByteArray(svg.encode())).render(painter, QRectF(0, 0, side, side))
    painter.end()
    canvas.setDevicePixelRatio(ratio)
    return canvas


class TrayItem(QWidget):
    """One item of the status bar: an icon in its state's color, and at most a few characters
    beside it (a name or a count). What it means in words is its tooltip."""

    def __init__(self, icon: str, color: str = OFF_DOT, text: str = "", tip: str = "") -> None:
        super().__init__()
        self.icon = QLabel()
        self.label = QLabel()
        self.label.setObjectName("statusItem")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        row.addWidget(self.icon)
        row.addWidget(self.label)
        self.icon_name, self.color, self.angle = "", "", 0
        self.set(icon, color, text, tip)

    def set(self, icon: str, color: str, text: str = "", tip: str = "", angle: int = 0) -> None:
        if (icon, color, angle) != (self.icon_name, self.color, self.angle):
            self.icon_name, self.color, self.angle = icon, color, angle
            self.icon.setPixmap(icon_pixmap(icon, color, ICON_PX, angle))
        if self.label.text() != text:
            self.label.setText(text)
        self.label.setVisible(bool(text))
        self.setToolTip(tip)

    def text(self) -> str:
        return self.label.text()


def status_items(status: dict) -> dict[str, tuple[str, str, str, str]]:
    """The status bar's items as (icon, color, text, tooltip), from Session.status(). The icon says
    what it is and its color how it is (STATE_DOTS; the README has the key); text only where it
    carries a name or a number."""
    items: dict[str, tuple[str, str, str, str]] = {}

    model = status.get("model") or {}
    if model:
        state = model.get("state", "unknown")
        tip = {"loaded": "loaded in Ollama", "idle": "not loaded - loads with the next message"}.get(state, state)
        items["model"] = ("lantern", STATE_DOTS.get(state, BAD_DOT), model.get("name", ""),
                          f"Nuru's model, {model.get('name', '')}: {model.get('error') or tip}")

    laya = status.get("laya") or {}
    state = laya.get("state", "unknown")
    items["laya"] = ("signpost", STATE_DOTS.get(state, BAD_DOT), "", f"Laya, the decision layer: {laya.get('error') or state}")

    embedding = status.get("embedding") or {}
    state, model_name = embedding.get("state", "unknown"), embedding.get("model", "")
    words = {"idle": "not loaded - loads when a search or the indexer needs it",
             "loaded": "loaded (Ollama unloads it after 5 minutes unused)",
             "embedding": f"embedding {embedding.get('pending', 0)} notes", "no vault": "no vault index"}.get(state, state)
    items["embedding"] = ("waypoints", STATE_DOTS.get(state, BAD_DOT), "",
                          f"Embeddings{' (' + model_name + ')' if model_name else ''}: {embedding.get('error') or words}")

    expert = status.get("expert") or {}
    if expert.get("key"):
        left, limit = expert.get("turn_limit", 0) - expert.get("turn_used", 0), expert.get("turn_limit", 0)
        items["expert"] = ("graduation-cap", OK_DOT if left else WARN_DOT, f"{left}/{limit}",
                           f"The expert: {left} of {limit} answers left this turn")
        if expert.get("planner_cap") is not None:
            planner_left = expert["planner_cap"] - (expert.get("planner_used_today") or 0)
            items["planner"] = ("calendar", OK_DOT if planner_left else WARN_DOT, str(planner_left),
                                f"The expert planner (called at the step cap): {planner_left} of {expert['planner_cap']} left today")
    else:
        items["expert"] = ("graduation-cap", OFF_DOT, "", "The expert: no API key - set ANTHROPIC_API_KEY in .env.deep-think")

    think = status.get("think")
    level = think if isinstance(think, str) else ("on" if think else "off")
    items["think"] = ("brain", {"off": OFF_DOT, "low": MUTED}.get(level, INK), "", f"Thinking: {level}")

    enforced = status.get("enforced") or []
    items["shield"] = ("shield-check" if enforced else "shield", OK_DOT if enforced else OFF_DOT, "",
                       "Enforced: " + (", ".join(enforced) if enforced else "nothing - every check is in shadow"))
    if "error" in status and len(status) == 1:
        items = {"laya": ("signpost", BAD_DOT, "", f"Status unavailable: {status['error']}")}
    return items


class Logo(QWidget):
    """The lantern logo. Given a `swing` angle, it rocks from its handle while started."""

    PAD = 3  # room above and below so a swinging lantern isn't clipped

    def __init__(self, height: int, swing: float = 0) -> None:
        super().__init__()
        self.source = QPixmap(str(LOGO_PATH))
        if self.source.isNull():
            raise FileNotFoundError(f"Logo image not found: {LOGO_PATH}")
        self.logo_height = height
        self.swing = swing
        self.phase = 0.0
        self.scaled: QPixmap | None = None  # the image scaled for this screen; rebuilt if the pixel ratio changes

        width = math.ceil(height * self.source.width() / self.source.height())
        self.reach = math.ceil(height * math.sin(math.radians(swing)))  # how far the bottom swings sideways
        self.setFixedSize(width + 2 * self.reach, height + 2 * self.PAD)
        self.timer = QTimer(self)
        self.timer.setInterval(30)
        self.timer.timeout.connect(self.rock)

    def start(self) -> None:
        self.timer.start()

    def stop(self) -> None:
        self.timer.stop()
        self.phase = 0.0  # hang straight again
        self.update()

    def rock(self) -> None:
        self.phase += 0.13
        self.update()

    def paintEvent(self, event) -> None:
        ratio = self.devicePixelRatioF()
        if self.scaled is None or self.scaled.devicePixelRatio() != ratio:
            self.scaled = self.source.scaledToHeight(
                round(self.logo_height * ratio), Qt.TransformationMode.SmoothTransformation
            )
            self.scaled.setDevicePixelRatio(ratio)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.translate(self.width() / 2, self.PAD)  # pivot at the top of the handle
        painter.rotate(self.swing * math.sin(self.phase))
        painter.drawPixmap(QPointF(-self.scaled.width() / ratio / 2, 0), self.scaled)


class SendButton(QPushButton):
    """Rounded square with an up arrow, or a stop square while a reply is streaming."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("send")
        self.setFixedSize(32, 32)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stopping = False

    def paintEvent(self, event) -> None:
        super().paintEvent(event)  # the stylesheet draws the background
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = self.width() / 2, self.height() / 2
        if self.stopping:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("white"))
            painter.drawRoundedRect(QRectF(cx - 5, cy - 5, 10, 10), 1, 1)
            return
        pen = QPen(QColor("white"), 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(QPointF(cx, cy + 6), QPointF(cx, cy - 6))
        painter.drawPolyline(QPolygonF([QPointF(cx - 5, cy - 1), QPointF(cx, cy - 6), QPointF(cx + 5, cy - 1)]))


class PromptEdit(QTextEdit):
    """Auto-growing input: Enter submits, Shift+Enter inserts a newline."""

    submitted = pyqtSignal()
    MIN_HEIGHT = 30
    MAX_HEIGHT = 200

    def __init__(self) -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.viewport().setAutoFillBackground(False)
        self.setAcceptRichText(False)
        self.setFont(font(15))
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Text, QColor(TEXT))
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(MUTED))
        self.setPalette(palette)
        self.document().documentLayout().documentSizeChanged.connect(self.fit_height)
        self.fit_height(self.document().size())

    def fit_height(self, size: QSizeF) -> None:
        self.setFixedHeight(min(max(math.ceil(size.height()), self.MIN_HEIGHT), self.MAX_HEIGHT))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        is_enter = event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        if is_enter and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.submitted.emit()
        else:
            super().keyPressEvent(event)


class MarkdownView(QTextBrowser):
    """Read-only markdown that grows to fit its content instead of scrolling."""

    def __init__(self) -> None:
        super().__init__()
        self.content: str | None = None  # what it shows now, so unchanged text isn't parsed and styled again
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.viewport().setAutoFillBackground(False)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setOpenLinks(False)  # the text comes from a model, so links are vetted first
        self.anchorClicked.connect(open_link)
        self.setFont(font(16, SERIF, QFont.StyleHint.Serif))
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Text, QColor(TEXT))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(BORDER))  # Qt draws horizontal rules with this
        self.setPalette(palette)
        self.document().setDocumentMargin(0)
        self.document().documentLayout().documentSizeChanged.connect(self.fit_height)

    def fit_height(self, size: QSizeF) -> None:
        self.setFixedHeight(math.ceil(size.height()))

    def set_content(self, part: Prose) -> None:
        if part.text == self.content:
            return
        self.content = part.text
        self.setMarkdown(part.text)
        style_document(self.document())


class BubbleLabel(QLabel):
    """Word-wrapped label that hugs its text up to a max width (QLabel's own hint squeezes short text)."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setMinimumWidth(1)  # clip a long unbroken word rather than let it widen the whole column

    def sizeHint(self) -> QSize:
        flags = Qt.TextFlag.TextWordWrap.value
        bounds = self.fontMetrics().boundingRect(0, 0, BUBBLE_WIDTH - 32, 100000, flags, self.text())
        return QSize(bounds.width() + 2, self.heightForWidth(bounds.width() + 2))


class UserMessage(QWidget):
    """The user's text in a rounded bubble, right-aligned and sized to its content."""

    def __init__(self, text: str) -> None:
        super().__init__()
        label = BubbleLabel(text)
        label.setObjectName("userText")
        label.setFont(font(15))
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        bubble = QFrame()
        bubble.setObjectName("userBubble")
        inner = QVBoxLayout(bubble)
        inner.setContentsMargins(13, 9, 13, 9)
        inner.addWidget(label)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        row.addWidget(bubble)


class CodeBlock(QFrame):
    """Fenced code in a shaded, rounded box with a language label and a copy button."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("codeBlock")
        self.language = QLabel()
        self.language.setObjectName("codeLanguage")
        self.copy = QPushButton("Copy")
        self.copy.setObjectName("copy")
        self.copy.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy.clicked.connect(self.copy_code)
        self.code = QLabel()
        self.code.setObjectName("codeText")
        self.code.setFont(font(13, MONO, QFont.StyleHint.Monospace))
        self.code.setWordWrap(True)
        self.code.setMinimumWidth(1)  # clip a long unbroken token rather than widen the whole column
        self.code.setTextFormat(Qt.TextFormat.PlainText)
        self.code.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        # Owned by the block, so it can't fire after the block is gone
        self.copied_timer = QTimer(self)
        self.copied_timer.setSingleShot(True)
        self.copied_timer.setInterval(1500)
        self.copied_timer.timeout.connect(self.reset_copy_label)

        header = QHBoxLayout()
        header.addWidget(self.language)
        header.addStretch(1)
        header.addWidget(self.copy)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 10, 12)
        layout.setSpacing(6)
        layout.addLayout(header)
        layout.addWidget(self.code)

    def set_content(self, part: Code) -> None:
        self.language.setText(part.language or "text")
        self.code.setText(part.source)

    def copy_code(self) -> None:
        QApplication.clipboard().setText(self.code.text())
        self.copy.setText("Copied")
        self.copied_timer.start()

    def reset_copy_label(self) -> None:
        self.copy.setText("Copy")


class ReplyHeader(QFrame):
    """The card above a reply: what Nuru is doing, with its working folded under it - the tool
    calls it made, kept together at the top, and what it reasoned below them.

    It stays open while the reply streams, so the working can be watched as it happens, and shuts
    itself once the reply lands, leaving one line that says what was done. A reply with no working
    behind it has nothing to fold away, so the card goes with it.

    Each call is marked as it ends - ✓ ran, ✗ the tool reported an error, ⊘ not run (a check or a
    limit stopped it) - with the reason after it. A watch stop is its own row, not a retry. While
    the turn runs, the line counts the thinking against the watch's budget and the steps against
    the step cap. What Laya said at each point is kept in rows shown only in the developer view.
    """

    OPEN, SHUT = "▾", "▸"  # the chevron on the line, pointing down while the working is showing
    MARKS = {"ok": "✓", "error": "✗", "blocked": "⊘"}
    PENDING = "·"  # a call still running

    def __init__(self, developer: bool = False) -> None:
        super().__init__()
        self.setObjectName("replyHeader")
        self.status = "Thinking…"
        self.expanded = True
        self.finished = False
        self.developer = developer  # show what Laya said at each point
        self.rows: list[QLabel] = []  # the calls, then the reasoning; each group in its own order
        self.tools = 0  # how many of those rows are tool calls, and so where the reasoning starts
        self.calls: list[tuple[str, QLabel, str]] = []  # (tool, its row, the row's text) - to mark on the result
        self.results: dict[str, int] = {"ok": 0, "error": 0, "blocked": 0}
        self.expert_answers = 0
        self.steers = 0  # calls the thinking watch stopped
        self.retried = False  # a silent call was asked again with thinking off
        self.laya_rows: list[QLabel] = []

        self.toggle = QPushButton()
        self.toggle.setObjectName("replyToggle")
        self.toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.toggle.clicked.connect(self.flip)

        self.rows_box = QWidget()
        self.rows_column = QVBoxLayout(self.rows_box)
        self.rows_column.setContentsMargins(12, 0, 0, 2)
        self.rows_column.setSpacing(4)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 7, 16, 7)
        layout.setSpacing(6)
        layout.addWidget(self.toggle)
        layout.addWidget(self.rows_box)
        self.draw()

    def add_call(self, call: ToolCall) -> None:
        # Calls join the ones before them, whenever they arrive: what Nuru did is easier to take in
        # at the top than scattered through reasoning that runs long.
        text = f"tool: {format_tool_call(call.name, call.arguments)}"
        label = self.add_row(f"{self.PENDING} {text}", wrap=False, at=self.tools)
        self.calls.append((call.name, label, text))
        self.tools += 1

    def add_result(self, result: ToolResult) -> None:
        """Mark the latest unmarked call to that tool with how it ended."""
        for i in range(len(self.calls) - 1, -1, -1):
            name, label, text = self.calls[i]
            if name == result.name and label.text().startswith(self.PENDING):
                mark = self.MARKS.get(result.status, "?")
                label.setText(f"{mark} {text}" + (f"  — {result.detail}" if result.detail else ""))
                label.setToolTip(result.detail)
                break
        self.results[result.status] = self.results.get(result.status, 0) + 1
        if result.name == "ask_an_expert" and result.status == "ok" and result.detail.startswith("answer "):
            self.expert_answers += 1

    def add_thinking(self, thinking: Thinking) -> None:
        # Reasoning is prose and runs long, so it wraps rather than being cut off like a call
        self.add_row(f"thinking: {thinking.text}", wrap=True, at=len(self.rows))

    def add_steer(self, steered: Steered) -> None:
        # The thinking watch stopped the reasoning - not a silent call - so it says so in its own words.
        self.steers += 1
        self.add_row(f"reasoning stopped ({steered.reason}) - asked to act, thinking off for one call", wrap=False,
                     at=len(self.rows))

    def add_retry(self, _retry: RetryWithoutThinking) -> None:
        # Where the reasoning got the answer but the reply didn't, so the turn was run again with
        # thinking off. It goes in with the working, and on the line that outlives it (see finish).
        self.retried = True
        self.add_row("retried with thinking off", wrap=False, at=len(self.rows))

    def add_laya(self, decision: LayaDecision) -> None:
        label = QLabel(f"laya · {decision.point}: {decision.summary}")
        label.setObjectName("layaRow")
        label.setFont(font(11, MONO, QFont.StyleHint.Monospace))
        label.setMinimumWidth(1)
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.laya_rows.append(label)
        self.rows_column.addWidget(label)  # after everything: in the order it was said
        self.draw()

    def show_progress(self, progress: Progress) -> None:
        """While the turn runs: "Thinking… ~600 / 1,200 tokens · step 3 of 10"."""
        if self.finished:
            return
        parts = []
        if progress.thinking_tokens:
            budget = f" / {progress.budget:,}" if progress.budget else ""
            parts.append(f"~{progress.thinking_tokens:,}{budget} tokens")
        parts.append(f"step {progress.step}" + (f" of {progress.step_cap}" if progress.step_cap else ""))
        self.status = "Thinking… " + " · ".join(parts)
        self.draw()

    def add_row(self, text: str, wrap: bool, at: int) -> QLabel:
        label = QLabel(text)
        label.setObjectName("workRow")
        label.setFont(font(12, MONO, QFont.StyleHint.Monospace))
        label.setMinimumWidth(1)  # an unwrapped row is clipped rather than widening the column
        label.setWordWrap(wrap)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.rows.insert(at, label)
        self.rows_column.insertWidget(at, label)
        self.draw()
        return label

    def set_developer(self, on: bool) -> None:
        self.developer = on
        if self.finished:
            self.setVisible(self.has_rows)
        self.draw()

    @property
    def has_rows(self) -> bool:
        return bool(self.rows) or (self.developer and bool(self.laya_rows))

    def flip(self) -> None:
        self.expanded = not self.expanded
        self.draw()

    def finish(self) -> None:
        """The reply has landed: say what was done, in the past, and fold it away."""
        self.finished = True
        if self.tools:
            self.status = f"Used {self.tools} tool" + ("" if self.tools == 1 else "s")
            problems = [f"{n} {word}" for n, word in ((self.results.get("blocked", 0), "blocked"),
                                                      (self.results.get("error", 0), "failed")) if n]
            if problems:
                self.status += f" ({', '.join(problems)})"
        else:
            self.status = "Thought about it"  # it reasoned its way there without calling anything
        if self.expert_answers:
            self.status += ", asked the expert" + ("" if self.expert_answers == 1 else f" {self.expert_answers} times")
        if self.steers:
            self.status += ", reasoning stopped " + {1: "once", 2: "twice"}.get(self.steers, f"{self.steers} times")
        if self.retried:
            # The reply came from a second call, so say so where it stays readable once this folds.
            self.status += ", retried with thinking off"
        self.expanded = False
        self.setVisible(self.has_rows)  # nothing was done: the card has nothing left to say
        self.draw()

    def draw(self) -> None:
        """Redraw the line and the working under it. With nothing to show there is nothing to open,
        so the chevron stays off and clicking the line does nothing."""
        chevron = f"{self.OPEN if self.expanded else self.SHUT}  " if self.has_rows else ""
        self.toggle.setText(chevron + self.status)
        self.toggle.setCursor(Qt.CursorShape.PointingHandCursor if self.has_rows else Qt.CursorShape.ArrowCursor)
        self.rows_box.setVisible(self.expanded and self.has_rows)
        for label in self.laya_rows:
            label.setVisible(self.developer)


class AssistantMessage(QWidget):
    """Nuru's reply in a white card, styled like the composer: markdown text and code boxes,
    with the logo at the bottom rocking until the reply is done. Its header is a smaller card above it."""

    def __init__(self, developer: bool = False) -> None:
        super().__init__()
        self.parts: list[MarkdownView | CodeBlock] = []  # in order, ahead of the logo
        self.logo = Logo(19, swing=SWING)
        self.logo.start()
        self.header = ReplyHeader(developer)
        self.notice_title: QLabel | None = None  # set when Nuru ended the turn itself

        body = self.body = QFrame()
        body.setObjectName("reply")
        self.column = QVBoxLayout(body)
        self.column.setContentsMargins(16, 13, 16, 11)
        self.column.setSpacing(10)
        self.column.addWidget(self.logo, 0, Qt.AlignmentFlag.AlignLeft)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.header)
        layout.addWidget(body)

    def set_markdown(self, text: str) -> None:
        parts = split_fences(text)
        for i, part in enumerate(parts):
            cls = MarkdownView if isinstance(part, Prose) else CodeBlock
            if i >= len(self.parts) or type(self.parts[i]) is not cls:
                if i < len(self.parts):  # a half-typed fence just became real code: swap the widget
                    self.discard(self.parts.pop(i))
                self.parts.insert(i, cls())
                self.column.insertWidget(i + (self.notice_title is not None), self.parts[i])  # below the notice line
            self.parts[i].set_content(part)
        for stale in self.parts[len(parts) :]:
            self.discard(stale)
        del self.parts[len(parts) :]

    @staticmethod
    def discard(widget: QWidget) -> None:
        widget.setParent(None)
        widget.deleteLater()

    def set_notice(self, clarify: bool) -> None:
        """Nuru ended this turn itself: the card turns warm, and a line above the text says so -
        or, when it ends with a question, that it's waiting for the user."""
        if self.notice_title is not None:
            return
        self.body.setObjectName("notice")
        self.body.style().unpolish(self.body)
        self.body.style().polish(self.body)
        self.notice_title = QLabel("Nuru stopped - waiting for your answer" if clarify else "Nuru stopped this turn")
        self.notice_title.setObjectName("noticeTitle")
        self.column.insertWidget(0, self.notice_title)

    def finish(self) -> None:
        self.logo.stop()
        self.header.finish()


class HistoryPanel(QFrame):
    """Past conversations, most recent first, under Today / Yesterday / Previous 7 days / Older.
    Clicking one asks to continue it; the pen starts a new one. Each is listed by its title - the
    model's summary of its first prompt (core.titles) - or that prompt cut short until it has one."""

    opened = pyqtSignal(int)
    new_chat = pyqtSignal()
    WIDTH = 232

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("history")
        self.setFixedWidth(self.WIDTH)
        title = QLabel("History")
        title.setObjectName("historyTitle")
        self.new_button = QPushButton()
        self.new_button.setObjectName("iconButton")
        self.new_button.setIcon(QIcon(icon_pixmap("square-pen", MUTED)))
        self.new_button.setIconSize(QSize(ICON_PX, ICON_PX))
        self.new_button.setToolTip("New chat")
        self.new_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.new_button.clicked.connect(self.new_chat)
        header = QHBoxLayout()
        header.setContentsMargins(12, 10, 8, 4)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.new_button)

        self.list = QListWidget()
        self.list.setObjectName("historyList")
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list.itemClicked.connect(self.on_clicked)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(header)
        layout.addWidget(self.list, 1)

    @staticmethod
    def group(last_at: str | None, today) -> str:
        try:
            day = datetime.fromisoformat((last_at or "").replace("Z", "+00:00")).astimezone().date()
        except ValueError:
            return "Older"
        if day == today:
            return "Today"
        if day == today - timedelta(days=1):
            return "Yesterday"
        return "Previous 7 days" if day >= today - timedelta(days=7) else "Older"

    def show_conversations(self, conversations: list[dict], current: int | None) -> None:
        self.list.clear()
        today, shown_group = datetime.now().astimezone().date(), None
        for conversation in conversations:
            group = self.group(conversation.get("last_at"), today)
            if group != shown_group:
                heading = QListWidgetItem(group)
                heading.setFlags(Qt.ItemFlag.NoItemFlags)  # a label, not something to click
                heading.setForeground(QColor(MUTED))
                heading_font = font(11)
                heading.setFont(heading_font)
                self.list.addItem(heading)
                shown_group = group
            prompt = conversation.get("first_prompt") or ""
            item = QListWidgetItem(conversation.get("title") or titles.fallback(prompt) or "Untitled")
            item.setData(Qt.ItemDataRole.UserRole, conversation["id"])
            item.setToolTip(prompt[:300])
            self.list.addItem(item)
            if conversation["id"] == current:
                item.setSelected(True)
                self.list.setCurrentItem(item)

    def on_clicked(self, item: QListWidgetItem) -> None:
        conversation_id = item.data(Qt.ItemDataRole.UserRole)
        if conversation_id is not None:
            self.opened.emit(conversation_id)

    def conversation_ids(self) -> list[int]:
        items = (self.list.item(i) for i in range(self.list.count()))
        return [i.data(Qt.ItemDataRole.UserRole) for i in items if i.data(Qt.ItemDataRole.UserRole) is not None]


class ChatWindow(QWidget):
    FLUSH_MS = 25  # streamed text is redrawn at most this often, however fast it arrives
    TITLE = "Nuru"
    SPIN_STEPS = 12  # the index icon turns through these while notes are being embedded
    STATUS_MS = 3000  # how often the status bar asks after Laya, the embedding model and the expert
    SPIN_MS = 100
    # Re-embedding one note takes ~0.1 s - less than a frame of the spinner. So the spinner stays at
    # least this long once shown, and a finished pass is then named in the title for a while.
    MIN_SPIN_SECONDS = 1.5
    DONE_SECONDS = 3.0
    CLOSE_WAIT_SECONDS = 5  # how long closing waits for a reply in flight to save what it has

    def __init__(self, start_session: Callable[[], Session]) -> None:
        super().__init__()
        self.setWindowTitle(self.TITLE)
        self.resize(960, 720)
        self.setMinimumSize(480, 420)
        self.setObjectName("chat")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setStyleSheet(STYLE)

        self.start_session = start_session
        self.session: Session | None = None  # None until start() has brought it up, and again once closed
        self.starting: Worker | None = None
        self.worker: Worker | None = None  # the reply in flight: set exactly while a reply is streaming
        self.cancel = threading.Event()  # set to stop the reply in flight
        self.message: AssistantMessage | None = None  # the bubble that reply is drawn in
        self.reply = Reply()  # what that reply has said so far
        self.following = True  # keep the newest text in view unless the user scrolls up

        self.flush_timer = QTimer(self)
        self.flush_timer.setSingleShot(True)
        self.flush_timer.setInterval(self.FLUSH_MS)
        self.flush_timer.timeout.connect(self.render_reply)

        # The background indexer's progress, in the status bar at the bottom. Runs from on_started,
        # once there's a session to ask.
        self.spin_frame = 0
        self.spin_started: float | None = None  # when the spinner went up; None while it's down
        self.spin_count = 0  # the most notes seen pending in the pass the spinner is showing
        self.shown_changes = 0  # the index_change count the title has already announced
        self.done_text = ""  # what the finished pass did, shown once the spinner comes down
        self.done_until = 0.0
        self.index_timer = QTimer(self)
        self.index_timer.setInterval(self.SPIN_MS)
        self.index_timer.timeout.connect(self.update_index)

        self.developer = False  # the developer view: what Laya said at each point, in each reply's card
        self.replies: list[AssistantMessage] = []
        self.poller = StatusPoller()
        self.poller.ready.connect(self.show_status)
        self.status_timer = QTimer(self)
        self.status_timer.setInterval(self.STATUS_MS)
        self.status_timer.timeout.connect(lambda: self.poller.poll(self.session))

        # Empty state: a greeting above the composer. Until the session is up it's a rocking lantern
        # beside "Starting Nuru…" instead, and the composer stays hidden.
        self.greeting_title = QLabel("Starting Nuru…")
        self.greeting_title.setObjectName("greeting")
        self.greeting_title.setFont(font(34, SERIF, QFont.StyleHint.Serif))
        self.greeting_logo = Logo(35, swing=SWING)
        self.greeting_logo.start()
        self.greeting = QWidget()
        greeting_row = QHBoxLayout(self.greeting)
        greeting_row.setContentsMargins(0, 0, 0, 20)
        greeting_row.setSpacing(14 - self.greeting_logo.reach)  # the room the lantern has to swing isn't part of the gap
        greeting_row.addStretch(1)
        greeting_row.addWidget(self.greeting_logo)
        greeting_row.addWidget(self.greeting_title)
        greeting_row.addStretch(1)

        # Conversation: a scrolling column of messages, centered and capped in width
        column = QWidget()
        self.messages = QVBoxLayout(column)
        self.messages.setContentsMargins(0, 16, 0, 16)
        self.messages.setSpacing(18)
        self.messages.addStretch(1)
        content = QWidget()
        content.setLayout(centered(column))
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(content)
        self.scroll.viewport().setAutoFillBackground(False)  # let the window's background show through
        content.setAutoFillBackground(False)
        self.scroll.hide()
        bar = self.scroll.verticalScrollBar()
        bar.rangeChanged.connect(self.on_range_changed)
        bar.valueChanged.connect(self.on_scrolled)

        # Composer: rounded box with the input and the send/stop button
        self.prompt = PromptEdit()
        self.prompt.setPlaceholderText("How can I help you today?")
        self.send = SendButton()
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.send)
        composer = QFrame()
        composer.setObjectName("composer")
        composer_layout = QVBoxLayout(composer)
        composer_layout.setContentsMargins(14, 10, 10, 10)
        composer_layout.setSpacing(6)
        composer_layout.addWidget(self.prompt)
        composer_layout.addLayout(buttons)

        self.dock = QWidget()
        self.dock_layout = QVBoxLayout(self.dock)
        self.dock_layout.setContentsMargins(0, 4, 0, 6)
        self.dock_layout.addLayout(centered(composer))
        # Hidden until the session is up, but its space stays reserved so the greeting doesn't jump
        # when the composer appears.
        keep_space = self.dock.sizePolicy()
        keep_space.setRetainSizeWhenHidden(True)
        self.dock.setSizePolicy(keep_space)
        self.dock.hide()

        # Status bar along the bottom, like an editor's: indexing, Laya, embeddings, the expert, the
        # thinking level, and the developer-view switch.
        self.tray = QFrame()
        self.tray.setObjectName("statusBar")
        tray_row = QHBoxLayout(self.tray)
        tray_row.setContentsMargins(14, 4, 10, 4)
        tray_row.setSpacing(14)
        self.history_toggle = QPushButton()
        self.history_toggle.setObjectName("iconButton")
        self.history_toggle.setCheckable(True)
        self.history_toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.history_toggle.setIcon(QIcon(icon_pixmap("panel-left", MUTED)))
        self.history_toggle.setIconSize(QSize(ICON_PX, ICON_PX))
        self.history_toggle.setToolTip("Show or hide the history")
        self.history_toggle.toggled.connect(self.set_history_visible)
        tray_row.addWidget(self.history_toggle)
        self.tray_items: dict[str, TrayItem] = {}
        for key, icon in (("model", "lantern"), ("index", "book-open"), ("laya", "signpost"), ("embedding", "waypoints"),
                          ("expert", "graduation-cap"), ("planner", "calendar"), ("think", "brain"), ("shield", "shield")):
            item = TrayItem(icon, OFF_DOT)
            item.setVisible(key != "planner")  # shown once a status says there is a planner
            self.tray_items[key] = item
            tray_row.addWidget(item)
        self.tray_index = self.tray_items["index"]
        tray_row.addStretch(1)
        self.dev_toggle = QPushButton()
        self.dev_toggle.setObjectName("devToggle")
        self.dev_toggle.setCheckable(True)
        self.dev_toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.dev_toggle.setToolTip("Laya view: show what the decision layer said at each step (in shadow it changes nothing)")
        self.dev_toggle.setIconSize(QSize(ICON_PX, ICON_PX))
        self.dev_toggle.setIcon(QIcon(icon_pixmap("bug", MUTED)))
        self.dev_toggle.toggled.connect(self.set_developer)
        tray_row.addWidget(self.dev_toggle)

        # The history panel on the left, the chat beside it, the status bar under both.
        self.history = HistoryPanel()
        self.history.opened.connect(self.open_conversation)
        self.history.new_chat.connect(self.new_chat)
        self.titling: Worker | None = None  # the model writing a conversation's title, between turns
        self.send_after_title = False  # a message was sent while a title was being written: it goes next
        chat_area = QWidget()
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self.history)
        body.addWidget(chat_area, 1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addLayout(body, 1)

        # The composer sits at the bottom from the start, as in a chat. Empty state centers the greeting
        # in the space above it; chat state gives that space to the scroll area.
        self.root = QVBoxLayout(chat_area)
        self.root.setContentsMargins(0, 0, 0, 0)
        self.root.setSpacing(0)
        self.root.addStretch(1)
        self.root.addWidget(self.greeting)
        self.root.addStretch(1)
        self.root.addWidget(self.scroll, 1)
        self.root.addWidget(self.dock)
        outer.addWidget(self.tray)

        shown = self.settings().value("history/visible", True, type=bool)
        self.history_toggle.setChecked(shown)
        self.history.setVisible(shown)

        self.prompt.submitted.connect(self.submit)
        self.prompt.textChanged.connect(self.update_send)
        self.send.clicked.connect(self.on_send_clicked)
        self.update_send()
        self.prompt.setFocus()

    def start(self) -> None:
        """Bring the session up in the background. The lantern rocks and the composer stays hidden until it's ready."""
        self.starting = Worker(self.open_session)
        self.starting.succeeded.connect(self.on_started)
        self.starting.failed.connect(self.on_start_failed)
        self.starting.start()

    def open_session(self) -> None:
        # Runs on the worker thread. The GUI thread doesn't look at `session` for anything that
        # matters until on_started, which the worker signals only after this returns.
        self.session = self.start_session()

    def on_started(self) -> None:
        self.index_timer.start()
        self.status_timer.start()
        self.poller.poll(self.session)
        self.refresh_history()
        self.greeting_title.setText(time_greeting(datetime.now().hour))
        self.greeting_logo.stop()
        self.dock.show()
        self.update_send()
        self.prompt.setFocus()

    def update_index(self) -> None:
        """The status bar's index item: turning arrows and the count ("12") while the indexer
        embeds, then the book and "+12" for DONE_SECONDS, else the book alone.

        The spinner stays up at least MIN_SPIN_SECONDS once shown. A pass that finished between two
        ticks - too quick ever to be seen pending - still gets the spinner and the summary.
        """
        now = time.monotonic()
        session = self.session
        pending = session.indexing if session is not None else 0
        changes, summary = session.index_change if session is not None else (0, "")

        if pending:
            if self.spin_started is None:
                self.spin_started = now
            self.spin_count = max(self.spin_count, pending)
        if changes != self.shown_changes:
            self.shown_changes = changes
            self.done_text = summary
            if self.spin_started is None:
                self.spin_started = now  # over before a tick caught it: show the spinner anyway
        if self.spin_started is not None and not pending and now - self.spin_started >= self.MIN_SPIN_SECONDS:
            self.spin_started = None
            self.spin_count = 0
            if self.done_text:
                self.done_until = now + self.DONE_SECONDS

        if self.spin_started is not None:
            self.spin_frame = (self.spin_frame + 1) % self.SPIN_STEPS
            left = pending or self.spin_count
            self.tray_index.set("refresh-cw", WARN_DOT, str(left) if left else "",
                                f"Indexing {left} note{'' if left == 1 else 's'}" if left else "Indexing",
                                angle=self.spin_frame * 360 // self.SPIN_STEPS)
        elif self.done_text and now < self.done_until:
            count = re.search(r"\d+", self.done_text)
            self.tray_index.set("book-open", OK_DOT, f"+{count.group()}" if count else "", f"Vault index: {self.done_text}")
        else:
            self.done_text = ""
            self.tray_index.set("book-open", OK_DOT, "", "Vault index: up to date")

    def show_status(self, status: dict) -> None:
        for key, (icon, color, text, tip) in status_items(status).items():
            self.tray_items[key].set(icon, color, text, tip)
            self.tray_items[key].setVisible(True)

    # ------------------------------------------------------------------ the history panel
    @staticmethod
    def settings() -> QSettings:
        return QSettings("Nuru", "Nuru")

    def set_history_visible(self, on: bool) -> None:
        self.history.setVisible(on)
        self.settings().setValue("history/visible", on)

    def refresh_history(self) -> None:
        """Relist the conversations. Only between turns: the store's connection is the turn's too."""
        session = self.session
        if session is None or self.worker is not None or not hasattr(session, "conversations"):
            return
        self.history.show_conversations(session.conversations(), session.conversation_id)

    def busy(self) -> bool:
        return self.worker is not None or self.titling is not None

    def open_conversation(self, conversation_id: int) -> None:
        """Continue a past conversation: show its messages; the next one goes into it."""
        session = self.session
        if session is None or self.busy() or conversation_id == getattr(session, "conversation_id", None):
            return
        messages = session.open_conversation(conversation_id)
        self.clear_chat()
        for message in messages:
            if message["role"] == "user":
                self.add_message(UserMessage(message["content"]))
            else:
                reply = AssistantMessage(self.developer)
                self.replies.append(reply)
                self.add_message(reply)
                reply.set_markdown(message["content"])
                reply.finish()
        if messages:
            self.show_chat()
            self.following = True
        self.refresh_history()
        self.prompt.setFocus()

    def new_chat(self) -> None:
        session = self.session
        if session is None or self.busy():
            return
        session.new_conversation()
        self.clear_chat()
        self.show_empty()
        self.refresh_history()
        self.prompt.setFocus()

    def clear_chat(self) -> None:
        """Take every message off the screen (the stretch at the end of the column stays)."""
        while self.messages.count() > 1:
            widget = self.messages.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self.replies = []
        self.message = None

    def show_empty(self) -> None:
        """Back to the empty state: the greeting in the space above the composer."""
        self.scroll.hide()
        self.greeting.show()
        self.root.setStretch(0, 1)
        self.root.setStretch(2, 1)
        self.prompt.setPlaceholderText("How can I help you today?")

    def title_if_needed(self) -> None:
        """After a turn: have the model title the conversation if it has no title yet - off the GUI
        thread, and before the next message goes out (they share the model and the store)."""
        session = self.session
        if session is None or not hasattr(session, "title_conversation") or self.titling is not None:
            return
        titling = self.titling = Worker(lambda: (session.title_conversation(), None)[1])
        titling.succeeded.connect(self.on_titled)
        titling.failed.connect(lambda _message: self.on_titled())
        titling.start()
        self.update_send()

    def on_titled(self) -> None:
        self.titling = None
        self.update_send()
        self.refresh_history()
        if self.send_after_title:
            self.send_after_title = False
            self.submit()

    def set_developer(self, on: bool) -> None:
        self.developer = on
        self.dev_toggle.setIcon(QIcon(icon_pixmap("bug", "white" if on else MUTED)))
        for reply in self.replies:
            reply.header.set_developer(on)

    def on_start_failed(self, message: str) -> None:
        reply = Reply()
        reply.add_error(f"Nuru couldn't start. {message}")
        self.greeting_logo.stop()  # the greeting is about to be hidden, so nothing should keep it rocking
        self.dock.show()  # still off, but the window looks like the chat it would have been
        self.show_chat()
        self.prompt.setPlaceholderText("Nuru couldn't start")
        failure = AssistantMessage()
        self.add_message(failure)
        failure.set_markdown(reply.markdown)
        failure.finish()

    def show_chat(self) -> None:
        """Leave the empty state: hide the greeting and hand the space to the conversation."""
        self.greeting.hide()
        self.scroll.show()
        self.root.setStretch(0, 0)  # the stretches around the greeting
        self.root.setStretch(2, 0)
        self.prompt.setPlaceholderText("Reply to Nuru…")

    def add_message(self, widget: QWidget) -> None:
        self.messages.insertWidget(self.messages.count() - 1, widget)

    def update_send(self) -> None:
        streaming = self.worker is not None
        stopping = streaming and self.cancel.is_set()  # stop was pressed; waiting for the reply to wind down
        has_text = bool(self.prompt.toPlainText().strip())
        self.send.stopping = streaming
        self.send.setEnabled(self.session is not None and not stopping and (streaming or has_text))
        self.send.update()

    def on_send_clicked(self) -> None:
        if self.worker is not None:
            self.cancel.set()
            self.update_send()
        else:
            self.submit()

    def submit(self) -> None:
        text = self.prompt.toPlainText().strip()
        session = self.session
        if not text or session is None or self.worker is not None:
            return
        if self.titling is not None:  # a second or two: it goes out once the title is written
            self.send_after_title = True
            return

        self.prompt.clear()
        self.show_chat()
        self.following = True
        self.add_message(UserMessage(text))
        self.prompt.setPlaceholderText("Reply to Nuru…")  # a question Nuru asked has been answered
        self.message = AssistantMessage(self.developer)
        self.replies.append(self.message)
        self.add_message(self.message)
        self.reply = Reply()

        cancel = self.cancel = threading.Event()
        worker = self.worker = Worker(lambda: session.ask(text, cancel))
        worker.yielded.connect(self.on_event)
        worker.succeeded.connect(self.finish_reply)
        worker.failed.connect(self.on_reply_failed)
        worker.start()
        self.update_send()

    def on_event(self, event: Event) -> None:
        header = self.message.header
        if isinstance(event, ToolCall):
            header.add_call(event)
        elif isinstance(event, ToolResult):
            header.add_result(event)
        elif isinstance(event, Thinking):
            header.add_thinking(event)
        elif isinstance(event, Steered):  # before RetryWithoutThinking: a watch stop is one too
            header.add_steer(event)
        elif isinstance(event, RetryWithoutThinking):
            header.add_retry(event)
        elif isinstance(event, Progress):
            header.show_progress(event)
        elif isinstance(event, LayaDecision):
            header.add_laya(event)
        elif isinstance(event, Stopped):
            self.message.set_notice(event.clarify)
            if event.clarify:
                self.prompt.setPlaceholderText("Reply to clarify for Nuru…")
        self.reply.add(event)
        if not self.flush_timer.isActive():
            self.flush_timer.start()

    def render_reply(self) -> None:
        self.message.set_markdown(self.reply.markdown)

    def on_reply_failed(self, message: str) -> None:
        self.reply.add_error(message)
        self.finish_reply()

    def finish_reply(self) -> None:
        self.flush_timer.stop()
        self.render_reply()
        self.message.finish()
        self.worker = None
        self.update_send()
        self.poller.poll(self.session)  # the expert's answers left, and anything else a turn changed
        self.refresh_history()  # a new conversation appears; a continued one moves to the top
        self.title_if_needed()

    def on_range_changed(self, _low: int, high: int) -> None:
        bar = self.scroll.verticalScrollBar()
        # The scrollbar narrows the message column; narrow the composer by the same amount to stay aligned
        margins = self.dock_layout.contentsMargins()
        margins.setRight(bar.sizeHint().width() if high else 0)
        self.dock_layout.setContentsMargins(margins)
        if self.following:
            bar.setValue(high)

    def on_scrolled(self, value: int) -> None:
        self.following = value >= self.scroll.verticalScrollBar().maximum() - 4

    def closeEvent(self, event: QCloseEvent) -> None:
        """Closing the window is /exit: put the model away and stop Ollama if it was started for us."""
        self.index_timer.stop()
        self.status_timer.stop()
        self.cancel.set()
        if self.worker is not None:
            self.worker.wait(self.CLOSE_WAIT_SECONDS)  # lets a reply in flight save what it has
        if self.titling is not None:
            self.titling.wait(self.CLOSE_WAIT_SECONDS)

        session, self.session = self.session, None
        try:
            if session is not None:
                try:
                    session.shutdown("window closed")
                finally:
                    session.close()
        except Exception as e:  # an exception out of closeEvent would abort the app with the model still loaded
            print(f"Nuru: error while shutting down: {e}", file=sys.stderr)
        super().closeEvent(event)


def squircle(side: float) -> QPainterPath:
    """The rounded square macOS gives app icons, `side` across, drawn as a ring of points close
    enough together to curve."""
    radius = side / 2
    corners = []
    for step in range(240):
        angle = 2 * math.pi * step / 240
        cos, sin = math.cos(angle), math.sin(angle)
        corners.append(
            QPointF(
                radius + radius * math.copysign(abs(cos) ** (2 / ICON_CURVE), cos),
                radius + radius * math.copysign(abs(sin) ** (2 / ICON_CURVE), sin),
            )
        )
    path = QPainterPath()
    path.addPolygon(QPolygonF(corners))
    path.closeSubpath()
    return path


def app_icon() -> QIcon:
    """The lantern on a white macOS-shaped tile. The logo is a transparent PNG and taller than it
    is wide, so on its own it would hang in mid-air over a dark Dock, in the wrong shape for an icon."""
    lantern = QPixmap(str(LOGO_PATH))
    tile = round(max(lantern.width(), lantern.height()) * (1 + 2 * ICON_PADDING))
    side = round(tile / ICON_SHAPE)  # the canvas the tile sits on, clear around its edges
    canvas = QPixmap(side, side)
    canvas.fill(QColor(Qt.GlobalColor.transparent))
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate((side - tile) / 2, (side - tile) / 2)
    painter.fillPath(squircle(tile), QColor("white"))
    painter.drawPixmap(QPointF((tile - lantern.width()) / 2, (tile - lantern.height()) / 2), lantern)
    painter.end()  # the icon has to be taken from a pixmap nothing is still painting on
    return QIcon(canvas)


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Nuru")
    app.setWindowIcon(app_icon())  # the lantern in the Dock, and on the taskbar elsewhere
    window = ChatWindow(Session.start)
    window.show()
    window.start()
    sys.exit(app.exec())
