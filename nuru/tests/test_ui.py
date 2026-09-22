import os
import threading
import time

import pytest

# No display needed - or available, on CI. A Qt install that can't load its system libraries skips
# these tests instead of failing them.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)

from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QFrame, QLabel

from core import ui
from core.reply_markdown import BREAK, Code, Prose
from core.session import Failure, RetryWithoutThinking, Text, Thinking, ToolCall


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def wait_until(condition, timeout=5.0):
    """Runs Qt's event loop until `condition()` holds. Signals from worker threads only arrive through it."""
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for the UI")
        QApplication.processEvents()
        time.sleep(0.002)


def settle(worker):
    """Wait for a worker's thread to end, then deliver the signals it left queued."""
    worker.wait(5)
    QApplication.processEvents()


class FakeSession:
    """Stand-in for Session. Each scripted turn is a list of events, an exception to raise, or a
    function of the cancel event that returns the events (for turns that must wait or be stopped)."""

    def __init__(self, turns=()):
        self._turns = iter(turns)
        self.asked = []
        self.cancels = []
        self.calls = []

    def ask(self, text, cancel=None):
        self.asked.append(text)
        self.cancels.append(cancel)
        turn = next(self._turns)
        if isinstance(turn, Exception):
            raise turn
        yield from turn(cancel) if callable(turn) else turn

    def shutdown(self, reason):
        self.calls.append(("shutdown", reason))

    def close(self):
        self.calls.append("close")


def waits_for(release, *events):
    """A scripted turn that holds until `release` is set, then plays `events`."""

    def turn(cancel):
        release.wait(5)
        yield from events

    return turn


def talks_until_stopped(cancel):
    """A scripted turn that says something, then goes on until the window sets `cancel`."""
    yield Text("partial")
    cancel.wait(5)


@pytest.fixture
def make_window(qapp):
    windows = []

    def make(session=None, start_session=None):
        session = session if session is not None else FakeSession()
        window = ui.ChatWindow(start_session or (lambda: session))
        window.show()
        windows.append(window)
        return window

    yield make

    for window in windows:
        window.cancel.set()  # frees any turn a test left waiting
        window.session = None  # closing is tested on its own; keep teardown quiet
        window.close()
        window.deleteLater()


@pytest.fixture
def started(make_window):
    """A window whose session has come up, given the turns the session will play."""

    def start(*turns):
        session = FakeSession(turns)
        window = make_window(session)
        window.start()
        settle(window.starting)
        return window, session

    return start


def type_and_send(window, text):
    window.prompt.setPlainText(text)
    window.submit()


def finish(window):
    wait_until(lambda: window.worker is None)


def bubble_text(window):
    return " ".join(part.toPlainText() for part in window.message.parts if isinstance(part, ui.MarkdownView))


@pytest.mark.parametrize(
    ("hour", "greeting"),
    [(0, "Good morning"), (11, "Good morning"), (12, "Good afternoon"), (17, "Good afternoon"), (18, "Good evening"), (23, "Good evening")],
)
def test_time_greeting_follows_the_hour(hour, greeting):
    assert ui.time_greeting(hour) == greeting


def test_font_names_a_fallback_for_machines_without_the_families(qapp):
    f = ui.font(17, ui.SERIF, QFont.StyleHint.Serif)

    assert f.pixelSize() == 17
    assert f.styleHint() == QFont.StyleHint.Serif


def test_the_app_icon_is_the_lantern_on_a_white_tile_shaped_the_way_macos_shapes_them(qapp):
    image = ui.app_icon().pixmap(256, 256).toImage()
    edge = round(256 * (1 - ui.ICON_SHAPE) / 2)  # where the tile starts, the canvas left clear around it

    assert image.width() == image.height()  # the logo alone is taller than it is wide
    assert image.pixelColor(0, 0).alpha() == 0
    assert image.pixelColor(edge + 1, edge + 1).alpha() == 0  # the tile's corners curve away from it
    assert image.pixelColor(128, edge + 1) == QColor("white")  # while the middle of its edge does not
    sampled = [image.pixelColor(x, y) for x in range(0, 256, 4) for y in range(0, 256, 4)]
    assert any(colour.lightness() < 100 for colour in sampled)  # and the lantern is on it


class FakeDesktopServices:
    opened = []

    @classmethod
    def openUrl(cls, url):
        cls.opened.append(url.toString())


@pytest.fixture
def opened_urls(monkeypatch):
    FakeDesktopServices.opened = []
    monkeypatch.setattr(ui, "QDesktopServices", FakeDesktopServices)
    return FakeDesktopServices.opened


@pytest.mark.parametrize("url", ["https://example.com/a", "http://example.com", "mailto:someone@example.com"])
def test_open_link_opens_web_and_mail_links(opened_urls, url):
    ui.open_link(QUrl(url))

    assert opened_urls == [url]


@pytest.mark.parametrize(
    "url", ["file:///Applications/Calculator.app", "javascript:alert(1)", "ssh://host", "x-some-app://do-something", "notes.md"]
)
def test_open_link_ignores_everything_else(opened_urls, url):
    ui.open_link(QUrl(url))

    assert opened_urls == []


def test_markdown_view_routes_clicked_links_through_the_allow_list(qapp, opened_urls):
    view = ui.MarkdownView()

    view.anchorClicked.emit(QUrl("https://example.com"))
    view.anchorClicked.emit(QUrl("file:///etc/passwd"))

    assert view.openLinks() is False
    assert opened_urls == ["https://example.com"]


def test_markdown_view_renders_markdown(qapp):
    view = ui.MarkdownView()

    view.set_content(Prose("# Title\n\nSome **bold** text"))

    assert view.toPlainText() == "Title\nSome bold text"


def test_markdown_view_skips_content_it_already_shows(qapp):
    view = ui.MarkdownView()
    parsed = []
    original = view.setMarkdown
    view.setMarkdown = lambda text: (parsed.append(text), original(text))

    view.set_content(Prose("same"))
    view.set_content(Prose("same"))
    view.set_content(Prose("different"))

    assert parsed == ["same", "different"]


def fragments_of(view, text):
    """The (text, format) runs of the block in `view`'s document that contains `text`."""
    block = view.document().begin()
    while block.isValid():
        if text in block.text():
            runs, it = [], block.begin()
            while not it.atEnd():
                runs.append((it.fragment().text(), it.fragment().charFormat()))
                it += 1
            return runs
        block = block.next()
    raise AssertionError(f"no block containing {text!r}")


def is_light_and_small(fmt):
    return fmt.foreground().color().name() == ui.MUTED and fmt.font().pixelSize() == 13


def test_the_models_own_text_is_left_as_it_was(qapp):
    view = ui.MarkdownView()

    view.set_content(Prose("Run `npm install` first.\n\n`just_code()`"))

    for text in ("npm install", "just_code"):
        assert not any(is_light_and_small(fmt) for _, fmt in fragments_of(view, text))


def test_code_block_shows_language_and_source(qapp):
    block = ui.CodeBlock()

    block.set_content(Code("python", "print(1)"))

    assert block.language.text() == "python"
    assert block.code.text() == "print(1)"


def test_code_block_calls_an_unlabelled_fence_text(qapp):
    block = ui.CodeBlock()

    block.set_content(Code("", "plain"))

    assert block.language.text() == "text"


def test_code_block_copy_button_copies_the_code_then_resets_its_label(qapp):
    block = ui.CodeBlock()
    block.set_content(Code("sh", "ls -la"))

    block.copy.click()

    assert QApplication.clipboard().text() == "ls -la"
    assert block.copy.text() == "Copied"
    assert block.copied_timer.isActive()
    block.copied_timer.timeout.emit()
    assert block.copy.text() == "Copy"


def test_user_message_shows_the_text_as_typed_not_as_markdown(qapp):
    message = ui.UserMessage("*not italic* <b>not bold</b>")

    assert message.findChild(QLabel, "userText").text() == "*not italic* <b>not bold</b>"


def test_assistant_message_splits_prose_and_code_into_widgets(qapp):
    message = ui.AssistantMessage()

    message.set_markdown("Before\n\n```python\nprint(1)\n```\n\nAfter")

    assert [type(p) for p in message.parts] == [ui.MarkdownView, ui.CodeBlock, ui.MarkdownView]
    assert message.parts[1].code.text() == "print(1)"


def test_assistant_message_keeps_the_logo_after_the_parts(qapp):
    message = ui.AssistantMessage()

    message.set_markdown("Before\n\n```sh\nls\n```")

    assert message.column.itemAt(message.column.count() - 1).widget() is message.logo
    assert message.column.count() == len(message.parts) + 1


def test_assistant_message_swaps_a_half_typed_fence_for_a_code_block_once_it_is_real(qapp):
    message = ui.AssistantMessage()

    message.set_markdown("Intro\n\n```py")
    assert [type(p) for p in message.parts] == [ui.MarkdownView]

    message.set_markdown("Intro\n\n```python\nprint(")
    assert [type(p) for p in message.parts] == [ui.MarkdownView, ui.CodeBlock]

    message.set_markdown("Intro\n\n```python\nprint(1)\n```\n\nDone")
    assert [type(p) for p in message.parts] == [ui.MarkdownView, ui.CodeBlock, ui.MarkdownView]


def test_assistant_message_replaces_the_widget_when_a_reply_opening_with_a_fence_makes_it_real(qapp):
    message = ui.AssistantMessage()

    message.set_markdown("```py")  # still just text: the language line hasn't finished arriving
    text_view = message.parts[0]
    assert isinstance(text_view, ui.MarkdownView)

    message.set_markdown("```python\nprint(")  # the same slot is now code

    assert [type(p) for p in message.parts] == [ui.CodeBlock]
    assert message.column.count() == 2  # the code block and the logo: the text view is gone, not left behind
    assert text_view.parent() is None


def test_assistant_message_does_not_reparse_earlier_text_as_the_reply_grows(qapp):
    message = ui.AssistantMessage()
    message.set_markdown("First paragraph.\n\n```sh\nls\n```\n\nSecond")
    first = message.parts[0]
    parsed = []
    original = first.setMarkdown
    first.setMarkdown = lambda text: (parsed.append(text), original(text))

    message.set_markdown("First paragraph.\n\n```sh\nls\n```\n\nSecond paragraph, longer now")

    assert parsed == []
    assert "longer now" in message.parts[2].toPlainText()


def test_assistant_message_drops_parts_that_are_no_longer_there(qapp):
    message = ui.AssistantMessage()
    message.set_markdown("A\n\n```sh\nls\n```\n\nB")

    message.set_markdown("Just A")

    assert [type(p) for p in message.parts] == [ui.MarkdownView]
    assert message.column.count() == 2  # the one part and the logo


def test_assistant_message_has_a_header_card_above_the_reply(qapp):
    message = ui.AssistantMessage()

    body = message.findChild(QFrame, "reply")
    assert message.header.toggle.text() == "Thinking…"
    assert message.layout().indexOf(message.header) < message.layout().indexOf(body)


def test_reply_header_keeps_the_calls_together_above_the_thinking(qapp):
    header = ui.ReplyHeader()

    # A turn that thought, called a tool, thought again and called another: the calls still group
    header.add_thinking(Thinking("I should look this up"))
    header.add_call(ToolCall("search_notes", {"query": "x"}))
    header.add_thinking(Thinking("now read the one it found"))
    header.add_call(ToolCall("read_note", {"path": "a.md"}))

    assert header.toggle.text() == f"{ui.ReplyHeader.OPEN}  Thinking…"
    assert header.rows_box.isVisibleTo(header)
    assert [label.text() for label in header.rows] == [
        "tool: search_notes({'query': 'x'})",
        "tool: read_note({'path': 'a.md'})",
        "thinking: I should look this up",
        "thinking: now read the one it found",
    ]
    # and the widgets sit in that order too, not just the list
    assert [header.rows_column.itemAt(i).widget() for i in range(header.rows_column.count())] == header.rows


def test_reply_header_wraps_the_thinking_but_clips_a_long_call(qapp):
    header = ui.ReplyHeader()

    header.add_thinking(Thinking("a long stretch of reasoning " * 10))
    header.add_call(ToolCall("create_note", {"content": "word " * 100}))

    call, thinking = header.rows
    assert thinking.wordWrap()
    assert not call.wordWrap()
    assert call.text().endswith("…")  # still cut off at the limit


def test_reply_header_folds_shut_once_the_reply_lands_and_says_what_was_used(qapp):
    header = ui.ReplyHeader()
    header.add_call(ToolCall("search_notes", {"query": "x"}))

    header.finish()

    assert header.toggle.text() == f"{ui.ReplyHeader.SHUT}  Used 1 tool"
    assert not header.rows_box.isVisibleTo(header)

    header.add_call(ToolCall("read_note", {"path": "a.md"}))  # a second call would read "2 tools"
    header.finish()
    assert header.toggle.text() == f"{ui.ReplyHeader.SHUT}  Used 2 tools"


def test_reply_header_that_only_thought_says_so_rather_than_counting_no_tools(qapp):
    header = ui.ReplyHeader()
    header.add_thinking(Thinking("2+2 is 4"))

    header.finish()

    assert header.toggle.text() == f"{ui.ReplyHeader.SHUT}  Thought about it"
    assert not header.isHidden()  # there is still working to open


def test_reply_header_keeps_saying_a_retried_turn_was_retried_once_it_folds(qapp):
    header = ui.ReplyHeader()
    header.add_thinking(Thinking("2+2 is 4"))
    header.add_retry(RetryWithoutThinking())

    assert "retried with thinking off" in header.rows[-1].text()

    header.finish()

    # The line outlives the working it folds away, so this is where the retry has to be readable.
    assert header.toggle.text() == f"{ui.ReplyHeader.SHUT}  Thought about it, retried with thinking off"


def test_reply_header_says_both_the_tools_it_used_and_that_it_retried(qapp):
    header = ui.ReplyHeader()
    header.add_call(ToolCall("search_notes", {"query": "x"}))
    header.add_retry(RetryWithoutThinking())

    header.finish()

    assert header.toggle.text() == f"{ui.ReplyHeader.SHUT}  Used 1 tool, retried with thinking off"


def test_reply_header_opens_and_shuts_again_when_the_line_is_clicked(qapp):
    header = ui.ReplyHeader()
    header.add_call(ToolCall("search_notes", {"query": "x"}))
    header.finish()

    header.toggle.click()
    assert header.rows_box.isVisibleTo(header)
    assert header.toggle.text().startswith(ui.ReplyHeader.OPEN)

    header.toggle.click()
    assert not header.rows_box.isVisibleTo(header)
    assert header.toggle.text().startswith(ui.ReplyHeader.SHUT)


def test_reply_header_with_nothing_behind_it_has_no_chevron_and_goes_away_when_the_reply_lands(qapp):
    header = ui.ReplyHeader()

    assert header.toggle.text() == "Thinking…"  # nothing to open, so nothing says it opens
    assert not header.rows_box.isVisibleTo(header)

    header.finish()

    assert header.isHidden()


def test_assistant_message_logo_rocks_until_finished(qapp):
    message = ui.AssistantMessage()
    assert message.logo.timer.isActive()

    message.finish()

    assert not message.logo.timer.isActive()
    assert message.logo.phase == 0.0


def test_prompt_enter_submits_and_shift_enter_adds_a_line(qapp):
    prompt = ui.PromptEdit()
    submitted = []
    prompt.submitted.connect(lambda: submitted.append(True))
    prompt.setPlainText("hi")

    QTest.keyClick(prompt, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    assert submitted == []
    assert "\n" in prompt.toPlainText()

    QTest.keyClick(prompt, Qt.Key.Key_Return)
    assert submitted == [True]


def test_worker_hands_back_events_in_order_then_reports_success(qapp):
    worker = ui.Worker(lambda: iter(["a", "b", "c"]))
    seen, outcomes = [], []
    worker.yielded.connect(seen.append)
    worker.succeeded.connect(lambda: outcomes.append("succeeded"))
    worker.failed.connect(lambda message: outcomes.append(message))

    worker.start()
    settle(worker)

    assert seen == ["a", "b", "c"]
    assert outcomes == ["succeeded"]


def test_worker_runs_a_job_that_yields_nothing(qapp):
    ran = []
    worker = ui.Worker(lambda: ran.append(True))
    outcomes = []
    worker.succeeded.connect(lambda: outcomes.append("succeeded"))

    worker.start()
    settle(worker)

    assert ran == [True]
    assert outcomes == ["succeeded"]


def test_worker_reports_an_exception_after_delivering_what_came_before_it(qapp):
    def job():
        yield "before"
        raise RuntimeError("boom")

    worker = ui.Worker(job)
    seen, outcomes = [], []
    worker.yielded.connect(seen.append)
    worker.succeeded.connect(lambda: outcomes.append("succeeded"))
    worker.failed.connect(lambda message: outcomes.append(message))

    worker.start()
    settle(worker)

    assert seen == ["before"]
    assert outcomes == ["RuntimeError: boom"]


def test_worker_runs_its_job_off_the_calling_thread(qapp):
    threads = []
    worker = ui.Worker(lambda: threads.append(threading.current_thread()))

    worker.start()
    settle(worker)

    assert threads and threads[0] is not threading.current_thread()
    assert threads[0].daemon  # closing the window never waits on a busy model


def slow_start(release):
    """A start_session that takes as long as the test wants: until `release` is set."""

    def start():
        release.wait(5)
        return FakeSession()

    return start


def test_while_starting_a_lantern_rocks_beside_loading_text_and_the_composer_is_hidden(make_window):
    release = threading.Event()
    window = make_window(start_session=slow_start(release))

    window.start()
    wait_until(lambda: window.greeting_logo.phase > 0)  # it really is swinging, not just scheduled to

    assert window.greeting_title.text() == "Starting Nuru…"
    assert window.greeting_logo.timer.isActive()
    assert not window.dock.isVisible()  # no input box yet
    window.prompt.setPlainText("hi")
    window.submit()  # too early: nothing is sent
    assert window.worker is None

    release.set()
    settle(window.starting)
    assert window.greeting_title.text().startswith("Good ")
    assert not window.greeting_logo.timer.isActive()  # it hangs still once Nuru is ready
    assert window.greeting_logo.phase == 0.0
    assert window.dock.isVisible()
    assert window.send.isEnabled()
    assert window.prompt.placeholderText() == "How can I help you today?"


def test_the_greeting_does_not_move_when_the_composer_appears(make_window):
    release = threading.Event()
    window = make_window(start_session=slow_start(release))
    window.start()
    QApplication.processEvents()
    QApplication.processEvents()
    before = window.greeting.geometry().y()
    assert before > 0

    release.set()
    settle(window.starting)
    QApplication.processEvents()

    assert window.dock.isVisible()
    assert window.greeting.geometry().y() == before


def test_the_lanterns_room_to_swing_does_not_widen_its_gap_to_the_greeting(make_window):
    window = make_window()
    window.start()
    settle(window.starting)
    QApplication.processEvents()

    logo, title = window.greeting_logo, window.greeting_title
    visible_right_edge = logo.x() + logo.width() - logo.reach  # the box has empty room to swing into on each side
    assert title.x() - visible_right_edge == 14


def test_a_failed_start_says_why_and_leaves_the_composer_off(make_window):
    def failing_start():
        raise RuntimeError("ollama is not installed")

    window = make_window(start_session=failing_start)
    window.start()
    settle(window.starting)

    window.prompt.setPlainText("hi")
    window.submit()

    assert window.worker is None
    assert not window.greeting_logo.timer.isActive()  # nothing keeps rocking behind the error
    assert window.dock.isVisible()  # the composer is there, but off
    assert not window.send.isEnabled()
    assert window.prompt.placeholderText() == "Nuru couldn't start"
    assert "ollama is not installed" in " ".join(part.toPlainText() for part in window.messages.itemAt(0).widget().parts)


def test_a_sent_message_streams_the_reply_into_its_own_bubble(started):
    window, session = started([Text("Hello "), Text("there")])

    type_and_send(window, "hi")
    finish(window)

    assert session.asked == ["hi"]
    assert window.prompt.toPlainText() == ""
    assert window.findChild(QLabel, "userText").text() == "hi"  # the input keeps its own bubble
    assert bubble_text(window) == "Hello there"
    assert not window.message.logo.timer.isActive()
    assert window.send.stopping is False


def test_a_tool_call_and_the_thinking_behind_it_show_in_the_header_not_in_the_bubble(started):
    window, _ = started(
        [
            Text("Let me look."),
            Thinking("the vault should have it"),
            ToolCall("search_notes", {"query": "x"}),
            Text("Found it."),
        ]
    )

    type_and_send(window, "find x")
    finish(window)

    assert window.reply.markdown == "Let me look." + BREAK + "Found it."
    assert len(window.messages.parentWidget().findChildren(ui.AssistantMessage)) == 1
    assert [label.text() for label in window.message.header.rows] == [
        "tool: search_notes({'query': 'x'})",
        "thinking: the vault should have it",
    ]
    text = bubble_text(window)
    for working in ("search_notes", "the vault should have it", "[tool]", "[nuru]"):
        assert working not in text


def test_a_tool_call_is_shown_as_it_happens_then_folded_away_when_the_reply_lands(started):
    release = threading.Event()

    def calls_then_waits(cancel):
        yield ToolCall("search_notes", {"query": "x"})
        release.wait(5)
        yield Text("Found it.")

    window, _ = started(calls_then_waits)

    type_and_send(window, "find x")
    header = window.message.header
    wait_until(lambda: header.rows)  # the call shows while the turn is still running

    assert header.rows_box.isVisibleTo(header)
    assert header.toggle.text().endswith("Thinking…")

    release.set()
    finish(window)

    assert not header.rows_box.isVisibleTo(header)
    assert header.toggle.text().endswith("Used 1 tool")


def test_a_failure_shows_as_an_error_in_the_reply(started):
    window, _ = started([Failure("request to the model failed: timed out")])

    type_and_send(window, "hi")
    finish(window)

    assert "Error: request to the model failed: timed out" in bubble_text(window)


def test_an_exception_mid_turn_is_shown_and_the_window_keeps_working(started):
    window, session = started(RuntimeError("tool blew up"), [Text("back")])

    type_and_send(window, "first")
    finish(window)
    assert "RuntimeError: tool blew up" in bubble_text(window)
    assert not window.message.logo.timer.isActive()

    type_and_send(window, "second")
    finish(window)
    assert session.asked == ["first", "second"]
    assert bubble_text(window) == "back"


def test_a_second_message_is_not_sent_while_a_reply_is_streaming(started):
    release = threading.Event()
    window, session = started(waits_for(release, Text("slow")))

    type_and_send(window, "first")
    wait_until(lambda: session.asked)
    type_and_send(window, "second")  # Enter pressed too early

    assert session.asked == ["first"]
    assert window.prompt.toPlainText() == "second"  # and what was typed is left alone
    release.set()
    finish(window)


def test_the_button_is_a_stop_button_while_streaming_and_a_send_button_otherwise(started):
    release = threading.Event()
    window, session = started(waits_for(release, Text("slow")))

    assert window.send.stopping is False
    type_and_send(window, "hi")
    assert window.send.stopping is True
    assert window.send.isEnabled()
    release.set()
    finish(window)
    assert window.send.stopping is False


def test_stop_cancels_the_reply_and_keeps_what_had_streamed(started):
    window, session = started(talks_until_stopped)
    type_and_send(window, "hi")
    wait_until(lambda: "partial" in window.reply.markdown)

    window.send.click()
    assert session.cancels[0].is_set()
    assert not window.send.isEnabled()  # stopping: wait for the reply to wind down
    finish(window)

    assert bubble_text(window) == "partial"
    assert not window.message.logo.timer.isActive()
    assert window.send.stopping is False


def test_a_reply_after_a_stopped_one_starts_uncancelled(started):
    window, session = started(talks_until_stopped, [Text("fresh")])
    type_and_send(window, "one")
    wait_until(lambda: "partial" in window.reply.markdown)
    window.send.click()
    finish(window)

    type_and_send(window, "two")
    finish(window)

    assert session.cancels[1] is not session.cancels[0]
    assert not session.cancels[1].is_set()
    assert bubble_text(window) == "fresh"


def test_send_is_off_for_an_empty_composer_and_on_once_there_is_text(started):
    window, _ = started()

    assert not window.send.isEnabled()
    window.prompt.setPlainText("   ")
    assert not window.send.isEnabled()
    window.prompt.setPlainText("hi")
    assert window.send.isEnabled()


def test_streamed_text_is_drawn_on_the_flush_timer_not_per_chunk(started):
    window, _ = started([])
    window.message = ui.AssistantMessage()

    window.on_event(Text("a"))
    window.on_event(Text("b"))

    assert window.message.parts == []  # nothing drawn yet...
    assert window.flush_timer.isActive()  # ...one redraw is queued for both chunks
    window.render_reply()
    assert bubble_text(window) == "ab"


def test_a_long_reply_scrolls_along_until_the_user_scrolls_up(started):
    window, _ = started([Text("line\n\n" * 80)], [Text("again\n\n" * 80)])

    type_and_send(window, "go")
    finish(window)
    bar = window.scroll.verticalScrollBar()
    wait_until(lambda: bar.maximum() > 0)  # layout takes a turn of the event loop to catch up
    assert bar.value() == bar.maximum()  # following the newest text

    bar.setValue(0)
    assert window.following is False

    type_and_send(window, "again")  # sending picks the thread back up
    assert window.following is True
    finish(window)


def test_closing_puts_the_model_away_like_exit(make_window):
    session = FakeSession()
    window = make_window(session)
    window.start()
    settle(window.starting)

    window.close()

    assert session.calls == [("shutdown", "window closed"), "close"]
    assert window.session is None


def test_closing_waits_for_a_reply_in_flight_to_save_before_shutting_down(make_window):
    def saves_once_stopped(cancel):
        yield Text("partial")
        cancel.wait(5)
        session.calls.append("reply saved")

    session = FakeSession([saves_once_stopped])
    window = make_window(session)
    window.start()
    settle(window.starting)
    type_and_send(window, "hi")
    wait_until(lambda: session.asked)

    window.close()

    assert session.calls == ["reply saved", ("shutdown", "window closed"), "close"]


def test_closing_before_the_session_is_up_touches_nothing(make_window):
    window = make_window(start_session=lambda: FakeSession())

    window.close()  # must not raise

    assert window.session is None


def test_closing_survives_a_failed_shutdown_and_still_releases_the_session(make_window, capsys):
    class BrokenSession(FakeSession):
        def shutdown(self, reason):
            raise ConnectionError("ollama went away")

    session = BrokenSession()
    window = make_window(session)
    window.start()
    settle(window.starting)

    window.close()  # an exception out of closeEvent would abort the app

    assert session.calls == ["close"]
    assert "ollama went away" in capsys.readouterr().err
