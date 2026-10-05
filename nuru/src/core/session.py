"""Everything a Nuru chat needs that doesn't depend on how it's shown.

A `Session` brings up the model and its MCP tools, runs one turn at a time (streaming the
reply, running the tool calls the model makes, logging and saving as it goes) and shuts it all
down again. A turn is a generator of events, so cli.py prints them and ui.py renders them.
"""

import json
import os
import subprocess
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from dotenv import dotenv_values, load_dotenv
from laya_mcp.config import load_config as load_decision_config
from laya_mcp.checks import WRITE_TOOLS
from laya_mcp.decision_log import DecisionLog
from mcp import StdioServerParameters
from mcp.client.stdio import get_default_environment

from core.cli_logging import CLILogger
from core.conversation_store import ConversationStore
from core.decision_shadow import DecisionShadow, think_tier
from core.enforcement import REGENERATE, STEER, STOP, STOPPED, UNVERIFIED, WARNING, Enforcement, ExpertUnavailable, log_lines
from core.expert import ExpertPlanner
from core import executor, model_profile, planner
from core.providers.mcp_tools import MCPTools
from core.providers.ollama import (
    chat,
    create_model,
    ensure_model_loaded,
    ensure_server_running,
    is_model_loaded,
    stop_server,
    unload_all_models,
)
from core.vault_indexer import VaultIndexer
from core.thinking_watch import ThinkingWatch
from core.tool_contract import MODEL_TOOLS
from core.write_args import ask_for_arguments, validate_arguments

# Every subsystem's settings live in the repo root, one file each (.env.nuru, .env.obsidian,
# .env.deep-think, ...; the .example beside each is the template). Gitignored.
ENV_DIR = Path(__file__).resolve().parents[3]
load_dotenv(ENV_DIR / ".env.nuru")

# BASE_MODEL is the plain Ollama model create_model() builds from; MODEL is
# the derived, custom model - built fresh from its family's Modelfile on every startup -
# that actually gets loaded and chatted with, carrying the tool-use SYSTEM
# prompt baked in.
BASE_MODEL = os.environ.get("NURU_BASE_MODEL", "granite4.2:3b")
MODEL = os.environ.get("MODEL", "nuru")


# What goes with BASE_MODEL's family besides its Modelfile (modelfiles/profiles.yaml): how thinking
# levels translate to Ollama's `think`, the level to run at, and sampling that depends on thinking.
PROFILE = model_profile.for_model(BASE_MODEL)
# The Ollama `think` value every model call starts from: the family's default level, translated.
THINK = PROFILE.think(PROFILE.default)
# Plan mode (a test: core.planner) - the model that writes plans, and how much it thinks.
PLANNER_MODEL = os.environ.get("NURU_PLANNER_MODEL", MODEL)
PLANNER_PROFILE = model_profile.for_runtime_model(PLANNER_MODEL, derived_model=MODEL, base_model=BASE_MODEL)
PLANNER_THINK = PLANNER_PROFILE.think(os.environ.get("NURU_PLANNER_THINK", "off"))

MODELFILES_DIR = Path(__file__).resolve().parents[2] / "modelfiles"


def _modelfile_for(base_model: str) -> Path:
    """The Modelfile carrying `base_model`'s sampling parameters, picked by model
    family - the name with any registry namespace and `:tag` stripped, so
    `qwen3.5:9b`, `qwen3.5` and `library/qwen3.5:latest` all resolve to
    modelfiles/qwen3.5.Modelfile.

    A family without its own file is an error rather than a fallback: the
    PARAMETER blocks are tuned per model, so quietly reusing another family's
    would sample the model wrongly and skew any bakeoff run against it.
    """
    family = model_profile.family(base_model)
    path = MODELFILES_DIR / f"{family}.Modelfile"
    if not path.exists():
        available = ", ".join(sorted(p.stem for p in MODELFILES_DIR.glob("*.Modelfile")))
        raise FileNotFoundError(
            f"No Modelfile for base model {base_model!r} (looked for {path}). "
            f"Available families: {available}. Add {family}.Modelfile to run it."
        )
    return path


OBSIDIAN_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "servers", "obsidian")
# Nuru's own obsidian server refuses to change notes the user wrote outside user/ (author: user) -
# see _reject_user_authored in the server. Other clients of the same server aren't affected.
OBSIDIAN_ENV = {"OBSIDIAN_PROTECT_USER_AUTHORED": "1"}
OBSIDIAN_SERVER_PARAMS = StdioServerParameters(
    command="uv", args=["run", "obsidian-mcp"], cwd=OBSIDIAN_DIR, env={**get_default_environment(), **OBSIDIAN_ENV}
)

DEEP_THINK_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "servers", "deep-think")
# ask_an_expert's file_paths are the vault-relative ones the obsidian tools returned, but
# deep-think runs in its own directory, so without a base to resolve them against every
# attachment fails with FileNotFoundError. Added to the MCP client's usual restricted env
# rather than replacing it; .env.deep-think supplies the default when this is unset.
_deep_think_env = get_default_environment()
if _vault := (os.environ.get("DEEP_THINK_BASE_DIR") or os.environ.get("VAULT_PATH")):
    _deep_think_env["DEEP_THINK_BASE_DIR"] = _vault
DEEP_THINK_SERVER_PARAMS = StdioServerParameters(
    command="uv", args=["run", "deep-think-mcp"], cwd=DEEP_THINK_DIR, env=_deep_think_env
)

def expert_api_key() -> str | None:
    """The expert's Anthropic API key: ANTHROPIC_API_KEY if this process has one, else the
    CLAUDE_API_KEY that deep-think (the ask_an_expert tool) already reads from .env.deep-think - one key
    for both, kept in one gitignored file and never copied into code, config or logs. A packaged Nuru
    (a standalone app, an Obsidian plugin) replaces this with its own settings or the system keychain,
    so the key can be added after installing."""
    return os.environ.get("ANTHROPIC_API_KEY") or dotenv_values(ENV_DIR / ".env.deep-think").get("CLAUDE_API_KEY")


MATH_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "servers", "math-4-llms")
MATH_SERVER_PARAMS = StdioServerParameters(
    command="cargo", args=["run", "--quiet", "--release", "--locked", "--features", "mcp", "--bin", "math-mcp"], cwd=MATH_DIR
)

PYTHON_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "servers", "python-4-llms")


def python_server_params() -> StdioServerParameters | None:
    """run_python's server (python-4-llms: a script in a fresh Docker sandbox), or None when it can't
    run - no .env.python, or Docker not answering. The server exits at startup when its sandbox
    checks fail, which would stop Nuru starting at all; without it Nuru starts without the tool."""
    env_file = ENV_DIR / ".env.python"
    settings = {k: v for k, v in dotenv_values(env_file).items() if v} if env_file.exists() else {}
    docker, socket = settings.get("PYTHON_MCP_DOCKER"), settings.get("PYTHON_MCP_DOCKER_SOCKET")
    if not (docker and socket and settings.get("PYTHON_MCP_IMAGE")):
        return None
    try:
        subprocess.run([docker, "--host", f"unix://{socket}", "info", "--format", "{{.ServerVersion}}"],
                       capture_output=True, timeout=5, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return StdioServerParameters(command="cargo", args=["run", "--quiet", "--release", "--locked", "--bin", "python-mcp"],
                                 cwd=PYTHON_DIR, env={**get_default_environment(), **settings})


LAYA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "servers", "laya")
# Laya reads no notes, so it gets no vault. NURU_DECISION_CONFIG, when set (an eval run's own
# config, logging to the run's own decision database), is passed on so both ends use it.
_laya_env = get_default_environment()
if _decision_config := os.environ.get("NURU_DECISION_CONFIG"):
    _laya_env["NURU_DECISION_CONFIG"] = _decision_config
LAYA_SERVER_PARAMS = StdioServerParameters(command="uv", args=["run", "laya-mcp"], cwd=LAYA_DIR, env=_laya_env)

LOG_DIR = Path(os.environ.get("NURU_LOG_DIR", Path(__file__).resolve().parents[2] / ".logs"))
DB_PATH = Path(os.environ.get("NURU_DB_PATH", Path(__file__).resolve().parents[2] / ".db" / "conversation.db"))

DEFAULT_NUM_CTX = 8192
# Ceiling on the context window nuru asks Ollama to allocate. Ollama sizes the KV
# cache from num_ctx, so requesting a large model's full architectural maximum
# (262144 for gemma4:12b) spills part of it onto the CPU and roughly halves
# generation speed.
MAX_NUM_CTX = int(os.environ.get("NURU_MAX_NUM_CTX", 32768))
# Leave this fraction of the context window free for the model's response.
RESPONSE_RESERVE = 0.2

# Rough chars-per-token estimate for rejecting an oversized message before it
# ever reaches the model - calibrated against an actual measurement (155,667
# chars of plain English filler text -> ~37,062 prompt tokens once the fixed
# tool-schema overhead is subtracted, i.e. ~4.2 chars/token).
CHARS_PER_TOKEN_ESTIMATE = 4

# DB role -> role the chat API expects.
_ROLE_TO_API = {"user": "user", "nuru": "assistant"}

# Stands in for the reply when the model ended a turn having written only its thinking, which is
# out as a Thinking event of its own by then.
_NO_REPLY_NOTE = "(No reply was written.)"

# Put in place of a tool result that made the next request longer than the context window, so
# the model hears what happened instead of the turn ending on Ollama's 400.
_TOO_LONG_NOTE = (
    "[The {name} result was about {tokens} tokens - too long for the context window, so it was left "
    "out. Do not call {name} again the same way. Answer from what you already have, or ask for less.]"
)


# The step cap's last call: no tools, an answer from what the turn has. Without it, a turn that ran
# out of steps (test 32: three notes, a correction and the arithmetic) ended with only a log.
_FINAL = "final"
_FINAL_ANSWER = (
    "[Nuru] That was the last tool round this turn (the {limit}-step limit), so no more tools. Answer now "
    "from the results above: give what you found and worked out, with the numbers and note paths, and say "
    "plainly what is still unresolved or unchecked."
)

# ask_an_expert's user_request is the user's message verbatim. The session has it, so it fills
# the field in itself: a small model asked to copy it paraphrased it, errors included.
_EXPERT_TOOL = "ask_an_expert"
_INJECTED_EXPERT_ARG = "user_request"

# Answered ask_an_expert calls allowed per turn (a failed call doesn't count). A small model
# otherwise sends its summary of an answer back for review and asks again, reworded, until the turn
# times out - two minutes and an Opus call each. The last answer carries _EXPERT_LAST_ANSWER, and the
# tool is then taken out of the tools offered; a call made anyway ends the turn with _EXPERT_CLARIFY,
# a question to the user (decided 2026-09-30).
_EXPERT_ANSWERS_PER_TURN = 2
_EXPERT_LAST_ANSWER = (
    f"\n\n[Nuru] That was the expert's last answer this turn ({_EXPERT_ANSWERS_PER_TURN} of "
    f"{_EXPERT_ANSWERS_PER_TURN}); ask_an_expert is no longer available. If the answers settle the "
    "request, reply to the user now and say which parts came from the expert. If they don't, don't guess: "
    "tell the user what is still unclear and ask them to clarify, so the expert can be asked again."
)
_EXPERT_CLARIFY = (
    f"the expert has already answered {_EXPERT_ANSWERS_PER_TURN} times this turn, and more was still needed. "
    "What's still unclear? Tell me, and I'll ask the expert again with that"
)


def _hide_injected_args(tool_definitions: list[dict]) -> list[dict]:
    """The tool definitions without the arguments the session fills in itself, so the model
    isn't asked for them."""
    hidden = []
    for tool in tool_definitions:
        fn = tool["function"]
        properties = fn.get("parameters", {}).get("properties", {})
        if fn["name"] == _EXPERT_TOOL and _INJECTED_EXPERT_ARG in properties:
            parameters = dict(fn["parameters"])
            parameters["properties"] = {k: v for k, v in properties.items() if k != _INJECTED_EXPERT_ARG}
            if "required" in parameters:
                parameters["required"] = [r for r in parameters["required"] if r != _INJECTED_EXPERT_ARG]
            tool = {**tool, "function": {**fn, "parameters": parameters}}
        if fn["name"] == _PYTHON_TOOL:
            tool = {**tool, "function": {**fn, "description": _PYTHON_DESCRIPTION, "parameters": _PYTHON_PARAMETERS}}
        hidden.append(tool)
    return hidden


# run_python as the model sees it: code plus the notes the script works on. Nuru reads the notes
# itself and gives the script their text as stdin JSON, so the model never retypes a note's numbers
# (on test 9 it dropped, shifted and invented values copying a table into statistics). stdin and
# timeout_ms are the session's to fill; the output is cut to what a 16k context has room for.
_PYTHON_TOOL = "run_python"
PYTHON_OUTPUT_CHARS = 6000
_PYTHON_DESCRIPTION = (
    "Run a Python script (standard library only: csv, statistics, datetime, math, re, json) in a sandbox "
    "and return what it prints. For computing from notes: pass their exact paths in notes, and the script "
    "gets their full text on stdin as JSON {path: text} - read it with json.load(sys.stdin) and parse the "
    "values there; never copy a note's numbers into the code. No file, network or vault access otherwise. "
    "Print every result with a label. Check ok and status; on SCRIPT_ERROR read stderr, fix the script and run it again."
)
_PYTHON_PARAMETERS = {
    "type": "object",
    "properties": {
        "code": {"type": "string", "description": "The whole Python script. Each call starts fresh."},
        "notes": {"type": "array", "items": {"type": "string"},
                  "description": "Exact paths of the notes the script needs; their text arrives on stdin as JSON {path: text}."},
    },
    "required": ["code"],
}


def _python_request(args: dict, read_note: Callable[[str], Any]) -> tuple[dict | None, str | None]:
    """run_python's server arguments for the model's {code, notes}: the notes' full text, read by
    Nuru (`read_note`: the obsidian tool, so its folder rules hold), goes to the script as stdin JSON.
    Returns (arguments, None), or (None, why the script wasn't run)."""
    notes = args.get("notes") or []
    if isinstance(notes, str):
        notes = [notes]
    texts = {}
    for note in notes:
        result = read_note(note)
        if result.is_error:
            return None, f"the note {note!r} could not be read: {result.text[:200]}"
        texts[note] = result.text
    request = {"code": args.get("code") or ""}
    if texts:
        request["stdin"] = json.dumps(texts, ensure_ascii=False)
    return request, None


def _python_result(text: str) -> str:
    """run_python's result with stdout and stderr cut to PYTHON_OUTPUT_CHARS each."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return text[:PYTHON_OUTPUT_CHARS]
    if not isinstance(data, dict):
        return text[:PYTHON_OUTPUT_CHARS]
    for key in ("stdout", "stderr"):
        value = data.get(key)
        if isinstance(value, str) and len(value) > PYTHON_OUTPUT_CHARS:
            data[key] = value[:PYTHON_OUTPUT_CHARS] + f"\n[... {len(value) - PYTHON_OUTPUT_CHARS} more characters]"
    return json.dumps(data, ensure_ascii=False)


def _expert_answered(content: str) -> bool:
    """Whether an ask_an_expert result carries an answer - not an error, and not an empty one."""
    try:
        result = json.loads(content)
    except (TypeError, ValueError):
        return False
    return isinstance(result, dict) and bool(result.get("answer"))


def _context_overflow(error: requests.exceptions.RequestException) -> bool:
    """Ollama's 400 for a request longer than num_ctx: "request (N tokens) exceeds the available
    context size (M tokens)"."""
    response = getattr(error, "response", None)
    return (
        response is not None
        and response.status_code == 400
        and "exceeds the available context size" in response.text
    )


def _context_window(model_info: dict | None) -> int:
    """The window nuru actually requests: the model's own maximum, capped at MAX_NUM_CTX."""
    return min((model_info or {}).get("context_window") or DEFAULT_NUM_CTX, MAX_NUM_CTX)


def _token_budget(context_window: int) -> int:
    return int(context_window * (1 - RESPONSE_RESERVE))


def _estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN_ESTIMATE


def _timestamp_line(now: datetime) -> str:
    """The line put ahead of each message sent to the model, so it always knows the local date and time."""
    return f"[Current date and time: {now:%A %Y-%m-%d %H:%M %Z}]"


def _cancelled(cancel: threading.Event | None) -> bool:
    return cancel is not None and cancel.is_set()


@dataclass(frozen=True)
class Text:
    """A chunk of the model's reply."""

    text: str


@dataclass(frozen=True)
class ToolCall:
    """The model asked for a tool. It runs right after this is yielded."""

    name: str
    arguments: dict


@dataclass(frozen=True)
class Thinking:
    """What the model reasoned on its way to a reply or a tool call. One per model call, and it
    arrives once that call is done, so it follows the reply text it was reasoning towards."""

    text: str


@dataclass(frozen=True)
class RetryWithoutThinking:
    """The model ended a call having written only its reasoning, so the turn is being run again
    with thinking off. Whatever follows this came from that second call, not the silent first one."""


@dataclass(frozen=True)
class Steered(RetryWithoutThinking):
    """watch_thinking (enforced) stopped the model's reasoning - `reason` says why: over budget,
    repeating, ready to act, stuck - and the turn goes on with thinking off, as after a silent call."""

    reason: str = ""


@dataclass(frozen=True)
class Failure:
    """The turn couldn't be completed. `message` says why, in words fit to show the user."""

    message: str


Event = Text | ToolCall | Thinking | RetryWithoutThinking | Steered | Failure


class Session:
    """One conversation with Nuru: the model, its tools and the stored history.

    Get one from start(), which does the slow setup, and release it with close().
    """

    def __init__(
        self,
        tools: MCPTools,
        tool_definitions: list[dict],
        logger: CLILogger,
        store: ConversationStore,
        conversation_id: int,
        shadow: DecisionShadow | None = None,
        enforcement: Enforcement | None = None,
    ) -> None:
        self.tools = tools
        self.tool_definitions = tool_definitions
        self.logger = logger
        self.store = store
        self.conversation_id = conversation_id
        self.shadow = shadow  # asks the decision layer at every decision point and logs its answers
        self.enforcement = enforcement  # acts on those answers, for the points set to enforce
        # Asked when an enforced gate_write says a write needs the user's OK; None means nobody is
        # there to ask (the UI, an eval run), and such a write is blocked.
        self.confirm_write: Callable[[str, dict], bool] | None = None
        self.indexer: VaultIndexer | None = None  # embeds changed notes in the background; None without a vault
        self.model_info: dict | None = None

    @classmethod
    def start(cls) -> "Session":
        """Build the custom model, connect the MCP servers, open the log and the database, load the
        model, and return the session that ties them together. Whatever was opened is closed
        again if a later step fails."""
        create_model(MODEL, BASE_MODEL, _modelfile_for(BASE_MODEL))

        decision_config = load_decision_config()  # refuses enforce on uncalibrated thresholds

        with ExitStack() as opened:
            python_server = python_server_params()
            tools = MCPTools(
                [OBSIDIAN_SERVER_PARAMS, DEEP_THINK_SERVER_PARAMS, MATH_SERVER_PARAMS, LAYA_SERVER_PARAMS,
                 *([python_server] if python_server else [])],
                model_tools=MODEL_TOOLS,
            )
            tools.connect()
            opened.callback(tools.close)
            tool_definitions = _hide_injected_args(tools.list_tools())

            logger = CLILogger(
                LOG_DIR,
                model=MODEL,
                think=THINK,
                server_command="uv run obsidian-mcp; uv run deep-think-mcp; cargo run --bin math-mcp; uv run laya-mcp"
                + ("; cargo run --bin python-mcp" if python_server else ""),
            )
            store = ConversationStore(DB_PATH)
            opened.callback(store.close)
            decision_log = DecisionLog(decision_config.database_path)
            opened.callback(decision_log.close)
            shadow = DecisionShadow(tools.call_harness_tool, decision_log, logger.session_id)
            shadow.effort_off = decision_config.mode("assess_effort") == "off"
            shadow.note_type_off = decision_config.mode("note_type") == "off"
            planner = ExpertPlanner(decision_config.expert, decision_log, api_key=expert_api_key())
            enforcement = Enforcement(decision_config, PROFILE.think_map, planner)

            session = cls(tools, tool_definitions, logger, store, store.create_conversation(), shadow, enforcement)
            session.load_model()
            session.indexer = VaultIndexer.from_env()
            if session.indexer:
                session.indexer.on_change = lambda result: logger.index_pass(
                    result.embedded, result.moved, result.removed
                )
                session.indexer.start()
                opened.callback(session.indexer.stop)
            opened.pop_all()  # it worked, so the session owns them now
        return session

    @property
    def indexing(self) -> int:
        """How many notes the background indexer has left to embed; 0 while it's idle."""
        return self.indexer.pending if self.indexer else 0

    @property
    def index_change(self) -> tuple[int, str]:
        """(how many indexing passes have changed the index, what the latest one did), for the
        window to announce a pass too quick to catch while it ran; (0, "") without an indexer."""
        return (self.indexer.changes, self.indexer.last_change) if self.indexer else (0, "")

    @property
    def context_window(self) -> int:
        return _context_window(self.model_info)

    def load_model(self) -> None:
        """Make sure the Ollama server is up and the model is loaded, starting either if it isn't."""
        server_spawned = ensure_server_running()
        self.model_info = ensure_model_loaded(MODEL)
        self.logger.model_lifecycle(server_spawned, self.model_info)

    def loaded_model_info(self) -> dict | None:
        """Static info about the model, or None if it isn't in memory right now."""
        return ensure_model_loaded(MODEL) if is_model_loaded(MODEL) else None

    def ask(self, text: str, cancel: threading.Event | None = None) -> Iterator[Event]:
        """Send `text` to the model, behind a line giving the current date and time, and yield what
        happens next: chunks of its reply, what it reasoned, and the tool calls it makes, then a
        Failure if the turn couldn't be completed. A turn that ends with reasoning but no reply of
        its own says so in one line.

        Setting `cancel` stops the turn at the next chunk (or once the running tool call
        returns); whatever the model had said by then is kept as its reply.

        Decision points set to enforce in the decision-layer config change the turn as
        core.enforcement describes; in shadow (the default) the decision layer only watches.
        """
        budget = _token_budget(self.context_window)
        estimated_tokens = _estimate_tokens(text)
        if estimated_tokens > budget:
            yield Failure(
                f"message too long: ~{estimated_tokens} estimated tokens, "
                f"which exceeds the {budget}-token budget for this model's context window. "
                "Please shorten it and try again."
            )
            return

        if planner.is_plan_request(text):
            yield from self._plan_turn(text)
            return

        user_message_id = self.store.add_message(self.conversation_id, "user", text)
        turn_id = self.logger.turn_start(text)
        decisions = self.shadow.begin(text, turn_id) if self.shadow else None
        enforcing = self.enforcement if self.enforcement and self.enforcement.any and self.shadow else None
        policy = enforcing.new_task() if enforcing else None
        first_read = enforcing.first_tool(decisions.gate) if enforcing and decisions else None
        failure = False  # a failure signal since the last model call (enforced tiers move up on it)
        tool_rounds = 0
        final_answer = False  # the step cap's last call, without tools, has been asked for
        messages = None
        prompt_tokens_recorded = False
        think = THINK
        # Reasoning from a call that ended with no reply, kept in case the retry below writes
        # nothing either and it has to stand in as the reply after all.
        carried_thinking = ""
        # (index in messages, tool name) of the last round's tool results, and whether one has
        # already been cut for overflowing the context window - which happens at most once a turn.
        last_results: list[tuple[int, str]] = []
        cut_a_result = False
        expert_answers = 0  # answered ask_an_expert calls this turn, up to _EXPERT_ANSWERS_PER_TURN
        # watch_thinking stopped a call: the next call runs with thinking off, then `resume_think` is
        # back. Off for the rest of the turn, Granite looped on tool calls instead (test 6, 2026-09-29).
        steered, resume_think = False, think
        calls_seen: Counter = Counter()  # (tool, arguments) -> times called this turn

        while True:
            self.load_model()

            num_ctx = _context_window(self.model_info)
            if messages is None:
                history = self.store.get_recent_messages(self.conversation_id, _token_budget(num_ctx))
                messages = [{"role": _ROLE_TO_API[m["role"]], "content": m["content"]} for m in history]
                # Only what the model sees carries the timestamp; the saved message stays as typed.
                messages[-1]["content"] = f"{_timestamp_line(datetime.now().astimezone())}\n\n{messages[-1]['content']}"

            tools_offered = self.tool_definitions
            if expert_answers >= _EXPERT_ANSWERS_PER_TURN:
                tools_offered = [t for t in tools_offered if t["function"]["name"] != _EXPERT_TOOL]
            hold_reply = False  # check_claim enforced: nothing is shown until the reply is checked
            if enforcing:
                if first_read:
                    yield from self._run_first_read(first_read, text, messages, turn_id, enforcing)
                    first_read = None
                    tool_rounds += 1
                    decisions = self.shadow.before_next_call()
                capped = enforcing.on("step_cap") and tool_rounds >= enforcing.step_cap
                if capped and final_answer:
                    next_think, tier = think, _FINAL  # the last answer's own retry (an empty reply)
                elif capped:
                    next_think, tier = enforcing.at_step_cap(policy, think)
                    if tier == STOP:  # no expert, or not at the cap: answer from what the turn has
                        next_think, tier = think, _FINAL
                else:
                    next_think, tier = enforcing.think_for(policy, decisions.effort if decisions else None, failure, think)
                failure = False
                if tier == STOP:
                    reason = (f"it reached the {enforcing.step_cap}-step limit" if capped else
                              "the local model kept failing at the highest thinking level")
                    if not enforcing.expert_available():
                        reason += ", and the expert isn't available (no API key, or today's budget is used up)"
                    yield from self._stop(turn_id, reason)
                    return
                if tier == "expert":
                    try:
                        plan = enforcing.expert_plan(text, self.tool_definitions, self.shadow.notes, self.shadow.steps)
                    except ExpertUnavailable as e:
                        if not capped:
                            yield from self._stop(turn_id, f"it needed the expert, and the expert couldn't help: {e}")
                            return
                        next_think, tier = think, _FINAL
                    else:
                        yield Thinking(f"Plan from the expert:\n{plan}")
                        messages.append({"role": "user", "content": f"[Nuru] A plan from the expert. Carry it out with your tools, then reply:\n{plan}"})
                if tier == _FINAL and not final_answer:
                    messages.append({"role": "user", "content": _FINAL_ANSWER.format(limit=enforcing.step_cap)})
                    final_answer = True
                think = False if steered else next_think
                if final_answer or (tier != "expert" and not enforcing.offer_tools(decisions.gate if decisions else None)):
                    tools_offered = None
                hold_reply = enforcing.on("check_claim")

            tool_calls = None
            usage: dict = {}
            thinking = ""
            output_text = ""
            watch, steer = None, None
            if self.shadow and self.enforcement and think:
                watch = ThinkingWatch(self.enforcement.config.watch_thinking, think_tier(think), self.shadow.watch)
                self.shadow.watch_enforced = self.enforcement.on("watch_thinking")
            start = time.monotonic()
            try:
                stream = chat(MODEL, messages, num_ctx=num_ctx, think=think, tools=tools_offered, **PROFILE.options(think))
                for out in stream:
                    if _cancelled(cancel):
                        self._end_cancelled(turn_id, output_text)
                        return
                    if isinstance(out, dict):
                        if "thinking_chunk" in out:
                            if watch and (signal := watch.feed(out["thinking_chunk"])):
                                if signal.source == "code":
                                    self.shadow.watch_signal(signal.kind, signal.tokens, signal.detail)
                                # Code signals act when the watch is enforced; Laya's only when watch_laya is too.
                                if self.shadow.watch_enforced and (signal.source == "code" or self.enforcement.on("watch_laya")):
                                    steer = signal
                                    break
                            continue
                        if "tool_calls" in out:
                            tool_calls = out["tool_calls"]
                        elif "thinking" in out:
                            thinking = out["thinking"].strip()
                            if thinking:
                                yield Thinking(thinking)
                        else:
                            usage = out
                        continue
                    output_text += out
                    if not hold_reply:
                        yield Text(out)
                if steer:
                    stream.close()  # stops the model mid-thought
                    usage = {"stop_reason": f"steered: {steer.kind}"}
            except requests.exceptions.RequestException as e:
                if last_results and not cut_a_result and _context_overflow(e):
                    # A tool result made this request too long. Swap the largest one from that
                    # round for a note saying so and ask again, rather than end the turn on a 400.
                    index, name = max(last_results, key=lambda result: len(messages[result[0]]["content"]))
                    tokens = _estimate_tokens(messages[index]["content"])
                    messages[index]["content"] = _TOO_LONG_NOTE.format(name=name, tokens=tokens)
                    cut_a_result = True
                    continue
                self.logger.turn_error(turn_id, str(e))
                self.store.delete_message(user_message_id)
                yield Failure(
                    f"request to the model failed: {e}\n"
                    "Your message was not saved - try again, or try a shorter prompt."
                )
                return

            if not prompt_tokens_recorded:
                self.store.update_tokens(user_message_id, usage.get("prompt_tokens"))
                prompt_tokens_recorded = True

            self.logger.model_call(
                turn_id=turn_id,
                duration_ms=(time.monotonic() - start) * 1000,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                stop_reason=usage.get("stop_reason"),
                tool_calls_requested=[call["function"]["name"] for call in tool_calls] if tool_calls else [],
                output_text=output_text,
            )
            if self.shadow:
                self.shadow.model_called(
                    [call["function"]["name"] for call in tool_calls] if tool_calls else [],
                    not output_text.strip() and not steer, think,
                )
                if watch:
                    self.shadow.watch_done(steer is not None, watch.tokens)
            if steered and not steer:
                think, steered = resume_think, False
            if steer:
                # watch_thinking enforced: the reasoning so far is out, and the model is asked to act.
                if watch.text.strip():
                    yield Thinking(watch.text.strip())
                yield Steered(steer.kind)
                messages.append({"role": "user", "content": STEER.get(steer.kind, STEER["default"])})
                resume_think = think
                think, steered = False, True
                continue

            if final_answer and tool_calls:  # tools were withdrawn, and it asked for one anyway
                yield from self._stop(turn_id, f"it reached the {enforcing.step_cap}-step limit")
                return

            if not tool_calls:
                if thinking and not output_text.strip() and think:
                    carried_thinking = thinking
                    if enforcing and enforcing.tiers_enforced:
                        # Empty content is a failure signal: the tier policy moves up a tier.
                        failure = True
                        continue
                    # Some thinking models write the whole answer into their reasoning and end the
                    # turn there. That is a routing failure rather than a truncation - the call ends
                    # with done_reason "stop" - so asking again with thinking off puts the answer in
                    # the reply instead. `think` stays off for the rest of the turn, which is also
                    # what keeps this to a single retry.
                    think = False
                    yield RetryWithoutThinking()
                    continue
                if not output_text.strip() and (thinking or carried_thinking):
                    # The retry wrote nothing either. The reasoning is already out as a Thinking
                    # event, so the reply only says that none was written; the history keeps the
                    # reasoning as what the model said.
                    output_text = thinking or carried_thinking
                    yield Text(_NO_REPLY_NOTE)
                    if self.shadow:
                        self.shadow.end(_NO_REPLY_NOTE)
                elif hold_reply:
                    output_text = yield from self._checked_reply(output_text, messages, num_ctx, think, policy, enforcing)
                    self.shadow.finish()
                elif self.shadow:
                    self.shadow.end(output_text)
                self.store.add_message(self.conversation_id, "nuru", output_text, usage.get("completion_tokens"))
                self.logger.turn_end(turn_id)
                return

            if hold_reply and output_text:
                yield Text(output_text)  # words ahead of a tool call aren't the reply being checked
            tool_rounds += 1
            messages.append({"role": "assistant", "tool_calls": tool_calls})
            last_results = []
            for call in tool_calls:
                fn = call["function"]
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    args = json.loads(args)
                yield ToolCall(fn["name"], args)
                if (stop := self._repeated_call(fn["name"], args, calls_seen)) is not None:
                    yield from self._stop(turn_id, stop)
                    return
                if fn["name"] == _EXPERT_TOOL:
                    args = {**self._resolve_attachments(args), _INJECTED_EXPERT_ARG: text}
                if enforcing and enforcing.on("gate_write") and fn["name"] in WRITE_TOOLS:
                    content, failed = yield from self._forced_write(fn, args, messages[:-1], num_ctx, think, turn_id)
                    failure = failure or failed
                    messages.append({"role": "tool", "content": content})
                    last_results.append((len(messages) - 1, fn["name"]))
                    continue
                args, write_gate, gate_id = self._before_tool(fn["name"], args, enforcing)
                if self._write_blocked(write_gate):
                    content = self._refuse_tool(turn_id, fn["name"], args, write_gate, gate_id)
                elif fn["name"] == _EXPERT_TOOL and expert_answers >= _EXPERT_ANSWERS_PER_TURN:
                    # Called although it's no longer offered: ask the user rather than loop.
                    self.logger.tool_call(turn_id, fn["name"], args, "refused", "expert limit reached", 0.0)
                    self.shadow.after_tool(fn["name"], args, None, 0.0, gate_id, error="refused: expert limit reached")
                    yield from self._stop(turn_id, _EXPERT_CLARIFY)
                    return
                else:
                    content, _ = self._call_tool(turn_id, fn["name"], args, gate_id, enforcing)
                    if fn["name"] == _EXPERT_TOOL and _expert_answered(content):
                        expert_answers += 1
                        if expert_answers >= _EXPERT_ANSWERS_PER_TURN:
                            content += _EXPERT_LAST_ANSWER
                messages.append({"role": "tool", "content": content})
                last_results.append((len(messages) - 1, fn["name"]))
                if _cancelled(cancel):
                    self._end_cancelled(turn_id, output_text)
                    return
            if self.shadow:
                decisions = self.shadow.before_next_call()

    # ------------------------------------------------------------------ running one tool
    def _before_tool(self, name: str, args: dict, enforcing: Enforcement | None) -> tuple[dict, dict | None, int | None]:
        """Before a tool runs: Laya's note type for a write_note (filled in when note_type is
        enforced) and gate_write for a write. Returns the arguments to run with, the write gate's
        verdict and its decision id."""
        typed = self.shadow.note_type(args) if self.shadow and name == "write_note" else None
        if (typed and typed.get("type") and typed["type"] != "correction" and not args.get("type")
                and enforcing and enforcing.on("note_type")):
            # Laya decides a new note's type when the model gave none (an existing note keeps its own;
            # the server ignores it). Never a correction: that needs targets only the model can give.
            args = {**args, "type": typed["type"]}
            self.shadow.gate_action(typed, f"filled:{typed['type']}")
        write_gate = self.shadow.before_tool(name, args) if self.shadow else None
        return args, write_gate, write_gate["decision_id"] if write_gate else None

    def _write_blocked(self, write_gate: dict | None) -> bool:
        """Code found a hard problem with a write (an invented note ID, a blocked tool, a protected
        path) and write_checks is enforced: the write doesn't run, whatever Laya would say."""
        return bool(write_gate and write_gate.get("source") == "code" and self.enforcement
                    and self.enforcement.on("write_checks"))

    def _refuse_tool(self, turn_id: str, name: str, args: dict, write_gate: dict, gate_id: int | None) -> str:
        reason = "; ".join(write_gate.get("reasons") or [])
        self.logger.tool_call(turn_id, name, args, "refused", reason, 0.0)
        self.shadow.after_tool(name, args, None, 0.0, gate_id, error=f"blocked: {reason}")
        self.shadow.gate_action(write_gate, "blocked")
        return f"[Nuru] {name} was not run: {reason}."

    def _call_tool(self, turn_id: str, name: str, args: dict, gate_id: int | None,
                   enforcing: Enforcement | None) -> tuple[str, bool]:
        """Run one tool, log it, and record it with the decision layer. Returns what the model
        sees as the result (reranked, when rerank is enforced) and whether the tool succeeded."""
        tool_start = time.monotonic()
        call_args = args
        if name == _PYTHON_TOOL:
            call_args, refused = _python_request(args, lambda note: self.tools.call_harness_tool("read_note", {"name": note}))
            if refused:
                duration_ms = (time.monotonic() - tool_start) * 1000
                self.logger.tool_call(turn_id, name, args, "error", refused, duration_ms)
                if self.shadow:
                    self.shadow.after_tool(name, args, None, duration_ms, gate_id, error=refused)
                return json.dumps({"ok": False, "status": "NOTE_NOT_READ", "message": refused}), False
        try:
            result = self.tools.call_tool_result(name, call_args)
        except Exception as e:
            duration_ms = (time.monotonic() - tool_start) * 1000
            self.logger.tool_call(turn_id, name, args, "error", str(e), duration_ms)
            if self.shadow:
                self.shadow.after_tool(name, args, None, duration_ms, gate_id, error=str(e))
            raise
        content = _python_result(result.text) if name == _PYTHON_TOOL else result.text
        duration_ms = (time.monotonic() - tool_start) * 1000
        self.logger.tool_call(turn_id, name, args, "error" if result.is_error else "ok",
                              result.text[:300] if result.is_error else None, duration_ms)
        if self.shadow:
            reranked = self.shadow.after_tool(name, args, result, duration_ms, gate_id)
            if reranked and enforcing and enforcing.on("rerank"):
                content = reranked
        return content, not result.is_error

    # ------------------------------------------------------------------ enforcement
    def _run_first_read(self, tool: str, query: str, messages: list[dict], turn_id: str, enforcing: Enforcement):
        """tool_gate enforced: the harness runs the first search itself, as if the model had."""
        args = {"query": query}
        yield ToolCall(tool, args)
        tool_start = time.monotonic()
        result = self.tools.call_tool_result(tool, args)
        duration_ms = (time.monotonic() - tool_start) * 1000
        self.logger.tool_call(turn_id, tool, args, "error" if result.is_error else "ok", None, duration_ms)
        reranked = self.shadow.after_tool(tool, args, result, duration_ms)
        content = reranked if reranked and enforcing.on("rerank") else result.text
        messages += [
            {"role": "assistant", "tool_calls": [{"function": {"name": tool, "arguments": args}}]},
            {"role": "tool", "content": content},
        ]

    def _forced_write(self, fn: dict, args: dict, context: list[dict], num_ctx: int, think, turn_id: str):
        """gate_write enforced: the write's arguments come from a forced schema (IDs only from the
        last search), are validated, and go through gate_write. Returns (tool result text, whether
        this was a failure signal)."""
        name = fn["name"]
        schema = next(t["function"].get("parameters", {}) for t in self.tool_definitions if t["function"]["name"] == name)

        def chat_json(msgs: list[dict], fmt: dict, think_value) -> str:
            return "".join(out for out in chat(MODEL, msgs, num_ctx=num_ctx, think=think_value, fmt=fmt,
                                                **PROFILE.options(think_value)) if isinstance(out, str))

        def not_run(reason: str, failed: bool, gate: dict | None = None, action: str | None = None):
            self.logger.tool_call(turn_id, name, args, "refused", reason, 0.0)
            self.shadow.after_tool(name, args, None, 0.0, error=reason)
            if action:
                self.shadow.gate_action(gate, action)
            return f"[Nuru] {name} was not run: {reason}.", failed

        # The model's own arguments first, under the same rules; the forced schema only if they fail.
        # Re-asking arguments that were already right made the model take the cannot_do way out
        # (enforced quick suite, 2026-09-28) - validation alone is what keeps invented IDs out.
        forced = validate_arguments(name, args, set(self.shadow.last_search_ids))
        if forced.problems:
            forced = ask_for_arguments(chat_json, context, name, schema, self.shadow.last_search_ids, think)
        if forced.problems:
            forced = ask_for_arguments(chat_json, context, name, schema, self.shadow.last_search_ids, think)
        if forced.problems:
            self.shadow.note_failure("schema_failures")
            return not_run(f"no valid arguments ({'; '.join(forced.problems)})", True)
        if forced.cannot_do is not None:
            self.shadow.note_failure("cannot_do")
            return not_run(f"the model couldn't do it ({forced.cannot_do})", True)
        args = forced.arguments
        fn["arguments"] = args  # the conversation shows the call that actually runs
        gate = self.shadow.before_tool(name, args)
        verdict = gate["verdict"] if gate else "ask"  # the decision layer down: ask
        if verdict == "block":
            return not_run(f"blocked ({'; '.join(gate.get('reasons') or []) or 'it did not match the request'})", False, gate, "blocked")
        if verdict == "ask":
            if self.confirm_write is None:
                return not_run("it needs your confirmation, and nobody was there to give it", False, gate, "blocked_unattended")
            if not self.confirm_write(name, args):
                return not_run("you declined it", False, gate, "declined")
        yield ToolCall(name, args)
        tool_start = time.monotonic()
        result = self.tools.call_tool_result(name, args)
        duration_ms = (time.monotonic() - tool_start) * 1000
        self.logger.tool_call(turn_id, name, args, "error" if result.is_error else "ok", None, duration_ms)
        self.shadow.after_tool(name, args, result, duration_ms, gate["decision_id"] if gate else None)
        return result.text, False

    def _checked_reply(self, draft: str, messages: list[dict], num_ctx: int, think, policy, enforcing: Enforcement):
        """check_claim enforced: show the reply only once it's checked. A failed one is written again
        once, with the log in view; if that fails too, it is shown with a warning and the log."""
        claim = self.shadow.check_reply(draft, "shown")
        if claim is None:
            yield Text(draft + UNVERIFIED)
            return draft + UNVERIFIED
        if claim["passed"]:
            final = draft + ("" if claim["verified"] else UNVERIFIED)
            yield Text(final)
            return final
        self.shadow.record_claim_action(claim, "regenerated")
        self.shadow.note_failure("claim_failures")
        log = log_lines(self.shadow.executed)
        if enforcing.tiers_enforced:  # a failure signal: one tier up for the rewrite, but no further
            up, tier = enforcing.think_for(policy, None, True, think)
            think = up if tier not in (STOP, "expert") else think
        retry = [*messages, {"role": "assistant", "content": draft},
                 {"role": "user", "content": REGENERATE.format(failures="; ".join(claim["failures"]), log=log)}]
        try:
            second = "".join(out for out in chat(MODEL, retry, num_ctx=num_ctx, think=think, **PROFILE.options(think))
                             if isinstance(out, str))
        except requests.exceptions.RequestException:
            second = ""
        second_claim = self.shadow.check_reply(second, "shown") if second.strip() else None
        if second_claim and second_claim["passed"]:
            final = second + ("" if second_claim["verified"] else UNVERIFIED)
        else:
            self.shadow.record_claim_action(second_claim, "shown_with_warning")
            failures = (second_claim or claim)["failures"]
            final = (second.strip() or draft) + WARNING.format(failures="; ".join(failures), log=log)
        yield Text(final)
        return final

    def _resolve_attachments(self, arguments: dict) -> dict:
        """ask_an_expert's file_paths as the vault's real paths. The model often names a note by its
        title ("Area Monitor Device Redesign"), which deep-think can't open; the obsidian server
        resolves it as read_note would. A name it can't resolve (an attachment, a typo) is passed on
        as given, and fails there with deep-think's own message."""
        paths = arguments.get("file_paths")
        if not paths or not isinstance(paths, list):
            return arguments
        resolved = []
        for path in paths:
            try:
                result = self.tools.call_harness_tool("note_info", {"name": path})
                resolved.append(json.loads(result.text)["note"] if not result.is_error else path)
            except Exception:
                resolved.append(path)
        return {**arguments, "file_paths": resolved}

    def _repeated_call(self, tool: str, arguments: dict, calls_seen: Counter) -> str | None:
        """Counts the call; the reason to stop the turn if it is a loop - the same tool, the same
        arguments, `repeat_calls` times - and watch_thinking is enforced. In shadow a loop is logged
        once and the call runs."""
        if not (self.enforcement and self.shadow):
            return None
        # Ignoring case: the vault's paths and searches do, so nuru/indexes/x.md re-reads nuru/Indexes/x.md.
        key = (tool, json.dumps(arguments, sort_keys=True).lower())
        calls_seen[key] += 1
        count, limit = calls_seen[key], self.enforcement.config.watch_thinking.repeat_calls
        enforced = self.enforcement.on("watch_thinking")
        if count < limit or (count > limit and not enforced):
            return None
        self.shadow.repeated_call(tool, arguments, count, enforced)
        return f"it called {tool} with the same arguments {count} times" if enforced else None

    def _plan_turn(self, text: str) -> Iterator[Event]:
        """A "PLAN:" message: the plan core.planner writes for the request, and what code found
        wrong with it. No tool runs and the decision layer isn't asked."""
        self.store.add_message(self.conversation_id, "user", text)
        turn_id = self.logger.turn_start(text)
        tools = [t for t in self.tool_definitions if t["function"]["name"] != _EXPERT_TOOL]

        def chat_json(messages: list[dict], schema: dict, think) -> str:
            start, out, usage = time.monotonic(), "", {}
            for piece in chat(PLANNER_MODEL, messages, num_ctx=_context_window(self.model_info), think=think, fmt=schema,
                              **PLANNER_PROFILE.options(think)):
                if isinstance(piece, str):
                    out += piece
                elif "stop_reason" in piece:
                    usage = piece
            self.logger.model_call(
                turn_id=turn_id,
                duration_ms=(time.monotonic() - start) * 1000,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                stop_reason=usage.get("stop_reason"),
                tool_calls_requested=[],
                output_text=out,
            )
            return out

        plan, problems = planner.make_plan(planner.request_of(text), tools, chat_json, PLANNER_THINK)
        reply = "```json\n" + json.dumps(plan, indent=1) + "\n```" if plan else "No plan was written."
        reply += ("\n\nProblems code found:\n" + "\n".join(f"- {p}" for p in problems)) if problems else "\n\nCode found no problems."
        yield Text(reply)
        self.store.add_message(self.conversation_id, "nuru", reply)
        self.logger.turn_end(turn_id)

    def run_plan(self, text: str, plan: dict, cancel: threading.Event | None = None) -> Iterator[Event]:
        """Carry out `plan` (core.planner's format) for the request `text`, the harness running every
        tool step through the same gates and logs as ask(); the model only picks, drafts and answers
        (core.executor). A step that fails stops the plan - nothing after it runs, so no write follows
        a failure - and the answer says what happened. Yields the same events as ask()."""
        self.load_model()
        num_ctx = _context_window(self.model_info)
        self.store.add_message(self.conversation_id, "user", text)
        turn_id = self.logger.turn_start(text)
        if self.shadow:
            self.shadow.begin(text, turn_id)
        enforcing = self.enforcement if self.enforcement and self.enforcement.any and self.shadow else None
        now = _timestamp_line(datetime.now().astimezone())  # what ask() puts ahead of the request
        steps = list(plan["steps"])
        if not steps or steps[-1].get("think") != "answer":
            steps.append({"step": f"s{len(steps) + 1}", "think": "answer", "uses": [], "want": "the reply"})
        by_name = {step.get("step"): step for step in steps}
        values: dict[str, Any] = {}
        log: list[dict] = []
        stopped: str | None = None
        reply = ""

        def call(messages: list[dict], fmt: dict | None):
            """One model call; a call that only thought is asked again with thinking off, as in ask()."""
            think = THINK
            while True:
                start, out, thinking, usage = time.monotonic(), "", "", {}
                for piece in chat(MODEL, messages, num_ctx=num_ctx, think=think, fmt=fmt, **PROFILE.options(think)):
                    if isinstance(piece, str):
                        out += piece
                    elif "thinking" in piece:
                        thinking = piece["thinking"].strip()
                    elif "stop_reason" in piece:
                        usage = piece
                self.logger.model_call(
                    turn_id=turn_id, duration_ms=(time.monotonic() - start) * 1000,
                    prompt_tokens=usage.get("prompt_tokens", 0), completion_tokens=usage.get("completion_tokens", 0),
                    stop_reason=usage.get("stop_reason"), tool_calls_requested=[], output_text=out,
                )
                if thinking:
                    yield Thinking(thinking)
                if out.strip() or not think:
                    return out
                think = False
                yield RetryWithoutThinking()

        labels: dict[str, str] = {}  # what each step's output is, in words - never its name

        def inputs_of(step: dict) -> dict[str, str]:
            return {labels[u]: v if isinstance(v, str) else json.dumps(v)
                    for u in step.get("uses", []) if (v := values.get(u)) is not None}

        def draft(step: dict, rejected: str = ""):
            want = step.get("want", "") + (f"\nYour last attempt was rejected: {rejected}" if rejected else "")
            schema = executor.consumer_schema(step["step"], steps, self.tool_definitions)
            out = yield from call(executor.draft_messages(text, want, inputs_of(step), now),
                                  {"type": "object", "properties": {"value": schema}, "required": ["value"]})
            try:
                return json.loads(out)["value"]
            except (json.JSONDecodeError, KeyError, TypeError):
                return None

        for step in steps:
            name, kind = step.get("step"), step.get("think")
            if _cancelled(cancel):
                self._end_cancelled(turn_id, reply)
                return
            if stopped and kind != "answer":
                continue
            if "tool" in step:
                tool = step["tool"]
                for attempt in (1, 2):
                    try:
                        args = executor.resolve(step.get("with") or {}, values)
                    except KeyError as missing:
                        stopped = f"{name} needs the output of {missing.args[0]}, which has none"
                        break
                    yield ToolCall(tool, args)
                    args, write_gate, gate_id = self._before_tool(tool, args, enforcing)
                    if self._write_blocked(write_gate):
                        content, ok = self._refuse_tool(turn_id, tool, args, write_gate, gate_id), False
                    else:
                        content, ok = self._call_tool(turn_id, tool, args, gate_id, enforcing)
                    failed = executor.tool_failed(tool, content, ok)
                    source = executor.refs_in(step.get("with", {}).get("expression", "")) if tool == "calculate" else []
                    if failed and attempt == 1 and len(source) == 1 and by_name.get(source[0], {}).get("think") == "draft":
                        # A rejected expression: write it again once, told why.
                        values[source[0]] = yield from draft(by_name[source[0]], f"{args.get('expression')!r}: {failed}")
                        continue
                    values[name] = executor.step_value(tool, content, ok)
                    labels[name] = f"The result of {tool} {json.dumps(args)[:300]}"
                    log.append({"step": name, "what": labels[name], "output": values[name]})
                    if failed:
                        stopped = f"{name} ({tool}) failed: {failed}"
                    break
            elif kind == "pick":
                options = list(dict.fromkeys(p for u in step.get("uses", []) for p in executor.candidates(values.get(u, ""))))
                if not options:
                    stopped = f"{name}: the results had nothing to choose from for {step.get('want')!r}"
                    continue
                out = yield from call(executor.pick_messages(text, step.get("want", ""), inputs_of(step), now),
                                      {"type": "object", "properties": {"choice": {"enum": options + ["none"]}},
                                       "required": ["choice"]})
                try:
                    choice = json.loads(out)["choice"]
                except (json.JSONDecodeError, KeyError, TypeError):
                    choice = None
                if choice in (None, "none"):
                    stopped = f"{name}: nothing in the results was {step.get('want')!r}"
                    continue
                values[name] = choice
                labels[name] = f"Chosen as {step.get('want')!r}"
                log.append({"step": name, "what": labels[name], "output": choice})
            elif kind == "draft":
                value = yield from draft(step)
                if value is None:
                    stopped = f"{name}: the draft couldn't be written"
                    continue
                values[name] = value
                labels[name] = f"Written as {step.get('want')!r}"
                log.append({"step": name, "what": labels[name], "output": value})
            elif kind == "answer":
                reply = (yield from call(executor.answer_messages(text, step.get("want", ""), log, stopped, now), None)).strip()
                yield Text(reply)
        self.store.add_message(self.conversation_id, "nuru", reply)
        if self.shadow:
            self.shadow.end(reply)
        self.logger.turn_end(turn_id)

    def _stop(self, turn_id: str, reason: str):
        """End the turn plainly, saying why and what had happened by then."""
        text = STOPPED.format(reason=reason, log=log_lines(self.shadow.executed))
        yield Text(text)
        self.store.add_message(self.conversation_id, "nuru", text)
        self.logger.turn_end(turn_id)
        self.shadow.finish()

    def _end_cancelled(self, turn_id: str, output_text: str) -> None:
        """Ends a turn the user stopped. What the model had said so far is kept as its reply so the
        history stays user/nuru alternating and the model knows what it said. No usage figures
        arrive for a cut-short call, so the messages keep a NULL tokens_used."""
        self.store.add_message(self.conversation_id, "nuru", output_text)
        self.logger.turn_end(turn_id)

    def shutdown(self, reason: str) -> None:
        """Unload every model and stop the Ollama server if we started it, and log why the session ended."""
        unload_all_models()
        stop_server()
        self.logger.session_end(reason=reason, models_unloaded=True, server_stopped=True)

    def close(self) -> None:
        """Stop the indexer and release the MCP servers and the database. Leaves the model loaded and
        Ollama running."""
        if self.indexer:
            self.indexer.stop()
        self.tools.close()
        self.store.close()
        if self.shadow:
            self.shadow.close()
