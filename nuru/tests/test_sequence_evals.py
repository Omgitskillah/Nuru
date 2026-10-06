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
        def ask(self, prompt):
            prompts.append(prompt)
            yield driver.Text("answer")
        def run_plan(self, prompt, plan):
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
