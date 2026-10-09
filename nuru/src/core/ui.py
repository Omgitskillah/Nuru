"""Desktop chat window for Nuru (PyQt6).

The window only draws. Talking to the model happens in core.session, on a background thread,
and what comes back is folded into markdown by core.reply_markdown.
"""

import math
import json
import sys
import threading
import time
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QObject, QPointF, QRectF, QSize, QSizeF, Qt, QTimer, QUrl, pyqtSignal
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
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core import questions
from core.questions import MULTI, YES_NO, Question
from core.reply_markdown import Code, Prose, Reply, format_tool_call, split_fences
from core.session import Ask, Event, RetryWithoutThinking, Session, Thinking, ToolCall

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

COLUMN_WIDTH = 760
BUBBLE_WIDTH = 600  # your messages wrap before the full column width so they read as bubbles
SWING = 8  # degrees the lantern rocks to either side
LOGO_PATH = Path(__file__).parent / "assets" / "lantern.png"  # transparent PNG
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
QFrame#composer, QFrame#reply {{ background: white; border: 1px solid {BORDER}; border-radius: 20px; }}
QFrame#replyHeader {{ background: white; border: 1px solid {BORDER}; border-radius: 14px; }}
QPushButton#replyToggle {{ background: transparent; border: none; padding: 0; text-align: left; color: {MUTED}; font-size: 12px; }}
QPushButton#replyToggle:hover {{ color: {TEXT}; }}
QLabel#workRow {{ color: {MUTED}; }}
QFrame#userBubble {{ background: {BUBBLE}; border-radius: 16px; }}
QFrame#codeBlock {{ background: {BUBBLE}; border-radius: 12px; }}
QLabel#userText, QLabel#greeting, QLabel#codeText {{ color: {TEXT}; }}
QLabel#codeLanguage {{ color: {MUTED}; font-size: 13px; }}
QPushButton#copy {{ background: transparent; border: none; color: {MUTED}; font-size: 13px; padding: 2px 6px; }}
QPushButton#copy:hover {{ color: {TEXT}; }}
QPushButton#send {{ background: {SEND}; border: none; border-radius: 9px; }}
QPushButton#send:hover {{ background: {SEND_HOVER}; }}
QPushButton#send:disabled {{ background: rgba(36, 32, 33, 80); }}
QFrame#question {{ background: {BG}; border: 1px solid {BORDER}; border-radius: 14px; }}
QLabel#questionText {{ color: {TEXT}; }}
QLabel#questionDetails, QLabel#questionNote, QLabel#choiceNote {{ color: {MUTED}; }}
QPushButton#choice {{ background: white; border: 1px solid {BORDER}; border-radius: 10px; padding: 8px 14px; color: {TEXT}; text-align: left; }}
QPushButton#choice:hover {{ border-color: {MUTED}; }}
QPushButton#choice[recommended="true"] {{ border: 2px solid {INK}; }}
QPushButton#choice:disabled {{ color: {MUTED}; }}
QPushButton#answer {{ background: {SEND}; border: none; border-radius: 10px; padding: 8px 18px; color: white; }}
QPushButton#answer:hover {{ background: {SEND_HOVER}; }}
QPushButton#answer:disabled {{ background: rgba(36, 32, 33, 80); }}
QCheckBox#check {{ color: {TEXT}; spacing: 10px; }}
QLineEdit#other {{ background: white; border: 1px solid {BORDER}; border-radius: 10px; padding: 7px 12px; color: {TEXT}; }}
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
    row.setContentsMargins(24, 0, 24, 0)
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
            painter.drawRoundedRect(QRectF(cx - 5, cy - 5, 10, 10), 2, 2)
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
        self.setFont(font(16))
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
        self.setFont(font(17, SERIF, QFont.StyleHint.Serif))
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
        label.setFont(font(16))
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        bubble = QFrame()
        bubble.setObjectName("userBubble")
        inner = QVBoxLayout(bubble)
        inner.setContentsMargins(16, 12, 16, 12)
        inner.addWidget(label)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        row.addWidget(bubble)


class QuestionCard(QFrame):
    """A question Nuru puts to the user, in the reply: a button per answer for yes/no and single
    choice, checkboxes and a Send button for a multi-choice question, and a line for an answer of
    your own where the question allows one. The recommended option is outlined and says why.

    `answered` carries the labels chosen; the card then shows the answer and takes no more input."""

    answered = pyqtSignal(list)

    def __init__(self, question: Question) -> None:
        super().__init__()
        self.setObjectName("question")
        self.question = question
        self.choices: list[QPushButton] = []
        self.checks: list[QCheckBox] = []
        self.other: QLineEdit | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        text = QLabel(question.text)
        text.setObjectName("questionText")
        text.setFont(font(16))
        text.setWordWrap(True)
        text.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(text)
        if question.details:
            details = QLabel(question.details)
            details.setObjectName("questionDetails")
            details.setFont(font(12, MONO, QFont.StyleHint.Monospace))
            details.setWordWrap(True)
            details.setTextFormat(Qt.TextFormat.PlainText)
            layout.addWidget(details)

        self.inputs = QWidget()
        inputs = QVBoxLayout(self.inputs)
        inputs.setContentsMargins(0, 2, 0, 0)
        inputs.setSpacing(8)
        if question.kind == MULTI:
            for option in question.choices:
                check = QCheckBox(option.label + ("  · recommended" if option.recommended else ""))
                check.setObjectName("check")
                check.setFont(font(15))
                check.setChecked(option.recommended)
                self.checks.append(check)
                inputs.addWidget(check)
                self.add_note(inputs, option.description, indent=28)
        else:
            row = QHBoxLayout() if question.kind == YES_NO else None
            for option in question.choices:
                button = QPushButton(option.label + ("  · recommended" if option.recommended else ""))
                button.setObjectName("choice")
                button.setFont(font(15))
                button.setProperty("recommended", option.recommended)
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.clicked.connect(lambda _, label=option.label: self.choose([label]))
                self.choices.append(button)
                if row is not None:
                    row.addWidget(button)
                else:
                    inputs.addWidget(button)
                    self.add_note(inputs, option.description, indent=16)
            if row is not None:
                row.addStretch(1)
                inputs.addLayout(row)
                if question.recommended and question.recommended.description:
                    self.add_note(inputs, question.recommended.description, indent=0)
        if question.allow_other or question.kind == MULTI:
            row = QHBoxLayout()
            if question.allow_other:
                self.other = QLineEdit()
                self.other.setObjectName("other")
                self.other.setFont(font(14))
                self.other.setPlaceholderText("Or type your own answer…")
                self.other.returnPressed.connect(self.send_other)
                row.addWidget(self.other, 1)
            else:
                row.addStretch(1)
            if question.kind == MULTI:
                send = QPushButton("Send")
                send.setObjectName("answer")
                send.setFont(font(14))
                send.setCursor(Qt.CursorShape.PointingHandCursor)
                send.clicked.connect(self.send_checked)
                row.addWidget(send)
            inputs.addLayout(row)
        layout.addWidget(self.inputs)

        self.note = QLabel()
        self.note.setObjectName("questionNote")
        self.note.setFont(font(13))
        self.note.setWordWrap(True)
        self.note.hide()
        layout.addWidget(self.note)

    @staticmethod
    def add_note(layout: QVBoxLayout, text: str, indent: int) -> None:
        if not text:
            return
        note = QLabel(text)
        note.setObjectName("choiceNote")
        note.setFont(font(13))
        note.setWordWrap(True)
        note.setContentsMargins(indent, 0, 0, 0)
        layout.addWidget(note)

    def send_checked(self) -> None:
        typed = self.other.text().strip() if self.other else ""
        chosen = [option.label for option, check in zip(self.question.choices, self.checks) if check.isChecked()]
        self.choose(chosen + ([typed] if typed else []))

    def send_other(self) -> None:
        typed = self.other.text().strip()
        if typed:
            self.send_checked() if self.question.kind == MULTI else self.choose([typed])

    def choose(self, chosen: list[str]) -> None:
        if not self.inputs.isEnabled():
            return
        self.close_with(f"You answered: {questions.answer_text(chosen)}")
        self.answered.emit(chosen)

    def close_with(self, note: str) -> None:
        """No more answers: say how the question ended, under it."""
        self.inputs.setEnabled(False)
        self.inputs.hide()
        self.note.setText(note)
        self.note.show()


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
        self.code.setFont(font(14, MONO, QFont.StyleHint.Monospace))
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
        layout.setContentsMargins(16, 10, 12, 16)
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
    """

    OPEN, SHUT = "▾", "▸"  # the chevron on the line, pointing down while the working is showing

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("replyHeader")
        self.status = "Thinking…"
        self.expanded = True
        self.rows: list[QLabel] = []  # the calls, then the reasoning; each group in its own order
        self.tools = 0  # how many of those rows are tool calls, and so where the reasoning starts
        self.retried = False  # a silent call was asked again with thinking off

        self.toggle = QPushButton()
        self.toggle.setObjectName("replyToggle")
        self.toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.toggle.clicked.connect(self.flip)

        self.rows_box = QWidget()
        self.rows_column = QVBoxLayout(self.rows_box)
        self.rows_column.setContentsMargins(16, 0, 0, 2)
        self.rows_column.setSpacing(4)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 10, 24, 10)
        layout.setSpacing(8)
        layout.addWidget(self.toggle)
        layout.addWidget(self.rows_box)
        self.draw()

    def add_call(self, call: ToolCall) -> None:
        # Calls join the ones before them, whenever they arrive: what Nuru did is easier to take in
        # at the top than scattered through reasoning that runs long.
        self.add_row(f"tool: {format_tool_call(call.name, call.arguments)}", wrap=False, at=self.tools)
        self.tools += 1

    def add_thinking(self, thinking: Thinking) -> None:
        # Reasoning is prose and runs long, so it wraps rather than being cut off like a call
        self.add_row(f"thinking: {thinking.text}", wrap=True, at=len(self.rows))

    def add_retry(self, _retry: RetryWithoutThinking) -> None:
        # Where the reasoning got the answer but the reply didn't, so the turn was run again with
        # thinking off. It goes in with the working, and on the line that outlives it (see finish).
        self.retried = True
        self.add_row("retried with thinking off", wrap=False, at=len(self.rows))

    def add_row(self, text: str, wrap: bool, at: int) -> None:
        label = QLabel(text)
        label.setObjectName("workRow")
        label.setFont(font(13, MONO, QFont.StyleHint.Monospace))
        label.setMinimumWidth(1)  # an unwrapped row is clipped rather than widening the column
        label.setWordWrap(wrap)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.rows.insert(at, label)
        self.rows_column.insertWidget(at, label)
        self.draw()

    def flip(self) -> None:
        self.expanded = not self.expanded
        self.draw()

    def finish(self) -> None:
        """The reply has landed: say what was done, in the past, and fold it away."""
        if self.tools:
            self.status = f"Used {self.tools} tool" + ("" if self.tools == 1 else "s")
        else:
            self.status = "Thought about it"  # it reasoned its way there without calling anything
        if self.retried:
            # The reply came from a second call, so say so where it stays readable once this folds.
            self.status += ", retried with thinking off"
        self.expanded = False
        self.setVisible(bool(self.rows))  # nothing was done: the card has nothing left to say
        self.draw()

    def draw(self) -> None:
        """Redraw the line and the working under it. With nothing to show there is nothing to open,
        so the chevron stays off and clicking the line does nothing."""
        chevron = f"{self.OPEN if self.expanded else self.SHUT}  " if self.rows else ""
        self.toggle.setText(chevron + self.status)
        self.toggle.setCursor(Qt.CursorShape.PointingHandCursor if self.rows else Qt.CursorShape.ArrowCursor)
        self.rows_box.setVisible(self.expanded and bool(self.rows))


class AssistantMessage(QWidget):
    """Nuru's reply in a white card, styled like the composer: markdown text and code boxes,
    with the logo at the bottom rocking until the reply is done. Its header is a smaller card above it."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[MarkdownView | CodeBlock] = []  # in order, ahead of the logo
        self.logo = Logo(28, swing=SWING)
        self.logo.start()
        self.header = ReplyHeader()

        body = QFrame()
        body.setObjectName("reply")
        self.column = QVBoxLayout(body)
        self.column.setContentsMargins(24, 20, 24, 18)
        self.column.setSpacing(14)
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
                self.column.insertWidget(i, self.parts[i])
            self.parts[i].set_content(part)
        for stale in self.parts[len(parts) :]:
            self.discard(stale)
        del self.parts[len(parts) :]

    @staticmethod
    def discard(widget: QWidget) -> None:
        widget.setParent(None)
        widget.deleteLater()

    def add_question(self, card: QuestionCard) -> None:
        """Under what the reply has said so far, above the logo."""
        self.column.insertWidget(self.column.indexOf(self.logo), card)

    def finish(self) -> None:
        self.logo.stop()
        self.header.finish()


class ChatWindow(QWidget):
    # A question code raised mid-turn, from the worker thread: (the question, (answered event, answer list))
    question_raised = pyqtSignal(object, object)

    FLUSH_MS = 25  # streamed text is redrawn at most this often, however fast it arrives
    TITLE = "Nuru"
    SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"  # the title cycles through these while notes are being embedded
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
        self.open_question: QuestionCard | None = None  # the question the last reply ended on, until answered
        self.raised_question: QuestionCard | None = None  # the last question raised mid-turn; Stop closes it

        self.flush_timer = QTimer(self)
        self.flush_timer.setSingleShot(True)
        self.flush_timer.setInterval(self.FLUSH_MS)
        self.flush_timer.timeout.connect(self.render_reply)

        # The title bar is native, so it can't hold a widget: the background indexer's progress is
        # shown in the title text instead. Runs from on_started, once there's a session to ask.
        self.spin_frame = 0
        self.spin_started: float | None = None  # when the spinner went up; None while it's down
        self.spin_count = 0  # the most notes seen pending in the pass the spinner is showing
        self.shown_changes = 0  # the index_change count the title has already announced
        self.done_text = ""  # what the finished pass did, shown once the spinner comes down
        self.done_until = 0.0
        self.title_timer = QTimer(self)
        self.title_timer.setInterval(self.SPIN_MS)
        self.title_timer.timeout.connect(self.update_title)

        # Empty state: a greeting above the composer. Until the session is up it's a rocking lantern
        # beside "Starting Nuru…" instead, and the composer stays hidden.
        self.greeting_title = QLabel("Starting Nuru…")
        self.greeting_title.setObjectName("greeting")
        self.greeting_title.setFont(font(38, SERIF, QFont.StyleHint.Serif))
        self.greeting_logo = Logo(52, swing=SWING)
        self.greeting_logo.start()
        self.greeting = QWidget()
        greeting_row = QHBoxLayout(self.greeting)
        greeting_row.setContentsMargins(0, 0, 0, 28)
        greeting_row.setSpacing(14 - self.greeting_logo.reach)  # the room the lantern has to swing isn't part of the gap
        greeting_row.addStretch(1)
        greeting_row.addWidget(self.greeting_logo)
        greeting_row.addWidget(self.greeting_title)
        greeting_row.addStretch(1)

        # Conversation: a scrolling column of messages, centered and capped in width
        column = QWidget()
        self.messages = QVBoxLayout(column)
        self.messages.setContentsMargins(0, 24, 0, 24)
        self.messages.setSpacing(28)
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
        self.writes = QPushButton("Pending writes")
        self.writes.clicked.connect(self.show_pending_writes)
        self.writes.hide()
        buttons.addWidget(self.writes)
        buttons.addStretch(1)
        buttons.addWidget(self.send)
        composer = QFrame()
        composer.setObjectName("composer")
        composer_layout = QVBoxLayout(composer)
        composer_layout.setContentsMargins(18, 14, 12, 12)
        composer_layout.setSpacing(8)
        composer_layout.addWidget(self.prompt)
        composer_layout.addLayout(buttons)

        self.dock = QWidget()
        self.dock_layout = QVBoxLayout(self.dock)
        self.dock_layout.setContentsMargins(0, 8, 0, 24)
        self.dock_layout.addLayout(centered(composer))
        # Hidden until the session is up, but its space stays reserved so the greeting doesn't jump
        # when the composer appears.
        keep_space = self.dock.sizePolicy()
        keep_space.setRetainSizeWhenHidden(True)
        self.dock.setSizePolicy(keep_space)
        self.dock.hide()

        # Empty state centers the greeting + composer; chat state gives all the room to the scroll area
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(0, 0, 0, 0)
        self.root.setSpacing(0)
        self.root.addStretch(2)
        self.root.addWidget(self.greeting)
        self.root.addWidget(self.scroll, 1)
        self.root.addWidget(self.dock)
        self.root.addStretch(3)

        self.question_raised.connect(self.show_raised_question)
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
        self.session.answer_question = self.answer_question
        self.title_timer.start()
        self.greeting_title.setText(time_greeting(datetime.now().hour))
        self.greeting_logo.stop()
        self.dock.show()
        self.update_send()
        self.prompt.setFocus()

    def update_title(self) -> None:
        """"Nuru ⠹ indexing 12 notes" while the indexer embeds, then "Nuru · indexed 12 notes" for
        DONE_SECONDS, else just "Nuru".

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
            self.spin_frame = (self.spin_frame + 1) % len(self.SPINNER)
            left = pending or self.spin_count
            count = f" {left} note{'' if left == 1 else 's'}" if left else ""
            title = f"{self.TITLE} {self.SPINNER[self.spin_frame]} indexing{count}"
        elif self.done_text and now < self.done_until:
            title = f"{self.TITLE} · {self.done_text}"
        else:
            self.done_text = ""
            title = self.TITLE
        if self.windowTitle() != title:
            self.setWindowTitle(title)

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
        self.root.setStretch(0, 0)
        self.root.setStretch(4, 0)
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
        self.writes.setEnabled(self.session is not None and not streaming)
        if self.session is not None and not streaming:
            pending = self.session.pending_writes()
            self.writes.setText(f"Pending writes ({len(pending)})")
            self.writes.setVisible(bool(pending))

    def show_pending_writes(self) -> None:
        session = self.session
        if session is None or self.worker is not None:
            return
        pending = session.pending_writes()
        if not pending:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Resolve pending writes")
        layout = QVBoxLayout(dialog)
        notice = QLabel("Inspect the affected notes and move destinations first. Stop other Nuru sessions using this vault. "
                        "Leave an attempt pending if its outcome is unknown. These actions record your conclusion; they do not retry a write.")
        notice.setWordWrap(True)
        layout.addWidget(notice)
        checked = QCheckBox("I inspected the affected notes and stopped other sessions using this vault.")
        layout.addWidget(checked)
        for attempt in pending:
            details = QTextEdit()
            details.setReadOnly(True)
            details.setPlainText(json.dumps(attempt, indent=2, ensure_ascii=False))
            layout.addWidget(details)
            row = QHBoxLayout()
            for state, label in (("completed", "Mark completed"), ("not_completed", "Mark not completed")):
                button = QPushButton(label)
                button.setEnabled(False)
                checked.toggled.connect(button.setEnabled)
                button.clicked.connect(lambda _, a=attempt, s=state: self.resolve_pending_write(dialog, checked, a["id"], s))
                row.addWidget(button)
            layout.addLayout(row)
        dialog.resize(640, 480)
        dialog.exec()
        self.update_send()

    def resolve_pending_write(self, dialog: QDialog, checked: QCheckBox, attempt_id: int, state: str) -> None:
        if self.session is None or self.worker is not None or not checked.isChecked():
            return
        try:
            self.session.resolve_write(attempt_id, state)
        except ValueError as error:
            QMessageBox.warning(dialog, "Resolution not recorded", str(error))
            return
        dialog.accept()  # Refresh before another attempt can be reconciled.

    def on_send_clicked(self) -> None:
        if self.worker is not None:
            if self.raised_question is not None and self.raised_question.inputs.isEnabled():
                self.raised_question.close_with("Not answered - stopped.")
            self.cancel.set()
            self.update_send()
        else:
            self.submit()

    def submit(self) -> None:
        text = self.prompt.toPlainText().strip()
        if not text or self.session is None or self.worker is not None:
            return
        self.prompt.clear()
        if self.open_question is not None:
            self.open_question.close_with("Not answered - you wrote a message instead.")
            self.open_question = None
        self.send_message(text)

    def send_message(self, text: str) -> None:
        session = self.session
        self.show_chat()
        self.following = True
        self.add_message(UserMessage(text))
        self.message = AssistantMessage()
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
        if isinstance(event, Ask):
            # The turn ends on it; the answer is the next message.
            self.render_reply()
            card = self.open_question = QuestionCard(event.question)
            card.setEnabled(False)  # until the turn has wound up (finish_reply): an answer starts the next one
            card.answered.connect(self.answer_open_question)
            self.message.add_question(card)
        elif isinstance(event, ToolCall):
            self.message.header.add_call(event)
        elif isinstance(event, Thinking):
            self.message.header.add_thinking(event)
        elif isinstance(event, RetryWithoutThinking):
            self.message.header.add_retry(event)
        self.reply.add(event)
        if not self.flush_timer.isActive():
            self.flush_timer.start()

    def answer_open_question(self, chosen: list[str]) -> None:
        self.open_question = None
        self.send_message(questions.answer_text(chosen))

    def answer_question(self, question: Question) -> list[str] | None:
        """Session.answer_question, called on the worker thread: shows the question in the reply in
        flight and waits for the answer. Stop gives up on it, and the session takes that as no answer."""
        answered, answer = threading.Event(), []
        cancel = self.cancel
        self.question_raised.emit(question, (answered, answer))
        while not answered.wait(0.05):
            if cancel.is_set():
                return None
        return answer[0]

    def show_raised_question(self, question: Question, waiter) -> None:
        answered, answer = waiter
        self.render_reply()
        card = QuestionCard(question)
        card.answered.connect(lambda chosen: (answer.append(chosen), answered.set()))
        self.raised_question = card
        self.message.add_question(card)

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
        if self.open_question is not None:
            self.open_question.setEnabled(True)
        self.update_send()

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
        self.title_timer.stop()
        self.cancel.set()
        if self.worker is not None:
            self.worker.wait(self.CLOSE_WAIT_SECONDS)  # lets a reply in flight save what it has

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
