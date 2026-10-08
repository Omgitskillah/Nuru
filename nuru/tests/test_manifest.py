import hashlib
import importlib.util
from pathlib import Path

import pytest

from core import session


@pytest.mark.parametrize("file_model,override,expected", [
    (None, None, "nomic-embed-text:v1.5"),
    ("nomic-embed-text-v2-moe", None, "nomic-embed-text-v2-moe"),
    ("nomic-embed-text-v2-moe", "nomic-embed-text:v1.5", "nomic-embed-text:v1.5"),
])
def test_manifest_tracks_shared_prompt_and_effective_embedding_model(monkeypatch, tmp_path, file_model, override, expected):
    evals = Path(__file__).resolve().parents[1] / "evals" / "decision_layer"
    monkeypatch.syspath_prepend(str(evals))
    spec = importlib.util.spec_from_file_location("prompt_manifest_test", evals / "manifest.py")
    manifest = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(manifest)
    modelfile = tmp_path / "qwen3.5.Modelfile"
    modelfile.write_text('FROM __BASE_MODEL__\nSYSTEM """__SYSTEM_PROMPT__"""\n')
    source = tmp_path / "system.md"
    source.write_text("Call {{tool:calculate}}.")
    config = tmp_path / "config.yaml"
    config.write_text("decision_points: {}")
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr(manifest, "REPO", tmp_path)
    monkeypatch.setattr(manifest, "DUMMY_VAULT", vault)
    monkeypatch.setattr(manifest, "_git", lambda *args: "")
    monkeypatch.setattr(manifest, "_ollama_model", lambda name: {})
    monkeypatch.setattr(session, "_modelfile_for", lambda name: modelfile)
    monkeypatch.delenv("OBSIDIAN_EMBED_MODEL", raising=False)
    if file_model:
        (tmp_path / ".env.obsidian").write_text(f"OBSIDIAN_EMBED_MODEL={file_model}\n")
    if override:
        monkeypatch.setenv("OBSIDIAN_EMBED_MODEL", override)
    built = manifest.build({}, config)
    assert built["embedding_model"] == expected
    first = built["model"]
    source.write_text("Call {{tool:calculate}} once.")
    second = manifest.build({}, config)["model"]
    assert first["modelfile_sha256"] == second["modelfile_sha256"]
    assert first["rendered_modelfile_sha256"] != second["rendered_modelfile_sha256"]
    rendered = manifest.render_modelfile(modelfile, session.BASE_MODEL)
    assert second["rendered_modelfile_sha256"] == hashlib.sha256(rendered.encode()).hexdigest()


def test_decision_config_hash_ignores_the_batch_database_path(monkeypatch, tmp_path):
    evals = Path(__file__).resolve().parents[1] / "evals" / "decision_layer"
    monkeypatch.syspath_prepend(str(evals))
    spec = importlib.util.spec_from_file_location("config_hash_test", evals / "manifest.py")
    manifest = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(manifest)
    a, b, c = tmp_path / "a.yaml", tmp_path / "b.yaml", tmp_path / "c.yaml"
    a.write_text("database: /runs/batch-1/decisions.sqlite3\nexpert: {at_step_cap: false}\n")
    b.write_text("expert: {at_step_cap: false}\ndatabase: /runs/batch-2/decisions.sqlite3\n")
    c.write_text("database: /runs/batch-1/decisions.sqlite3\nexpert: {at_step_cap: true}\n")

    assert manifest._decision_config_sha256(a) == manifest._decision_config_sha256(b)
    assert manifest._decision_config_sha256(a) != manifest._decision_config_sha256(c)
