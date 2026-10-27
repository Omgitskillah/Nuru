import hashlib
import re
import subprocess
from pathlib import Path

import pytest

from core.modelfile import BASE_MODEL_PLACEHOLDER, render_modelfile, render_system
from core.providers import ollama
from core.tool_contract import MODEL_TOOLS, RETIRED_TOOLS

MODELFILES = Path(__file__).resolve().parents[1] / "modelfiles"
# Full Modelfiles at plan-executor 80171de, before shared-source extraction, plus core rule 8 and the expert example (no expert) and Qwen num_batch 256 and IBM's Granite sampling (2026-10-07).
BASELINE_HASHES = {
    "qwen3.5": "7188e6caf5c405964f818136d481fbc72adb33f698fc0021bdd094240532b76f",
    "granite4.2": "e81b4ea62c881f705b848fe666bc44b3cb27fbcd667729d79f2497484e82fe93",
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


# --------------------------------------------------------------------------- a user's header (TODO F22)
from core.modelfile import (GUARD_NUM_PREDICT, HeaderError, check_header, default_header, parse_header,  # noqa: E402
                            render_header)

F = "FROM __BASE_MODEL__\n"


@pytest.mark.parametrize("family", BASELINE_HASHES)
def test_nuru_s_own_header_as_a_user_starts_from_it_renders_what_nuru_builds(family):
    """Nuru's files stay trusted (render_modelfile); the same header, without its SYSTEM placeholder,
    passes the strict check and gives the same Modelfile, directive for directive."""
    path = MODELFILES / f"{family}.Modelfile"
    assert "SYSTEM" not in default_header(path)
    ours = render_modelfile(path, "base:x")
    users = render_header(default_header(path), "base:x", MODELFILES / "system.md")
    assert sorted(parse_header(ours), key=str) != [] and \
        sorted((d, v) for d, v, *_ in parse_header(ours)) == sorted((d, v) for d, v, *_ in parse_header(users))


def test_directives_are_case_insensitive_and_a_template_may_run_over_lines():
    header = F + 'template """{{ .System }}\n# not a comment in here\n{{ .Prompt }}"""\nparameter stop "<|end|>"\n' \
                 'PARAMETER stop "#keep"\n  # a comment\n\nCAPABILITY tools\nREQUIRES 0.34.4\r\n'
    assert check_header(header, {"tools"}) == [
        ("TEMPLATE", "{{ .System }}\n# not a comment in here\n{{ .Prompt }}"), ("PARAMETER", 'stop "<|end|>"'),
        ("PARAMETER", 'stop "#keep"'), ("CAPABILITY", "tools"), ("REQUIRES", "0.34.4")]


@pytest.mark.parametrize("header, problem", [
    (F + 'SYSTEM """mine"""\n', "SYSTEM isn't allowed"),
    (F + "message user hi\n", "MESSAGE isn't allowed"),
    (F + "ADAPTER /etc/passwd\n", "ADAPTER isn't allowed"),
    (F + "LICENSE mine\n", "LICENSE isn't allowed"),
    (F + "WHATEVER x\n", "unknown directive"),
    (F + "-rf x\n", "isn't a directive"),
    (F + 'REQUIRES """0.1.0"""\n', 'only TEMPLATE takes a """ block'),
    (F + 'TEMPLATE """never closed\n', "never closed"),
    (F + 'TEMPLATE """a""" SYSTEM x\n', "nothing may follow"),
    (F + 'TEMPLATE """a\nb""" x\n', "nothing may follow"),
    (F + 'TEMPLATE "a"\nTEMPLATE "b"\n', "TEMPLATE is given twice"),
    ("PARAMETER temperature 1\n", "needs FROM"),
    (F + F, "FROM is given twice"),
    ("FROM llama3.1:8b\n", "FROM must be"),
    (F + "PARAMETER num_gpu 99\n", "unknown or unsupported parameter"),
    (F + "PARAMETER temperature 3\n", "temperature must be a number from 0 to 2"),
    (F + "PARAMETER temperature nan\n", "temperature must be a number"),
    (F + "PARAMETER temperature 1e3\n", "temperature must be a number"),
    (F + "PARAMETER top_k 2.5\n", "top_k must be a whole number"),
    (F + "PARAMETER top_k true\n", "top_k must be a whole number"),
    (F + f"PARAMETER num_predict {GUARD_NUM_PREDICT + 1}\n", "num_predict must be a whole number from 1 to 8192"),
    (F + "PARAMETER num_predict -1\n", "num_predict must be"),
    (F + "PARAMETER top_p 0.9\nPARAMETER top_p 0.8\n", "top_p is given twice"),
    (F + "PARAMETER stop end\n", "a stop sequence is"),
    (F + 'PARAMETER stop ""\n', "a stop sequence is"),
    (F + 'PARAMETER stop "a"b"\n', "a stop sequence is"),
    (F + "".join(f'PARAMETER stop "s{i}"\n' for i in range(9)), "at most 8 stop sequences"),
    (F + "CAPABILITY vision\n", "capability 'vision'"),
    (F + "CAPABILITY admin\n", "capability 'admin'"),
    (F + "CAPABILITY tools\nCAPABILITY tools\n", "given twice"),
    (F + "REQUIRES latest\n", "REQUIRES is an Ollama version"),
    (F + "REQUIRES 0.1.0\nREQUIRES 0.2.0\n", "REQUIRES is given twice"),
    (F + 'TEMPLATE "a""""\n', "TEMPLATE can't contain"),
    (F + 'TEMPLATE """"a"""\n', "TEMPLATE can't contain"),
    (F + "PARAMETER temperature 1\x1b[2J\n", "control character"),
    (F + "# " + "x" * (64 * 1024), "longer than 64 KiB"),
], ids=lambda value: value if isinstance(value, str) and len(value) < 40 and "\n" not in value else None)
def test_a_header_outside_the_allowlist_is_refused_naming_why(header, problem):
    with pytest.raises(ValueError, match=re.escape(problem) if "'" in problem else problem):
        check_header(header, {"tools", "completion"})


def test_a_refusal_names_its_line():
    with pytest.raises(HeaderError) as refused:
        check_header(F + "\n# fine\nMESSAGE user hi\n")
    assert refused.value.line == 4 and str(refused.value).startswith("line 4: MESSAGE")


def test_the_rendering_is_rebuilt_from_what_was_checked_with_the_guard_and_one_system():
    header = F + "# my notes\nPARAMETER temperature 0.5\nTEMPLATE \"{{ .Prompt }} SYSTEM fake\"\n"
    rendered = render_header(header, "llama3.1:8b", MODELFILES / "system.md")
    lines = rendered.splitlines()
    assert lines[:4] == ["FROM llama3.1:8b", "PARAMETER temperature 0.5", 'TEMPLATE """{{ .Prompt }} SYSTEM fake"""',
                         f"PARAMETER num_predict {GUARD_NUM_PREDICT}"]
    assert "# my notes" not in rendered and rendered.count('SYSTEM """') == 1
    assert rendered.endswith(f'SYSTEM """{render_system((MODELFILES / "system.md").read_text())}"""\n')
    kept = render_header(F + "PARAMETER num_predict 4096\n", "llama3.1:8b", MODELFILES / "system.md")
    assert "PARAMETER num_predict 4096" in kept and f"num_predict {GUARD_NUM_PREDICT}" not in kept


@pytest.mark.parametrize("base", ["", "two words", 'x"y', "x\nFROM y"])
def test_a_base_model_that_isn_t_one_name_is_refused(base):
    with pytest.raises(ValueError, match="not a model name"):
        render_header(F, base, MODELFILES / "system.md")


def test_a_header_must_be_text():
    with pytest.raises(ValueError, match="must be text"):
        check_header(b"FROM __BASE_MODEL__")
