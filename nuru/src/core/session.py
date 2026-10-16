"""Everything a Nuru chat needs that doesn't depend on how it's shown.

A `Session` brings up the model and its MCP tools, runs one turn at a time (streaming the
reply, running the tool calls the model makes, logging and saving as it goes) and shuts it all
down again. A turn is a generator of events, so cli.py prints them and ui.py renders them.
"""

import json
import os
import secrets
import subprocess
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
import anyio
from mcp.shared.exceptions import MCPError
from dotenv import dotenv_values, load_dotenv
from laya_mcp.config import load_config as load_decision_config
from laya_mcp.checks import WRITE_TOOLS
from laya_mcp.decision_log import DecisionLog
from mcp import StdioServerParameters
from mcp.client.stdio import get_default_environment

from core.cli_logging import CLILogger
from core.conversation_store import ConversationStore
from core.context_budget import ContextBudgetExceeded, available_tokens, estimate_tokens, input_budget, require_fit
from core.decision_shadow import LAYA_POINTS, DecisionShadow, think_tier
from core.enforcement import REGENERATE, STEER, STOP, STOPPED, UNVERIFIED, WARNING, Enforcement, ExpertUnavailable, log_lines
from core.expert import ExpertPlanner
from core import executor, model_profile, planner, questions
from core.providers.mcp_tools import MCPTools, ToolResult
from core.questions import Question
from core import tool_dispatch
from core.tool_dispatch import ToolOutcome, MUTATIONS, written_path
from claude_access.keys import api_key as claude_api_key
from core.modelfile import render_system
from core.providers import claude as claude_provider
from core.providers.ollama import chat as ollama_chat
from core.providers.ollama import (
    create_model,
    ensure_model_loaded,
    ensure_server_running,
    is_model_loaded,
    stop_server,
    unload_all_models,
)
from core.vault_indexer import VaultIndexer, semantic_db
from core.vault_link_auditor import VaultLinkAuditor
from core.vault_steward import VaultSteward
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
# NURU_MAIN_MODEL=anthropic:<id> (claude-haiku-5-5 or claude-sonnet-5-5) runs the turn on Claude through
# the shared claude-access provider instead of Ollama; unset, or "ollama", keeps the local model. With
# Claude, MODEL is the Claude model and no Modelfile is built - Ollama only serves the notes search's
# embeddings.
MAIN_MODEL = os.environ.get("NURU_MAIN_MODEL", "").strip()
CLAUDE = MAIN_MODEL.startswith(claude_provider.PREFIX)
if MAIN_MODEL and not CLAUDE and MAIN_MODEL != "ollama":
    raise ValueError(f"NURU_MAIN_MODEL={MAIN_MODEL!r}: use 'ollama' or anthropic:<model>")
MODEL = claude_provider.model_id(MAIN_MODEL) if CLAUDE else os.environ.get("MODEL", "nuru")
chat = claude_provider.chat if CLAUDE else ollama_chat


# What goes with BASE_MODEL's family besides its Modelfile (modelfiles/profiles.yaml): how thinking
# levels translate to Ollama's `think`, the level to run at, and sampling that depends on thinking.
# With Claude, the Claude model's own profile.
PROFILE = model_profile.for_model(MODEL if CLAUDE else BASE_MODEL)
# The Ollama `think` value every model call starts from: the family's default level, translated.
THINK = PROFILE.think(PROFILE.default)
# Off by default (tested 2026-10-15): NURU_KEEP_THINKING=1 keeps a call's thinking and text on its
# tool-call message for the rest of the turn, as Qwen's template expects. Ollama drops it again once a
# new user message follows. The Claude provider's content (its thinking blocks included) is always
# kept: the API requires earlier thinking blocks back unchanged during tool use.
KEEP_THINKING = os.environ.get("NURU_KEEP_THINKING") == "1"


def _tool_call_message(tool_calls: list, thinking: str, text: str, provider_content: list | None = None) -> dict:
    message = {"role": "assistant", "tool_calls": tool_calls}
    if provider_content:
        message["provider_content"] = provider_content
    if KEEP_THINKING:
        if thinking:
            message["thinking"] = thinking
        if text.strip():
            message["content"] = text
    return message
# Plan mode (a test: core.planner) - the model that writes plans, and how much it thinks.
PLANNER_MODEL = os.environ.get("NURU_PLANNER_MODEL", MODEL)
PLANNER_PROFILE = model_profile.for_runtime_model(PLANNER_MODEL, derived_model=MODEL,
                                                   base_model=MODEL if CLAUDE else BASE_MODEL)
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
DEEP_THINK_SERVER_PARAMS = StdioServerParameters(
    command="uv", args=["run", "deep-think-mcp"], cwd=DEEP_THINK_DIR, env=get_default_environment()
)

def expert_api_key() -> str | None:
    """The Anthropic API key, for the expert planner and Claude as the main model alike: one key, read
    by claude_access.keys (ANTHROPIC_API_KEY, else CLAUDE_API_KEY, else .env.deep-think)."""
    return claude_api_key()


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
# When a failed tool call couldn't be put right (recovery stopped, or its two calls are used up). Worded
# like _FINAL_ANSWER: a terse "Recovery budget exhausted. Answer from successful evidence only..." was
# once copied back as the whole reply (test 16, 2026-10-07).
_RECOVERY_ANSWER = (
    "[Nuru] The failed tool call above couldn't be put right this turn, so no more tools or writes. Answer "
    "my request now from the results that succeeded: give what you found and worked out, with the numbers "
    "and note paths, and say plainly which step failed and what is still unresolved."
)

# ask_user (core.questions) is offered to the model only with NURU_ASK_USER=1, until a model run shows
# when it asks: too often, and Nuru checks in instead of doing the work.
ASK_USER = os.environ.get("NURU_ASK_USER") == "1"

def _hide_injected_args(tool_definitions: list[dict]) -> list[dict]:
    """The tool definitions without the arguments the session fills in itself, so the model
    isn't asked for them."""
    hidden = []
    for tool in tool_definitions:
        fn = tool["function"]
        properties = fn.get("parameters", {}).get("properties", {})
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
    "additionalProperties": False,
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
    return input_budget(context_window)


def _estimate_tokens(text: str) -> int:
    return estimate_tokens(text)


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
    """The model asked for a tool. It runs right after this is yielded. `call_id` names this call in
    its outcome (Session.on_tool_outcome) and in the write ledger; it isn't part of what a call is."""

    name: str
    arguments: dict
    call_id: str = field(default="", compare=False)


@dataclass(frozen=True)
class Thinking:
    """What the model reasoned on its way to a reply or a tool call. One per model call, and it
    arrives once that call is done, so it follows the reply text it was reasoning towards. In
    ask(), ThinkingChunk events have streamed the same text as it came."""

    text: str


@dataclass(frozen=True)
class ThinkingChunk:
    """A piece of the model's reasoning, as it streams in. The Thinking event that ends the call
    carries the whole of it again: a viewer that showed the pieces settles them with that rather
    than showing the reasoning twice, and one that only wants the whole can ignore these."""

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


@dataclass(frozen=True)
class Ask:
    """The model asked the user `question` (the ask_user tool), which ends the turn. The answer goes
    back as the next message (questions.answer_text). Nothing follows this in the turn."""

    question: Question


Event = Text | ToolCall | Thinking | ThinkingChunk | RetryWithoutThinking | Steered | Failure | Ask


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
        system_text: str = "",
        server_tools: list[dict] | None = None,
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
        # Asked a question code raises mid-turn (a write to OK, a save the message ruled out); returns
        # the chosen labels, or None if nobody answered. Used when confirm_write isn't set.
        self.answer_question: Callable[[Question], list[str] | None] | None = None
        # Told how each tool call shown as a ToolCall turned out - (its call_id, name, arguments,
        # outcome, whether it's a write) - once it has run or been refused; nuru-serve reports it to its
        # clients. Calls never shown (refusals once tools are closed, siblings skipped after a failure)
        # and the calls Nuru makes for itself (the notes run_python reads) aren't reported.
        self.on_tool_outcome: Callable[[str, str, dict, ToolOutcome, bool], None] | None = None
        self._shown_call: str | None = None  # the call_id of the ToolCall last shown, until it runs
        self._turn_text = ""  # the user's message this turn: what "do not save" is looked for in
        self.indexer: VaultIndexer | None = None  # embeds changed notes in the background; None without a vault
        self.link_auditor: VaultLinkAuditor | None = None
        self.steward: VaultSteward | None = None
        self.model_info: dict | None = None
        self._cached_system_text = system_text
        self._tool_validators = tool_dispatch.validators(tool_definitions)
        self._server_validators = tool_dispatch.validators(server_tools or tool_definitions)
        self._vault_scope = str(Path(os.environ.get("VAULT_PATH") or dotenv_values(ENV_DIR / ".env.obsidian").get("VAULT_PATH") or ".").resolve())
        self._turn_writes: list[tuple[str, dict, ToolOutcome]] = []
        self._repair_rounds = 0
        # conversation_id -> (known_ids, last_search_ids) of the conversations not in use (use_conversation)
        self._evidence: dict[int, tuple[set[str], list[str]]] = {}

    @classmethod
    def start(cls, conversation_id: int | None = None, *, load_model: bool = True, vault: Path | None = None) -> "Session":
        """Build the custom model, connect the MCP servers, open the log and the database, load the
        model, and return the session that ties them together. Whatever was opened is closed
        again if a later step fails.

        `conversation_id` resumes a stored conversation instead of starting a new one. With
        load_model=False the model loads on the first ask(), as nuru-serve wants: its status and
        history must work while no model is loaded. `vault` names the vault outright, for the notes
        server and the write ledger alike; without it each finds its own (VAULT_PATH, else
        .env.obsidian), and the notes server, which doesn't get this process's VAULT_PATH, may find
        another."""
        if CLAUDE:  # no Modelfile: the same system.md goes out with each request instead
            system_text = render_system((MODELFILES_DIR / "system.md").read_text())
        else:
            rendered = create_model(MODEL, BASE_MODEL, _modelfile_for(BASE_MODEL))
            system_text = rendered.split('SYSTEM """', 1)[1].rsplit('"""', 1)[0]

        decision_config = load_decision_config()  # refuses enforce on uncalibrated thresholds

        with ExitStack() as opened:
            python_server = python_server_params()
            obsidian, deep_think = OBSIDIAN_SERVER_PARAMS, DEEP_THINK_SERVER_PARAMS
            if vault is not None:
                vault = Path(vault).resolve()
                obsidian = obsidian.model_copy(update={"env": {**obsidian.env, "VAULT_PATH": str(vault),
                                                               "OBSIDIAN_SEMANTIC_DB": semantic_db(vault)}})
                deep_think = deep_think.model_copy(update={"env": {**deep_think.env, "DEEP_THINK_BASE_DIR": str(vault)}})
            laya_off = {point for point in LAYA_POINTS if decision_config.mode(point) == "off"}
            laya = [LAYA_SERVER_PARAMS] if laya_off != LAYA_POINTS else []  # all off: Laya isn't started
            tools = MCPTools(
                [obsidian, deep_think, MATH_SERVER_PARAMS, *laya,
                 *([python_server] if python_server else [])],
                model_tools=MODEL_TOOLS,
            )
            tools.connect()
            opened.callback(tools.close)
            server_tools = tools.list_tools()
            tool_definitions = _hide_injected_args(server_tools)

            logger = CLILogger(
                LOG_DIR,
                model=MODEL,
                think=THINK,
                server_command="uv run obsidian-mcp; uv run deep-think-mcp; cargo run --bin math-mcp"
                + ("; uv run laya-mcp" if laya else "") + ("; cargo run --bin python-mcp" if python_server else ""),
            )
            store = ConversationStore(DB_PATH)
            opened.callback(store.close)
            decision_log = DecisionLog(decision_config.database_path)
            opened.callback(decision_log.close)
            shadow = DecisionShadow(tools.call_harness_tool, decision_log, logger.session_id)
            shadow.off = laya_off
            planner = ExpertPlanner(decision_config.expert, decision_log, api_key=expert_api_key())
            enforcement = Enforcement(decision_config, PROFILE.think_map, planner)

            if conversation_id is None:
                conversation_id = store.create_conversation()
            session = cls(tools, tool_definitions, logger, store, conversation_id, shadow, enforcement, system_text, server_tools)
            if vault is not None:
                session._vault_scope = str(vault)
            if load_model:
                session.load_model()
            session.indexer = VaultIndexer.from_env()
            if session.indexer:
                session.indexer.on_change = lambda result: logger.index_pass(
                    result.embedded, result.moved, result.removed
                )
                session.indexer.start()
                opened.callback(session.indexer.stop)
            try:
                session.link_auditor = VaultLinkAuditor.from_env()
            except (ValueError, OSError) as exc:
                print(f"Nuru: link auditor disabled: {exc}", file=sys.stderr)
                session.link_auditor = None
            if session.link_auditor:
                session.link_auditor.on_change = logger.link_audit
                session.link_auditor.start()
                opened.callback(session.link_auditor.stop)
            try:
                session.steward = VaultSteward.from_env()
            except (ValueError, OSError) as exc:
                print(f"Nuru: vault steward disabled: {exc}", file=sys.stderr)
                session.steward = None
            if session.steward:
                session.steward.on_change = logger.stewardship
                session.steward.start()
                opened.callback(session.steward.stop)
            opened.pop_all()  # it worked, so the session owns them now
        return session

    def use_conversation(self, conversation_id: int) -> None:
        """Go on in another stored conversation (nuru-serve shares one Session between them). The note
        IDs the decision layer has seen and the last search's results are evidence for the conversation
        they came from: they're kept for it, and the other conversation gets its own back, or none."""
        if conversation_id == self.conversation_id:
            return
        if self.shadow:
            self._evidence[self.conversation_id] = (self.shadow.known_ids, self.shadow.last_search_ids)
            self.shadow.known_ids, self.shadow.last_search_ids = self._evidence.pop(conversation_id, (set(), []))
        self.conversation_id = conversation_id

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
        server_spawned = ensure_server_running()  # with Claude too: the notes search embeds through Ollama
        self.model_info = claude_provider.model_info(MODEL) if CLAUDE else ensure_model_loaded(MODEL)
        self.logger.model_lifecycle(server_spawned, self.model_info)

    def loaded_model_info(self) -> dict | None:
        """Static info about the model, or None if it isn't in memory right now."""
        if CLAUDE:
            return claude_provider.model_info(MODEL)
        return ensure_model_loaded(MODEL) if is_model_loaded(MODEL) else None

    def _system_text(self) -> str:
        return self._cached_system_text

    def _chat(self, model: str, messages: list[dict], *, num_ctx: int, history_count: int = 0, **kwargs):
        messages = [dict(message) for message in messages]
        original_history_count = history_count
        omitted_tokens = []
        # Evict complete old exchanges before sacrificing current-turn evidence.
        while history_count >= 2 and available_tokens(num_ctx, messages, system=self._system_text(),
                                                      tools=kwargs.get("tools"), fmt=kwargs.get("fmt")) < 0:
            offset = 1 if messages[0].get("role") == "system" else 0
            del messages[offset:offset + 2]
            history_count -= 2
        # Keep tool-call relationships, but omit the largest evidence bodies first.
        # Work on copies: the original transcript remains available for logging/recovery.
        for index in sorted((i for i, m in enumerate(messages) if m.get("role") == "tool"),
                            key=lambda i: estimate_tokens(messages[i].get("content") or ""), reverse=True):
            if available_tokens(num_ctx, messages, system=self._system_text(),
                                tools=kwargs.get("tools"), fmt=kwargs.get("fmt")) >= 0:
                break
            content = messages[index].get("content") or ""
            note = (f"[left out: ~{estimate_tokens(content)} tokens; evidence unavailable. "
                    "State what remains unchecked or retrieve a smaller section.]")
            if estimate_tokens(note) < estimate_tokens(content):
                messages[index]["content"] = note
                omitted_tokens.append(estimate_tokens(content))
        if history_count != original_history_count or omitted_tokens:
            self.logger.context_reduction(num_ctx, original_history_count - history_count, omitted_tokens)
        require_fit(num_ctx, messages, system=self._system_text(),
                    tools=kwargs.get("tools"), fmt=kwargs.get("fmt"))
        if CLAUDE:
            kwargs["system"] = self._system_text()
        yield from chat(model, messages, num_ctx=num_ctx, **kwargs)

    def _history(self, messages: list[dict], window: int, *, before_id=None, tools=None, fmt=None) -> list[dict]:
        budget = available_tokens(window, messages, system=self._system_text(), tools=tools, fmt=fmt)
        rows = self.store.get_recent_messages(self.conversation_id, budget, before_id=before_id)
        return [{"role": _ROLE_TO_API.get(m["role"], m["role"]), "content": m["content"]} for m in rows]

    def continuation_context(self) -> str:
        """Bounded complete exchanges for live planning, without the new request."""
        rows = self.store.get_recent_messages(self.conversation_id, _token_budget(self.context_window) // 4)
        return "\n".join(f"{m['role']}: {m['content']}" for m in rows)

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
        self.load_model()  # Admission uses the effective window, including after unload/reload.
        if planner.is_plan_request(text):
            yield from self._plan_turn(text)
            return
        current = {"role": "user", "content": f"{_timestamp_line(datetime.now().astimezone())}\n\n{text}"}
        try:
            require_fit(self.context_window, [current], system=self._system_text(), tools=self.tool_definitions)
        except ContextBudgetExceeded as e:
            yield Failure(f"message too long: {e}")
            return

        self._turn_writes = []
        self._repair_rounds = 0
        self._turn_text = text
        self._shown_call = None
        user_message_id = self.store.add_message(self.conversation_id, "user", text)
        turn_id = self.logger.turn_start(text)
        decisions = self.shadow.begin(text, turn_id) if self.shadow else None
        enforcing = self.enforcement if self.enforcement and self.enforcement.any and self.shadow else None
        policy = enforcing.new_task() if enforcing else None
        first_read = enforcing.first_tool(decisions.gate) if enforcing and decisions else None
        failure = False  # a failure signal since the last model call (enforced tiers move up on it)
        tool_rounds = 0
        answer_after_failure = False
        repair_pending: dict[str, ToolOutcome] = {}
        recovery_calls = 0  # Two corrections/recovery reads per turn, even without step_cap enforcement.
        final_answer = False  # the step cap's last call, without tools, has been asked for
        tools_refused = False  # a tool call after tools closed has been refused once
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
        # watch_thinking stopped a call: the next call runs with thinking off, then `resume_think` is
        # back. Off for the rest of the turn, Granite looped on tool calls instead (test 6, 2026-09-29).
        steered, resume_think = False, think
        calls_seen: Counter = Counter()  # (tool, arguments) -> times called this turn

        while True:
            if messages is not None:
                self.load_model()
            num_ctx = _context_window(self.model_info)
            if messages is None:
                messages = self._history([current], num_ctx, before_id=user_message_id, tools=self.tool_definitions)
                history_count = len(messages)
                messages.append(current)

            tools_offered = self.tool_definitions
            if ASK_USER:
                tools_offered = [*tools_offered, questions.TOOL]
            hold_reply = False  # check_claim enforced: nothing is shown until the reply is checked
            if enforcing:
                if first_read:
                    first_outcome = yield from self._run_first_read(first_read, text, messages, turn_id, enforcing)
                    if first_outcome.must_stop or (not first_outcome.success and not first_outcome.repairable):
                        yield from self._abort_turn(turn_id, first_outcome.user_message(first_read))
                        return
                    if not first_outcome.success:
                        repair_pending[first_read] = first_outcome
                    first_read = None
                    tool_rounds += 1
                    decisions = self.shadow.before_next_call()
                capped = enforcing.on("step_cap") and tool_rounds >= enforcing.step_cap
                if repair_pending and recovery_calls >= 2:
                    # A failed correction must get its final answer, not a tier escalation.
                    next_think, tier = think, "recovery_answer"
                elif capped and final_answer:
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
                if not final_answer and tier != "expert" and not enforcing.offer_tools(decisions.gate if decisions else None):
                    tools_offered = None
                hold_reply = enforcing.on("check_claim")

            # Once tools close (a final answer, or an answer after a refused call) the tool list still
            # goes out unchanged: it sits at the start of the prompt, so dropping it made Ollama re-read
            # the whole prompt (40-77 s a call in the 10-06 baseline). A call made anyway is refused below.
            if answer_after_failure or final_answer:
                if not final_answer and repair_pending:
                    messages.append({"role": "user", "content": _RECOVERY_ANSWER})
                    final_answer = True
            if repair_pending and not final_answer:
                if recovery_calls >= 2:
                    messages.append({"role": "user", "content": _RECOVERY_ANSWER})
                    final_answer = True

            if final_answer or answer_after_failure:
                think = False
            tool_calls = None
            provider_content = None
            usage: dict = {}
            thinking = ""
            output_text = ""
            watch, steer = None, None
            if self.shadow and self.enforcement and think:
                watch = ThinkingWatch(self.enforcement.config.watch_thinking, think_tier(think), self.shadow.watch)
                self.shadow.watch_enforced = self.enforcement.on("watch_thinking")
            start = time.monotonic()
            try:
                stream = self._chat(MODEL, messages, num_ctx=num_ctx, history_count=history_count, think=think, tools=tools_offered, **PROFILE.options(think))
                for out in stream:
                    if _cancelled(cancel):
                        self._end_cancelled(turn_id, output_text)
                        return
                    if isinstance(out, dict):
                        if "thinking_chunk" in out:
                            yield ThinkingChunk(out["thinking_chunk"])
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
                        elif "provider_content" in out:
                            provider_content = out["provider_content"]
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
            except ContextBudgetExceeded as e:
                yield from self._abort_turn(turn_id, str(e) + " The turn stopped.")
                return
            except requests.exceptions.RequestException as e:
                if last_results and not cut_a_result and _context_overflow(e):
                    # A tool result made this request too long. Swap the largest one from that
                    # round for a note saying so and ask again, rather than end the turn on a 400.
                    index, name = max(last_results, key=lambda result: len(messages[result[0]]["content"]))
                    tokens = _estimate_tokens(messages[index]["content"])
                    messages[index]["content"] = _TOO_LONG_NOTE.format(name=name, tokens=tokens)
                    cut_a_result = True
                    continue
                if self._turn_writes:
                    yield from self._abort_turn(turn_id, f"Model request failed: {e}. Earlier writes must not be repeated.")
                    return
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
                cache_read_tokens=usage.get("cache_read_tokens", 0), cache_write_tokens=usage.get("cache_write_tokens", 0),
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

            if (final_answer or answer_after_failure) and tool_calls and not tools_refused:
                # Tools are closed but still listed: refuse the calls once and ask again.
                tools_refused = True
                messages.append(_tool_call_message(tool_calls, thinking, output_text, provider_content))
                for call in tool_calls:
                    fn = call.get("function") or {}
                    refused = self._call_tool(turn_id, fn.get("name", ""), fn.get("arguments", {}), None, enforcing,
                        refusal=tool_dispatch.failure("policy", "TOOLS_CLOSED", "Not run: no more tools this turn. Answer now from the results above."))
                    messages.append({"role": "tool", "content": refused.text})
                continue

            if answer_after_failure and tool_calls:
                yield from self._abort_turn(turn_id, "The tool was refused; no further calls were allowed.")
                return

            if final_answer and tool_calls:  # tools were closed, and it asked for one again after a refusal
                reason = ("the recovery budget was exhausted" if repair_pending else
                          f"it reached the {enforcing.step_cap}-step limit")
                yield from self._stop(turn_id, reason)
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
            messages.append(_tool_call_message(tool_calls, thinking, output_text, provider_content))
            last_results = []
            correcting = bool(repair_pending)
            for index, call in enumerate(tool_calls):
                fn = call.get("function") or {}
                name = fn.get("name", "")
                raw_args = fn.get("arguments", {})
                if ASK_USER and name == questions.TOOL_NAME:
                    try:
                        question = questions.from_tool(raw_args)
                    except ValueError as e:
                        outcome = self._call_tool(turn_id, name, raw_args, None, enforcing,
                            refusal=tool_dispatch.failure("arguments", "INVALID_QUESTION", str(e), repairable=True))
                        messages.append({"role": "tool", "content": outcome.text})
                        failure = True
                        break
                    # The calls after it in this round don't run: the user's answer may change them.
                    yield from self._end_with_question(turn_id, question, output_text)
                    return
                args, invalid = tool_dispatch.validate(name, raw_args, self._tool_validators)
                shown_args = args if args is not None else {"invalid_arguments": str(raw_args)[:300]}
                yield self._show(name, shown_args)
                write_allowed = self._recovery_write_allowed(name, repair_pending)
                exhausted = correcting and recovery_calls >= 2
                if correcting:
                    recovery_calls += 1
                if exhausted or (correcting and index > 0):
                    outcome = self._call_tool(turn_id, name, raw_args, None, enforcing,
                        refusal=tool_dispatch.failure("repair", "REPAIR_LIMIT", "At most two recovery calls, one per model round, are allowed."))
                elif correcting and name in MUTATIONS and not write_allowed:
                    outcome = self._call_tool(turn_id, name, raw_args, None, enforcing,
                        refusal=tool_dispatch.failure("repair", "DEPENDENT_CALL_BLOCKED", "Repair failed evidence before writing. Reads and computations remain available."))
                elif invalid:
                    outcome = self._call_tool(turn_id, name, raw_args, None, enforcing)
                elif (stop := self._repeated_call(name, args, calls_seen)) is not None:
                    if correcting:
                        outcome = self._call_tool(turn_id, name, args, None, enforcing,
                            refusal=tool_dispatch.failure("repair", "REPEATED_CALL", stop + "; answer from successful evidence and explain the unresolved failure."))
                    else:
                        yield from self._stop(turn_id, stop)
                        return
                elif enforcing and enforcing.on("gate_write") and name in WRITE_TOOLS:
                    before_repair = self._repair_rounds
                    outcome = yield from self._forced_write(fn, args, messages[:-1], num_ctx, think, turn_id,
                        allow_repair=not (enforcing.on("step_cap") and tool_rounds >= enforcing.step_cap)
                                     and (not correcting or recovery_calls < 2))
                    tool_rounds += self._repair_rounds - before_repair
                    if correcting:
                        recovery_calls += self._repair_rounds - before_repair
                else:
                    outcome = self._call_tool(turn_id, name, args, None, enforcing)
                messages.append({"role": "tool", "content": outcome.text})
                last_results.append((len(messages) - 1, name))
                if outcome.must_stop:
                    yield from self._abort_turn(turn_id, outcome.user_message(name))
                    return
                if not outcome.success:
                    if outcome.kind == "policy":
                        answer_after_failure = True
                    elif not outcome.repairable:
                        answer_after_failure = True
                    else:
                        repair_pending[name] = outcome
                    failure = True
                    # Do not dispatch siblings whose arguments may depend on failed evidence.
                    for skipped in tool_calls[index + 1:]:
                        skipped_name = (skipped.get("function") or {}).get("name", "")
                        skipped_args = (skipped.get("function") or {}).get("arguments", {})
                        skipped_outcome = self._call_tool(turn_id, skipped_name, skipped_args, None, enforcing,
                            refusal=tool_dispatch.failure("dependency", "EARLIER_CALL_FAILED", "An earlier call failed; this call was not run."))
                        messages.append({"role": "tool", "content": skipped_outcome.text})
                    break
                if correcting:
                    repair_pending.pop(name, None)
                    # A replacement read/computation can replace an unavailable tool, but an
                    # unrelated successful read cannot clear an actual failed evidence source.
                    repair_pending = {n: o for n, o in repair_pending.items() if n in self._tool_validators}
                if _cancelled(cancel):
                    self._end_cancelled(turn_id, output_text)
                    return
            if self.shadow:
                decisions = self.shadow.before_next_call()

    # ------------------------------------------------------------------ running one tool
    def pending_writes(self) -> list[dict]:
        return self.store.pending_writes(self._vault_scope)

    def resolve_write(self, attempt_id: int, state: str) -> None:
        """Operator action, never exposed as a model tool. Stop active sessions first."""
        self.store.resolve_write(attempt_id, self._vault_scope, state)

    def _answer(self, turn_id: str, question: Question) -> list[str] | None:
        """The user's answer to a question code raises, or None with nobody there to ask. A question
        left unanswered (its time ran out, or the turn was stopped) is taken as No. Either way the
        outcome is logged."""
        if self.answer_question is None:
            self.logger.question(turn_id, question.text, None, questions.NO, "nobody there", None)
            return None
        started = time.monotonic()
        answer = self.answer_question(question)
        seconds = time.monotonic() - started
        if answer is None:
            self.logger.question(turn_id, question.text, None, questions.NO, "no answer", seconds)
            return [questions.NO]
        self.logger.question(turn_id, question.text, answer, questions.answer_text(answer), "answered", seconds)
        return answer

    def _in_shared(self, target: str) -> str | None:
        """`target` as a vault-relative path if it lands in shared/. Dot components and an absolute
        path are resolved against the vault, as the server resolves them, so "nuru/../shared/x.md"
        is shared/x.md."""
        vault = Path(self._vault_scope)
        try:
            relative = (vault / target.replace("\\", "/")).resolve().relative_to(vault)
        except ValueError:
            return None  # outside the vault: the server refuses it
        return relative.as_posix() if relative.parts and relative.parts[0].lower() == "shared" else None

    def _write_scope(self, name: str, args: dict) -> tuple[str, str]:
        """Where a write lands: ("shared", its path), ("private", ""), or ("unknown", why) when that
        can't be established. A note named by its title is found with note_info, the way the server
        finds it; one it reports as not found is created in nuru/. Any other failure is unknown, not
        private: the server may still resolve the name to a note in shared/."""
        if name == "rename_or_move_file":
            shared = self._in_shared(str(args.get("destination_path") or ""))
            return ("shared", shared) if shared else ("private", "")
        if name != "write_note" or args.get("type") == "daily" or not args.get("note"):
            return "private", ""
        note = str(args["note"])
        if shared := self._in_shared(note):
            return "shared", shared
        try:
            result = self.tools.call_harness_tool("note_info", {"name": note})
        except Exception as e:
            return "unknown", f"the note lookup failed: {type(e).__name__}"
        if result.is_error:
            if "No note found named" in result.text:
                return "private", ""
            return "unknown", "the note lookup failed"
        try:
            path = json.loads(result.text)["note"]
        except (ValueError, TypeError, KeyError):
            return "unknown", "the note lookup gave no path"
        shared = self._in_shared(str(path))
        return ("shared", shared) if shared else ("private", "")

    def _confirmed(self, turn_id: str, name: str, args: dict) -> bool | None:
        """The user's OK for a write gate_write wants confirmed; None if nobody was there to give it."""
        if questions.no_save_phrase(self._turn_text) or self._write_scope(name, args)[0] != "private":
            return True  # the "do not save" or shared/ question asks instead, once (_call_tool)
        if self.confirm_write:
            return self.confirm_write(name, args)
        answer = self._answer(turn_id, questions.write(name, args))
        return None if answer is None else questions.is_yes(answer)

    def _end_with_question(self, turn_id: str, question: Question, said: str):
        """End the turn on the model's ask_user question. The conversation keeps it as Nuru's reply,
        after anything the model wrote first, so the user's answer reads as a reply to it."""
        reply = (said.rstrip() + "\n\n" if said.strip() else "") + questions.as_text(question)
        self.store.add_message(self.conversation_id, "nuru", reply)
        if self.shadow:
            self.shadow.end(reply)
        self.logger.turn_end(turn_id)
        yield Ask(question)

    def _recovery_write_allowed(self, name: str, pending: dict[str, ToolOutcome]) -> bool:
        """Keep advertised tools stable; enforce failed-evidence dependencies at dispatch."""
        failed_evidence = any(tool not in MUTATIONS and tool in self._tool_validators for tool in pending)
        outcome = pending.get(name)
        return (not failed_evidence and outcome is not None
                and outcome.certainty in {"not_dispatched", "not_completed"})

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

    def _call_tool(self, turn_id: str, name: str, args, gate_id: int | None,
                   enforcing: Enforcement | None, *, harness: bool = False, gate_checked: bool = False, refusal: ToolOutcome | None = None) -> ToolOutcome:
        """One validated, logged dispatch for ordinary, forced, planned and injected calls."""
        call_id = None
        if not harness:
            call_id, self._shown_call = self._shown_call, None  # this is the call last shown, if any
        started = time.monotonic()
        original_args = args
        args, outcome = tool_dispatch.validate(name, args, self._tool_validators)
        outcome = refusal or outcome
        attempt_id = None
        if outcome is None and not gate_checked:
            args, write_gate, gate_id = self._before_tool(name, args, enforcing)
            if self._write_blocked(write_gate):
                reason = "; ".join(write_gate.get("reasons") or [])
                outcome = tool_dispatch.failure("policy", "NOT_RUN", f"{name} was not run: {reason}")
                self.shadow.gate_action(write_gate, "blocked")
            else:
                args, outcome = tool_dispatch.validate(name, args, self._tool_validators)
            if outcome is None and name in MUTATIONS and enforcing and enforcing.on("gate_write"):
                verdict = write_gate["verdict"] if write_gate else "ask"
                if verdict == "block":
                    outcome = tool_dispatch.failure("policy", "NOT_RUN", f"{name} was not run: write gate blocked it.")
                elif verdict == "ask" and not self._confirmed(turn_id, name, args):
                    outcome = tool_dispatch.failure("policy", "NOT_RUN", f"{name} was not run: confirmation was not given.")
                if outcome and self.shadow:
                    self.shadow.gate_action(write_gate, "blocked")
        # Worded for the model, which reads them; a hint addressed to "the user" gets repeated to them.
        if outcome is None and name in MUTATIONS and (said := questions.no_save_phrase(self._turn_text)):
            answer = self._answer(turn_id, questions.against_no_save(name, args, said))
            if not questions.is_yes(answer):
                outcome = tool_dispatch.failure("policy", "NOT_RUN", f'{name} was not run: this message says "{said}"'
                    + (", and the user didn't allow it when asked." if answer is not None else ".")
                    + " Give the result in the reply instead.")
        if outcome is None and name in MUTATIONS and (scope := self._write_scope(name, args))[0] != "private":
            kind, where = scope
            answer = self._answer(turn_id, questions.shared_write(name, args, where) if kind == "shared"
                                  else questions.unknown_write(name, args, where))
            if not questions.is_yes(answer):
                outcome = tool_dispatch.failure("policy", "NOT_RUN", f"{name} was not run: "
                    + (f"{where} is in shared/, which others read, and " if kind == "shared" else
                       f"Nuru couldn't confirm that {args.get('note')} is outside shared/ ({where}), and ")
                    + ("the user didn't allow it when asked." if answer is not None else "nobody was there to allow it.")
                    + " Give the result in the reply instead.")
        if outcome is None and name in MUTATIONS:
            attempt_id = self.store.begin_write(self.conversation_id, turn_id, self._vault_scope, name, args, call_id=call_id)
            if attempt_id is None:
                pending = self.store.pending_writes(self._vault_scope)
                outcome = tool_dispatch.failure("replay", "UNRESOLVED_WRITE", f"Write attempt {pending[0]['id'] if pending else 'from another session'} was unresolved at admission. "
                    "Inspect the vault and reconcile it with /resolve-write before any further writes.", certainty="not_dispatched")
        call_args = args
        if outcome is None and name == _PYTHON_TOOL:
            failed_read = None
            def read(note):
                nonlocal failed_read
                result = self._call_tool(turn_id, "read_note", {"name": note}, None, enforcing, harness=True)
                if not result.success:
                    failed_read = result
                return ToolResult(result.text, not result.success)
            call_args, refused = _python_request(args, read)
            if refused:
                outcome = tool_dispatch.failure("internal" if failed_read and failed_read.must_stop else "prerequisite",
                    "NOTE_NOT_READ", refused, repairable=bool(failed_read and failed_read.repairable))
            else:
                _, outcome = tool_dispatch.validate(name, call_args, self._server_validators)
                if outcome is not None:
                    outcome = tool_dispatch.failure("internal", "INJECTED_SCHEMA_MISMATCH", outcome.hint)
        if outcome is None:
            try:
                result = (self.tools.call_harness_tool if harness else self.tools.call_tool_result)(name, call_args)
                if name == _PYTHON_TOOL:
                    result = ToolResult(_python_result(result.text), result.is_error)
                outcome = tool_dispatch.classify(name, result)
            except Exception as e:
                # Do not confuse a programming error with repairable model arguments.
                transport = isinstance(e, (OSError, TimeoutError, requests.exceptions.RequestException,
                                           MCPError, anyio.EndOfStream, anyio.BrokenResourceError))
                outcome = tool_dispatch.failure("transport" if transport else "internal", type(e).__name__, str(e),
                    certainty="uncertain" if name in MUTATIONS else "failed", repairable=transport and name not in MUTATIONS)
        if attempt_id is not None:
            outcome = replace(outcome, attempt_id=attempt_id)
            self.store.finish_write(attempt_id, "completed" if outcome.success else
                                    "not_completed" if outcome.certainty == "not_completed" else "uncertain", outcome.hint,
                                    target=written_path(name, outcome))
            self._turn_writes.append((name, args, outcome))
        if name == _PYTHON_TOOL:
            outcome = replace(outcome, text=_python_result(outcome.text))
        duration_ms = (time.monotonic() - started) * 1000
        shown_args = args if args is not None else {"invalid_arguments": str(original_args)[:300]}
        self.logger.tool_call(turn_id, name, shown_args, "ok" if outcome.success else "refused" if outcome.kind in {"policy", "repair", "dependency", "replay"} else "error",
                              outcome.hint, duration_ms, outcome=outcome)
        if self.shadow:
            reranked = self.shadow.after_tool(name, shown_args, ToolResult(outcome.text, not outcome.success),
                                             duration_ms, gate_id, error=outcome.hint)
            if outcome.success and reranked and enforcing and enforcing.on("rerank"):
                outcome = replace(outcome, text=reranked)
        if self.on_tool_outcome and call_id:
            self.on_tool_outcome(call_id, name, shown_args, outcome, name in MUTATIONS)
        return outcome

    def _show(self, name: str, arguments: dict) -> ToolCall:
        """The ToolCall to yield for a call about to run: the next non-harness _call_tool is this call."""
        self._shown_call = secrets.token_urlsafe(12)
        return ToolCall(name, arguments, self._shown_call)

    def _partial_actions(self) -> str:
        if not self._turn_writes:
            return ""
        lines = []
        for name, args, outcome in self._turn_writes:
            target = args.get("note") or args.get("source_path") or "new note"
            status = ("confirmed completed" if outcome.success else "confirmed not completed" if
                      outcome.certainty == "not_completed" else "outcome uncertain; do not retry")
            lines.append(f"- {name} ({target}): {status} (attempt {outcome.attempt_id}).")
        return "\nEarlier write actions:\n" + "\n".join(lines)

    def _abort_turn(self, turn_id: str, reason: str):
        reply = reason + self._partial_actions()
        self.logger.turn_error(turn_id, reply)
        self.store.add_message(self.conversation_id, "nuru", reply)
        if self.shadow:
            self.shadow.finish()
        self.logger.turn_end(turn_id)
        yield Failure(reply)

    # ------------------------------------------------------------------ enforcement
    def _run_first_read(self, tool: str, query: str, messages: list[dict], turn_id: str, enforcing: Enforcement):
        """tool_gate enforced: the harness runs the first search itself, as if the model had."""
        args = {"query": query}
        yield self._show(tool, args)
        outcome = self._call_tool(turn_id, tool, args, None, enforcing)
        messages += [
            {"role": "assistant", "tool_calls": [{"function": {"name": tool, "arguments": args}}]},
            {"role": "tool", "content": outcome.text},
        ]
        return outcome

    def _forced_write(self, fn: dict, args: dict, context: list[dict], num_ctx: int, think, turn_id: str, *, allow_repair: bool = True):
        """gate_write enforced: the write's arguments come from a forced schema (IDs only from the
        last search), are validated, and go through gate_write. Returns the shared tool outcome."""
        name = fn["name"]
        schema = next(t["function"].get("parameters", {}) for t in self.tool_definitions if t["function"]["name"] == name)

        def chat_json(msgs: list[dict], fmt: dict, think_value) -> str:
            self._repair_rounds += 1
            started, output, usage = time.monotonic(), "", {}
            try:
                for out in self._chat(MODEL, msgs, num_ctx=num_ctx, think=think_value, fmt=fmt, **PROFILE.options(think_value)):
                    if isinstance(out, str):
                        output += out
                    elif "stop_reason" in out:
                        usage = out
            except ContextBudgetExceeded:
                return ""
            self.logger.model_call(turn_id, (time.monotonic() - started) * 1000,
                usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), usage.get("stop_reason"), [], output,
                cache_read_tokens=usage.get("cache_read_tokens", 0), cache_write_tokens=usage.get("cache_write_tokens", 0))
            return output

        def not_run(reason: str, failed: bool, gate: dict | None = None, action: str | None = None):
            outcome = tool_dispatch.failure("policy", "NOT_RUN", f"{name} was not run: {reason}", repairable=failed)
            if action:
                self.shadow.gate_action(gate, action)
            return self._call_tool(turn_id, name, args, None, self.enforcement, refusal=outcome)

        # The model's own arguments first, under the same rules; the forced schema only if they fail.
        # Re-asking arguments that were already right made the model take the cannot_do way out
        # (enforced quick suite, 2026-09-28) - validation alone is what keeps invented IDs out.
        forced = validate_arguments(name, args, set(self.shadow.last_search_ids))
        if forced.problems and not allow_repair:
            return not_run("the step limit left no room for argument repair", False)
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
            confirmed = self._confirmed(turn_id, name, args)
            if confirmed is None:
                return not_run("it needs your confirmation, and nobody was there to give it", False, gate, "blocked_unattended")
            if not confirmed:
                return not_run("you declined it", False, gate, "declined")
        yield self._show(name, args)
        return self._call_tool(turn_id, name, args, gate["decision_id"] if gate else None, self.enforcement, gate_checked=True)

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
        log = log_lines(self.shadow.executed if self.shadow else [])
        if enforcing.tiers_enforced:  # a failure signal: one tier up for the rewrite, but no further
            up, tier = enforcing.think_for(policy, None, True, think)
            think = up if tier not in (STOP, "expert") else think
        retry = [*messages, {"role": "assistant", "content": draft},
                 {"role": "user", "content": REGENERATE.format(failures="; ".join(claim["failures"]), log=log)}]
        try:
            second = "".join(out for out in self._chat(MODEL, retry, num_ctx=num_ctx, think=think, **PROFILE.options(think))
                             if isinstance(out, str))
        except (requests.exceptions.RequestException, ContextBudgetExceeded):
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
        self._turn_writes = []
        self._repair_rounds = 0
        self._turn_text = text
        self._shown_call = None
        user_message_id = self.store.add_message(self.conversation_id, "user", text)
        turn_id = self.logger.turn_start(text)
        tools = self.tool_definitions

        planner_info = ensure_model_loaded(PLANNER_MODEL) if PLANNER_MODEL != MODEL else self.model_info
        planner_window = _context_window(planner_info)
        prompt_tokens_recorded = False

        def chat_json(messages: list[dict], schema: dict, think) -> str:
            nonlocal prompt_tokens_recorded
            history = self._history(messages, planner_window, before_id=user_message_id, fmt=schema)
            messages = [messages[0], *history, *messages[1:]]
            start, out, usage = time.monotonic(), "", {}
            for piece in self._chat(PLANNER_MODEL, messages, num_ctx=planner_window, think=think, fmt=schema,
                              **PLANNER_PROFILE.options(think)):
                if isinstance(piece, str):
                    out += piece
                elif "stop_reason" in piece:
                    usage = piece
            if not prompt_tokens_recorded:
                self.store.update_tokens(user_message_id, usage.get("prompt_tokens"))
                prompt_tokens_recorded = True
            self.logger.model_call(
                turn_id=turn_id,
                duration_ms=(time.monotonic() - start) * 1000,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                stop_reason=usage.get("stop_reason"),
                cache_read_tokens=usage.get("cache_read_tokens", 0), cache_write_tokens=usage.get("cache_write_tokens", 0),
                tool_calls_requested=[],
                output_text=out,
            )
            return out

        try:
            plan, problems = planner.make_plan(planner.request_of(text), tools, chat_json, PLANNER_THINK)
        except ContextBudgetExceeded as e:
            reply = str(e)
            self.store.add_message(self.conversation_id, "nuru", reply)
            self.logger.turn_end(turn_id)
            yield Failure(reply)
            return
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
        now = _timestamp_line(datetime.now().astimezone())
        try:
            require_fit(num_ctx, executor.answer_messages(text, "the reply", [], None, now))
        except ContextBudgetExceeded as e:
            yield Failure(f"message too long: {e}")
            return
        self._turn_writes = []
        self._repair_rounds = 0
        self._turn_text = text
        self._shown_call = None
        user_message_id = self.store.add_message(self.conversation_id, "user", text)
        turn_id = self.logger.turn_start(text)
        if self.shadow:
            self.shadow.begin(text, turn_id)
        enforcing = self.enforcement if self.enforcement and self.enforcement.any and self.shadow else None
        steps = list(plan["steps"])
        if not steps or steps[-1].get("think") != "answer":
            steps.append({"step": f"s{len(steps) + 1}", "think": "answer", "uses": [], "want": "the reply"})
        by_name = {step.get("step"): step for step in steps}
        values: dict[str, Any] = {}
        log: list[dict] = []
        stopped: str | None = None
        tool_attempts = 0
        reply = ""

        prompt_tokens_recorded = False

        def call(messages: list[dict], fmt: dict | None):
            """One model call; a call that only thought is asked again with thinking off, as in ask()."""
            nonlocal prompt_tokens_recorded
            history = self._history(messages, num_ctx, before_id=user_message_id, fmt=fmt)
            messages = [messages[0], *history, *messages[1:]]
            try:
                require_fit(num_ctx, messages, fmt=fmt)
            except ContextBudgetExceeded as e:
                yield Failure(str(e))
                raise
            think = THINK
            while True:
                start, out, thinking, usage = time.monotonic(), "", "", {}
                for piece in self._chat(MODEL, messages, num_ctx=num_ctx, think=think, fmt=fmt, **PROFILE.options(think)):
                    if isinstance(piece, str):
                        out += piece
                    elif "thinking" in piece:
                        thinking = piece["thinking"].strip()
                    elif "stop_reason" in piece:
                        usage = piece
                if not prompt_tokens_recorded:
                    self.store.update_tokens(user_message_id, usage.get("prompt_tokens"))
                    prompt_tokens_recorded = True
                self.logger.model_call(
                    turn_id=turn_id, duration_ms=(time.monotonic() - start) * 1000,
                    prompt_tokens=usage.get("prompt_tokens", 0), completion_tokens=usage.get("completion_tokens", 0),
                    stop_reason=usage.get("stop_reason"),
                cache_read_tokens=usage.get("cache_read_tokens", 0), cache_write_tokens=usage.get("cache_write_tokens", 0), tool_calls_requested=[], output_text=out,
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

        try:
            for step in steps:
                name, kind = step.get("step"), step.get("think")
                if _cancelled(cancel):
                    self._end_cancelled(turn_id, reply)
                    return
                if stopped and kind != "answer":
                    continue
                if "tool" in step:
                    tool = step["tool"]
                    repaired_args = None
                    for attempt in (1, 2, 3):
                        try:
                            args = repaired_args if repaired_args is not None else executor.resolve(step.get("with") or {}, values)
                        except KeyError as missing:
                            stopped = f"{name} needs the output of {missing.args[0]}, which has none"
                            break
                        yield self._show(tool, args)
                        if enforcing and enforcing.on("step_cap") and tool_attempts >= enforcing.step_cap:
                            stopped = f"the {enforcing.step_cap}-step limit was reached"
                            self._shown_call = None  # shown, but it won't run
                            break
                        tool_attempts += 1
                        outcome = self._call_tool(turn_id, tool, args, None, enforcing)
                        if outcome.must_stop:
                            yield from self._abort_turn(turn_id, outcome.user_message(tool))
                            return
                        content, ok = outcome.text, outcome.success
                        failed = None if ok else outcome.hint or content
                        source = list(dict.fromkeys(executor.refs_in(step.get("with", {}))))
                        can_repair = not (enforcing and enforcing.on("step_cap") and tool_attempts >= enforcing.step_cap)
                        if failed and can_repair and outcome.repairable and attempt < 3 and len(source) == 1 and by_name.get(source[0], {}).get("think") == "draft":
                            # At most two corrections, counted against the step cap.
                            values[source[0]] = yield from draft(by_name[source[0]], f"{json.dumps(args)[:300]}: {failed}")
                            continue
                        if failed and can_repair and outcome.repairable and attempt < 3 and tool in self._tool_validators:
                            out = yield from call(executor.repair_messages(text, tool, args, failed, now),
                                                  self._tool_validators[tool].schema)
                            repaired_args = out  # The dispatch boundary validates and logs the correction.
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
                    if self._turn_writes and stopped:
                        reply += self._partial_actions()
                    yield Text(reply)
        except (ContextBudgetExceeded, requests.exceptions.RequestException) as e:
            reply = str(e) + " The plan stopped." + self._partial_actions()
            yield Text(reply)
        self.store.add_message(self.conversation_id, "nuru", reply)
        if self.shadow:
            self.shadow.end(reply)
        self.logger.turn_end(turn_id)

    def _stop(self, turn_id: str, reason: str):
        """End the turn plainly, saying why and what had happened by then."""
        text = STOPPED.format(reason=reason, log=log_lines(self.shadow.executed if self.shadow else []))
        yield Text(text)
        self.store.add_message(self.conversation_id, "nuru", text)
        self.logger.turn_end(turn_id)
        if self.shadow:
            self.shadow.finish()

    def _end_cancelled(self, turn_id: str, output_text: str) -> None:
        """Ends a turn the user stopped. What the model had said so far is kept as its reply so the
        history stays user/nuru alternating and the model knows what it said. No usage figures
        arrive for a cut-short call, so the messages keep a NULL tokens_used."""
        self.store.add_message(self.conversation_id, "nuru", output_text + self._partial_actions())
        self.logger.turn_end(turn_id)

    def shutdown(self, reason: str) -> None:
        """Unload every model and stop the Ollama server if we started it, and log why the session ended.
        With Claude as the main model Ollama is left as it is: it serves only the embeddings, and may be
        running other models."""
        if not CLAUDE:
            unload_all_models()
            stop_server()
        self.logger.session_end(reason=reason, models_unloaded=True, server_stopped=True)

    def close(self) -> None:
        """Stop the indexer and release the MCP servers and the database. Leaves the model loaded and
        Ollama running."""
        if self.indexer:
            self.indexer.stop()
        if self.link_auditor:
            self.link_auditor.stop()
        if self.steward:
            self.steward.stop()
        self.tools.close()
        self.store.close()
        if self.shadow:
            self.shadow.close()
