"""Desktop chat window for Nuru (PyQt6).

The window only draws. Talking to the model happens in core.session, on a background thread,
and what comes back is folded into markdown by core.reply_markdown.
"""

import math
import sys
import threading
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
    QKeyEvent,
    QPainter,
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

from core.reply_markdown import TOOL_MARK, Code, Prose, Reply, split_fences
from core.session import Event, Session

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
QFrame#userBubble {{ background: {BUBBLE}; border-radius: 16px; }}
QFrame#codeBlock {{ background: {BUBBLE}; border-radius: 12px; }}
QLabel#userText, QLabel#greeting, QLabel#codeText {{ color: {TEXT}; }}
QLabel#codeLanguage {{ color: {MUTED}; font-size: 13px; }}
QPushButton#copy {{ background: transparent; border: none; color: {MUTED}; font-size: 13px; padding: 2px 6px; }}
QPushButton#copy:hover {{ color: {TEXT}; }}
QPushButton#send {{ background: {SEND}; border: none; border-radius: 9px; }}
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

        # A tool call is Nuru's working, not part of its answer: small and light. The mark only said
        # which line it was, so it goes before the line is styled.
        if block.text().startswith(TOOL_MARK):
            tool = QTextCharFormat()
            tool.setForeground(QBrush(QColor(MUTED)))
            tool.setFont(font(13, MONO, QFont.StyleHint.Monospace), QTextCharFormat.FontPropertiesInheritanceBehavior.FontPropertiesSpecifiedOnly)
            cursor.deleteChar()
            cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
            cursor.mergeCharFormat(tool)
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


class AssistantMessage(QFrame):
    """Nuru's reply in a white card, styled like the composer: markdown text and code boxes,
    with the logo at the bottom rocking until the reply is done."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("reply")
        self.parts: list[MarkdownView | CodeBlock] = []  # in order, ahead of the logo
        self.logo = Logo(28, swing=SWING)
        self.logo.start()

        self.column = QVBoxLayout(self)
        self.column.setContentsMargins(24, 20, 24, 18)
        self.column.setSpacing(14)
        self.column.addWidget(self.logo, 0, Qt.AlignmentFlag.AlignLeft)

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

    def finish(self) -> None:
        self.logo.stop()


class ChatWindow(QWidget):
    FLUSH_MS = 25  # streamed text is redrawn at most this often, however fast it arrives
    CLOSE_WAIT_SECONDS = 5  # how long closing waits for a reply in flight to save what it has

    def __init__(self, start_session: Callable[[], Session]) -> None:
        super().__init__()
        self.setWindowTitle("Nuru")
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
        self.greeting_title.setText(time_greeting(datetime.now().hour))
        self.greeting_logo.stop()
        self.dock.show()
        self.update_send()
        self.prompt.setFocus()

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

        self.prompt.clear()
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


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Nuru")
    window = ChatWindow(Session.start)
    window.show()
    window.start()
    sys.exit(app.exec())
