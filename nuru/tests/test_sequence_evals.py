"""Runner and scoring contracts, without launching Ollama/MCP servers."""
import importlib
import json
from pathlib import Path

import pytest

EVALS = Path(__file__).resolve().parents[1] / "evals" / "decision_layer"


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(EVALS))
    return [importlib.import_module(name) for name in ("cases", "run_suite", "score_runs", "drive_turn")]


def test_additional_sequence_discovery_and_scoring(modules):
    cases, runner, scorer, _ = modules
    loaded = cases.load_cases(EVALS / "cases.json")
    assert 35 in loaded and 35 not in runner.suite_prompts()
    replies = ["Juniper, 35 days", "5 weeks", "Juniper"]
    turns = [{**turn, "reply": reply, "vault_changes": []} for turn, reply in zip(loaded[35]["turns"], replies)]
    assert all(scorer.score_sequence_turn(t)[0] == 5 for t in turns)
    assert scorer.score({"turns": turns, "vault_changes": []})[0] == 5
    turns[2]["reply"] = "I did not find a note mentioning Juniper."
    assert not dict(scorer.score_sequence_turn(turns[2])[1])["remembers earlier subject"]
    turns[1]["reply"] = "What duration?"
    assert not dict(scorer.score_sequence_turn(turns[1])[1])["converted earlier duration"]


def test_runner_passes_sequence_file_to_one_driver_process(modules, tmp_path, monkeypatch):
    _, runner, _, _ = modules
    seen = []
    class Process:
        returncode = 0
        def __init__(self, command, **kwargs):
            seen.append(command)
            case = json.loads(Path(command[command.index("--case") + 1]).read_text())
            assert len(case["turns"]) == 2
            (tmp_path / "turn.json").write_text('{"turns": [{}, {}]}')
        def wait(self, **kwargs):
            return 0
    monkeypatch.setattr(runner.subprocess, "Popen", Process)
    record = runner.run_turn(tmp_path, {"NURU_LOG_DIR": str(tmp_path)}, "first", "off", 10,
                             case={"turns": [{"prompt": "first"}, {"prompt": "second"}]})
    assert len(seen) == 1
    assert "--prompt" not in seen[0]
    assert len(record["turns"]) == 2


def test_sequence_logs_are_attributed_by_turn(modules, tmp_path):
    _, _, scorer, _ = modules
    (tmp_path / "logs").mkdir()
    events = [{"event": "tool_call", "turn_id": f"t-{i}", "tool_name": name, "status": "success"}
              for i, name in enumerate(["search", "calculate"], 1)]
    (tmp_path / "logs" / "now_session.jsonl").write_text("\n".join(json.dumps(e) for e in events))
    assert scorer._executed(tmp_path, "session", "t-2") == [("calculate", "success")]


def test_sequence_snapshot_catches_created_changed_and_deleted_files(modules, tmp_path):
    cases, *_ = modules
    (tmp_path / "old.md").write_text("before")
    before = cases.vault_snapshot(tmp_path)
    (tmp_path / "old.md").unlink()
    (tmp_path / "new.md").write_text("after")
    assert [c["path"] for c in cases.snapshot_changes(before, cases.vault_snapshot(tmp_path))] == ["new.md", "old.md"]


@pytest.mark.parametrize("planned", [False, True])
def test_driver_keeps_one_session_for_sequence_and_records_numeric_failures(modules, monkeypatch, tmp_path, planned):
    from types import SimpleNamespace
    _, _, _, driver = modules
    prompts, starts = [], []
    class FakeSession:
        shadow = SimpleNamespace(failures=0)
        logger = SimpleNamespace(session_id="test")
        tools = SimpleNamespace(call_harness_tool=lambda *a: SimpleNamespace(text='{"laya":{"state":"ready"}}'))
        def continuation_context(self):
            return "previous answer" if prompts else ""
        def ask(self, prompt, cancel=None):
            prompts.append(prompt)
            yield driver.Text("answer")
        def run_plan(self, prompt, plan, cancel=None):
            assert plan == {"steps": []}
            yield from self.ask(prompt)
        def close(self):
            pass
    def start():
        starts.append(True)
        return FakeSession()
    monkeypatch.setattr(driver.session.Session, "start", start)
    for key in ("VAULT_PATH", "OBSIDIAN_SEMANTIC_DB", "NURU_DECISION_CONFIG", "NURU_LOG_DIR"):
        monkeypatch.setenv(key, str(tmp_path))
    monkeypatch.setenv("OBSIDIAN_EMBED_MODEL", "nomic-embed-text-v2-moe")
    case = tmp_path / "case.json"
    fixture = {"turns": [{"prompt": p, "checks": {"answered": "answer"}} for p in ("first", "follow up")]}
    if planned:
        fixture["plan"] = {"steps": []}
    case.write_text(json.dumps(fixture))
    out = tmp_path / "out.json"
    monkeypatch.setattr("sys.argv", ["drive_turn", "--case", str(case), "--think", "off", "--out", str(out)])
    driver.main()
    assert starts == [True] and prompts == ["first", "follow up"]
    assert driver.session.OBSIDIAN_SERVER_PARAMS.env["OBSIDIAN_EMBED_MODEL"] == "nomic-embed-text-v2-moe"
    records = json.loads(out.read_text())["turns"]
    assert [r["shadow_failures"] for r in records] == [0, 0]


def test_driver_cancels_a_turn_at_its_deadline_and_does_not_send_later_turns(modules, monkeypatch, tmp_path):
    import time
    from types import SimpleNamespace
    _, _, _, driver = modules
    prompts = []
    class FakeSession:
        shadow = SimpleNamespace(failures=0)
        logger = SimpleNamespace(session_id="test")
        tools = SimpleNamespace(call_harness_tool=lambda *a: SimpleNamespace(text='{"laya":{"state":"ready"}}'))
        def continuation_context(self):
            return ""
        def ask(self, prompt, cancel=None):
            prompts.append(prompt)
            while prompt == "slow" and not cancel.is_set():  # Session.ask stops at the next chunk
                time.sleep(0.01)
                yield driver.Text(".")
            yield driver.Text("answer")
        def close(self):
            pass
    monkeypatch.setattr(driver.session.Session, "start", FakeSession)
    for key in ("VAULT_PATH", "OBSIDIAN_SEMANTIC_DB", "NURU_DECISION_CONFIG", "NURU_LOG_DIR"):
        monkeypatch.setenv(key, str(tmp_path))
    case = tmp_path / "case.json"
    case.write_text(json.dumps({"turns": [{"prompt": p, "checks": {}} for p in ("first", "slow", "third")]}))
    out = tmp_path / "out.json"
    monkeypatch.setattr("sys.argv", ["drive_turn", "--case", str(case), "--think", "off", "--out", str(out),
                                     "--turn-timeout", "0.2"])
    driver.main()
    output = json.loads(out.read_text())
    first, slow, third = output["turns"]
    assert prompts == ["first", "slow"]
    assert not first["timed_out"] and first["failure"] is None
    assert slow["timed_out"] and "timed out after 0.2 s" in slow["failure"]
    assert third == {"prompt": "third", "not_run": True, "checks": {}}
    assert output["turns_not_run"] == 1 and output["failure"] == slow["failure"]


@pytest.mark.parametrize("case,flag,deadline", [
    (None, False, 10),
    ({"turns": [{"prompt": p, "checks": {}} for p in ("a", "b", "c")]}, True, 10 * 3 + 300),
])
def test_runner_gives_sequences_a_deadline_per_turn(modules, tmp_path, monkeypatch, case, flag, deadline):
    _, runner, _, _ = modules
    seen = {}
    class Process:
        returncode = 0
        def __init__(self, command, **kwargs):
            seen["command"] = command
            (tmp_path / "turn.json").write_text("{}")
        def wait(self, timeout=None):
            seen["timeout"] = timeout
            return 0
    monkeypatch.setattr(runner.subprocess, "Popen", Process)
    runner.run_turn(tmp_path, {"NURU_LOG_DIR": str(tmp_path)}, "a", "off", 10, case=case)
    command = seen["command"]
    assert ("--turn-timeout" in command) is flag
    if flag:
        assert command[command.index("--turn-timeout") + 1] == "10"
    assert seen["timeout"] == deadline


@pytest.mark.parametrize("reply,passed", [
    ('The codeword you gave me is "Juniper."', True),
    ('The codeword was **Juniper**.', True),
    ('"Juniper."', True),
    ('Codeword: Juniper', True),
    ('I did not find any note mentioning Juniper.', False),
])
def test_memory_rubric_checks_affirmation_and_allows_formatting(modules, reply, passed):
    cases, _, scorer, _ = modules
    fixture = cases.load_cases(EVALS / "cases.json")[35]["turns"][2]
    _, checks = scorer.score_sequence_turn({**fixture, "reply": reply})
    assert dict(checks)["remembers earlier subject"] is passed


def test_explicit_rescoring_keeps_raw_results_and_requires_same_prompts(modules, tmp_path):
    cases, _, scorer, _ = modules
    fixture = cases.load_cases(EVALS / "cases.json")[35]
    turns = [{**t, "checks": {"old broken rubric": "impossible"}, "reply": reply}
             for t, reply in zip(fixture["turns"], ['Juniper 35', '5 weeks', '"Juniper."'])]
    raw = json.dumps({"test": 35, "turns": turns})
    path = tmp_path / "results.jsonl"
    path.write_text(raw)
    result = scorer.load_results(tmp_path, cases_path=EVALS / "cases.json")[0]
    assert result["acceptance_passed"]
    assert result["scoring_cases_sha256"]
    assert path.read_text() == raw
    turns[0]["prompt"] = "different"
    path.write_text(json.dumps({"test": 35, "turns": turns}))
    with pytest.raises(ValueError, match="preserve"):
        scorer.load_results(tmp_path, cases_path=EVALS / "cases.json")


def test_suite_excludes_shared_v2_index_and_sidecars(modules, tmp_path, monkeypatch):
    cases, runner, *_ = modules
    source = tmp_path / "fixture"
    source.mkdir()
    (source / "note.md").write_text("keep")
    for name in (".obsidian-mcp-semantic-v2.sqlite3", ".obsidian-mcp-semantic-v2.sqlite3-wal"):
        (source / name).write_bytes(b"runtime")
    monkeypatch.setattr(runner, "DUMMY_VAULT", source)
    scratch = tmp_path / "scratch"
    runner.copy_vault(scratch)
    assert [p.name for p in scratch.iterdir()] == ["note.md"]
    assert list(cases.vault_snapshot(source)) == ["note.md"]
    assert runner.vault_changes(scratch) == []


def test_plain_suite_retains_34_prompt_baseline(modules, monkeypatch):
    _, runner, _, _ = modules
    selected = []
    parse_tests = runner.parse_tests
    def stop_after_selection(value):
        selected.extend(parse_tests(value))
        raise RuntimeError("selection captured before model setup")
    monkeypatch.setattr(runner, "parse_tests", stop_after_selection)
    monkeypatch.setattr("sys.argv", ["run_suite"])
    with pytest.raises(RuntimeError, match="selection captured"):
        runner.main()
    assert selected == list(range(1, 35))


def test_suite_uses_distinct_journals_for_fresh_case_vaults(modules, tmp_path, monkeypatch):
    _, runner, _, _ = modules
    source = tmp_path / 'fixture'
    source.mkdir()
    (source / 'note.md').write_text('fixture')
    monkeypatch.setattr(runner, 'DUMMY_VAULT', source)
    monkeypatch.setattr(runner, 'RUNS', tmp_path / 'runs')
    monkeypatch.setattr(runner.manifest, 'build', lambda *args: {})
    def index(command, *, env, **kwargs):
        Path(env['OBSIDIAN_SEMANTIC_DB']).write_bytes(b'scratch index')
    monkeypatch.setattr(runner.subprocess, 'run', index)
    histories = []
    def turn(batch, env, *args):
        histories.append(env['NURU_DB_PATH'])
        return {'reply': 'done', 'tool_calls': [], 'turn_seconds': 0}
    monkeypatch.setattr(runner, 'run_turn', turn)
    monkeypatch.setattr('sys.argv', ['run_suite', '--tests', '28,29', '--repeats', '2'])
    runner.main()
    assert len(histories) == len(set(histories)) == 4


@pytest.mark.parametrize("path,body,passed", [
    ("nuru/daily-2026-10-11.md", "# Daily\n\n- [ ] Confirm the appointment with Giulia\n- [ ] Prepare the forms", True),
    ("nuru/daily-2026-10-11.md", "# Daily\n\n* [ ] confirm with Giulia\n1. [ ] fill in the forms", True),
    ("nuru/daily-2026-10-11.md", "# Daily\n\n[ ] Confirm the appointment with Giulia\n[ ] Prepare the forms", False),
    ("nuru/daily-2026-10-11.md", "# Daily\n\n- Confirm the appointment with Giulia\n- Prepare the forms", False),
    ("nuru/appointments.md", "- [ ] Confirm the appointment with Giulia\n- [ ] Prepare the forms", False),
])
def test_task_case_wants_checkbox_list_items_in_todays_daily(modules, path, body, passed):
    cases, _, scorer, _ = modules
    fixture = cases.load_cases(EVALS / "cases.json")[37]
    changes = [{"path": path, "before": None, "after": body}]
    turn = {**fixture["turns"][0], "reply": "Added both tasks to today's daily note.", "vault_changes": changes}
    checks = dict(scorer.score_sequence_turn(turn)[1])
    assert "no vault changes" not in checks
    assert checks["confirm task is a checkbox in today's daily"] is passed
    assert checks["forms task is a checkbox in today's daily"] is passed
    points, case_checks = scorer.score({"turns": [turn], "vault_changes": changes})
    assert "case changed no vault files" not in dict(case_checks)
    assert (points == 5) is passed


def test_a_case_that_writes_passes_acceptance_with_its_changes(modules, tmp_path):
    cases, _, scorer, _ = modules
    fixture = cases.load_cases(EVALS / "cases.json")[37]
    changes = [{"path": "nuru/daily-2026-10-11.md", "before": None, "after": "- [ ] confirm\n- [ ] forms"}]
    turn = {**fixture["turns"][0], "reply": "Done.", "vault_changes": changes}
    (tmp_path / "results.jsonl").write_text(json.dumps({"test": 37, "turns": [turn], "vault_changes": changes}))
    assert scorer.load_results(tmp_path, cases_path=EVALS / "cases.json")[0]["acceptance_passed"]


def test_writes_checks_must_be_regexes(modules, tmp_path):
    cases, _, _, _ = modules
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"90": {"name": "x", "turns": [{"prompt": "p", "checks": {}, "writes": {"bad": "("}}]}}))
    with pytest.raises(Exception):
        cases.load_cases(path)
    path.write_text(json.dumps({"90": {"name": "x", "turns": [{"prompt": "p", "checks": {}, "writes": {}}]}}))
    with pytest.raises(ValueError, match="writes"):
        cases.load_cases(path)
