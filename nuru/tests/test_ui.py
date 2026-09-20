import os
import threading
import time

import pytest

# No display needed - or available, on CI. A Qt install that can't load its system libraries skips
# these tests instead of failing them.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)

from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel

from core import ui
from core.reply_markdown import BREAK, TOOL_MARK, Code, Prose
from core.session import Failure, Text, ToolCall


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


def test_a_tool_line_is_shown_small_and_light_without_its_mark(qapp):
    view = ui.MarkdownView()

    view.set_content(Prose(f"The answer.\n\n---\n\n{TOOL_MARK}`search_notes({{'q': 1}})`\n\n---\n\nMore."))

    assert TOOL_MARK not in view.toPlainText()
    tool_runs = fragments_of(view, "search_notes")
    assert "".join(text for text, _ in tool_runs) == "search_notes({'q': 1})"
    assert all(is_light_and_small(fmt) for _, fmt in tool_runs)
    for prose in ("The answer.", "More."):  # the answer around it is left as it was
        assert not any(is_light_and_small(fmt) for _, fmt in fragments_of(view, prose))


def test_inline_code_in_the_models_own_text_is_not_mistaken_for_a_tool_line(qapp):
    view = ui.MarkdownView()

    view.set_content(Prose("Run `npm install` first.\n\n`just_code()`"))

    for text in ("npm install", "just_code"):
        assert not any(is_light_and_small(fmt) for _, fmt in fragments_of(view, text))


def test_a_tool_call_in_a_reply_is_styled_as_working_not_answer(started):
    window, _ = started([Text("Let me look."), ToolCall("search_notes", {"query": "x"}), Text("Found it.")])

    type_and_send(window, "find x")
    finish(window)

    tool_views = [p for p in window.message.parts if isinstance(p, ui.MarkdownView) and "search_notes" in p.toPlainText()]
    assert len(tool_views) == 1
    assert all(is_light_and_small(fmt) for _, fmt in fragments_of(tool_views[0], "search_notes"))
    assert not any(TOOL_MARK in p.toPlainText() for p in window.message.parts if isinstance(p, ui.MarkdownView))


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


def test_a_tool_call_shows_as_a_line_between_breaks_in_the_same_bubble(started):
    window, _ = started([Text("Let me look."), ToolCall("search_notes", {"query": "x"}), Text("Found it.")])

    type_and_send(window, "find x")
    finish(window)

    assert window.reply.markdown == "Let me look." + BREAK + TOOL_MARK + "`search_notes({'query': 'x'})`" + BREAK + "Found it."
    assert len(window.messages.parentWidget().findChildren(ui.AssistantMessage)) == 1
    text = bubble_text(window)
    assert "search_notes" in text and "[tool]" not in text and "[nuru]" not in text


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
