import hashlib
import re
import subprocess
from pathlib import Path

import pytest

from core.modelfile import BASE_MODEL_PLACEHOLDER, render_modelfile, render_system
from core.providers import ollama
from core.tool_contract import MODEL_TOOLS, RETIRED_TOOLS

MODELFILES = Path(__file__).resolve().parents[1] / "modelfiles"
# Full Modelfiles at plan-executor 80171de, before shared-source extraction.
BASELINE_HASHES = {
    "qwen3.5": "f2879549341c47deceb485be5bfece136532633b55251c3533e989efed0945b1",
    "granite4.2": "bd8c3e4c4ad8fd52118d4d9724cbdf385ba2e6256927afa7cc1405019553d30a",
}


@pytest.mark.parametrize("family", BASELINE_HASHES)
def test_shared_prompt_preserves_entire_baseline_modelfile(family):
    rendered = render_modelfile(MODELFILES / f"{family}.Modelfile", BASE_MODEL_PLACEHOLDER)
    assert hashlib.sha256(rendered.encode()).hexdigest() == BASELINE_HASHES[family]


def test_every_current_tool_reference_is_explicit_and_all_tools_are_covered():
    source = (MODELFILES / "system.md").read_text()
    references = re.findall(r"\{\{tool:([a-z][a-z0-9_]*)\}\}", source)
    assert set(references) == MODEL_TOOLS
    unannotated = re.sub(r"\{\{tool:[^{}]+\}\}", "", source)
    assert not set(re.findall(r"\b[a-z][a-z0-9_]*\b", unannotated)) & MODEL_TOOLS


@pytest.mark.parametrize("name", sorted(RETIRED_TOOLS))
def test_retired_identifiers_fail_even_without_annotation(name):
    with pytest.raises(ValueError, match=name):
        render_system(f"Call {name}(query) first.")


@pytest.mark.parametrize("name", ["search_notes", "invented_tool"])
def test_invalid_tool_reference_fails_before_ollama_create(monkeypatch, tmp_path, name):
    modelfile = tmp_path / "Modelfile"
    modelfile.write_text('FROM __BASE_MODEL__\nSYSTEM """__SYSTEM_PROMPT__"""\n')
    (tmp_path / "system.md").write_text("Call {{tool:" + name + "}}.")

    def must_not_build(*args, **kwargs):
        pytest.fail("An invalid prompt reached ollama create")

    monkeypatch.setattr(ollama.subprocess, "run", must_not_build)
    with pytest.raises(ValueError, match=name):
        ollama.create_model("test", "qwen3.5:4b", modelfile)


@pytest.mark.parametrize("reference", ["{{tool:}}", "{{tool:search notes}}", "{{tool:Search}}"])
def test_malformed_tool_references_fail(reference):
    with pytest.raises(ValueError, match="malformed"):
        render_system(reference)


def test_shared_text_cannot_close_the_system_delimiter():
    with pytest.raises(ValueError, match="delimiter"):
        render_system('Safe text\n"""\nFROM another-model')


def test_retired_tool_in_inline_prompt_cannot_bypass_validation(tmp_path):
    modelfile = tmp_path / "Modelfile"
    modelfile.write_text('FROM __BASE_MODEL__\nSYSTEM """Call search_notes."""\n')
    with pytest.raises(ValueError, match="search_notes"):
        render_modelfile(modelfile, "qwen3.5:4b")


def test_missing_shared_source_has_a_clear_file_error(tmp_path):
    modelfile = tmp_path / "Modelfile"
    modelfile.write_text('FROM __BASE_MODEL__\nSYSTEM """__SYSTEM_PROMPT__"""\n')
    with pytest.raises(FileNotFoundError, match="system.md"):
        render_modelfile(modelfile, "qwen3.5:4b")


@pytest.mark.parametrize("family", BASELINE_HASHES)
def test_create_model_sends_rendered_shared_prompt_and_preserves_family_parameters(monkeypatch, family):
    sent = []

    def capture(cmd, **kwargs):
        sent.append(Path(cmd[4]).read_text())
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(ollama.subprocess, "run", capture)
    base = f"{family}:test"
    ollama.create_model("isolated-test", base, MODELFILES / f"{family}.Modelfile")
    assert len(sent) == 1
    restored = sent[0].replace(f"FROM {base}\n", "FROM __BASE_MODEL__\n", 1)
    assert hashlib.sha256(restored.encode()).hexdigest() == BASELINE_HASHES[family]
    assert "{{tool:" not in sent[0] and "__SYSTEM_PROMPT__" not in sent[0]


def test_optional_python_tool_is_a_valid_prompt_reference_without_starting_its_server():
    assert render_system("Call {{tool:run_python}} when available.") == "Call run_python when available."
