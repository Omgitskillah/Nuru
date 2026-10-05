import hashlib
import importlib.util
from pathlib import Path

from core import session


def test_manifest_hash_tracks_shared_prompt_content_without_changing_family_header(monkeypatch, tmp_path):
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
    first = manifest.build({}, config)["model"]
    source.write_text("Call {{tool:calculate}} once.")
    second = manifest.build({}, config)["model"]
    assert first["modelfile_sha256"] == second["modelfile_sha256"]
    assert first["rendered_modelfile_sha256"] != second["rendered_modelfile_sha256"]
    rendered = manifest.render_modelfile(modelfile, session.BASE_MODEL)
    assert second["rendered_modelfile_sha256"] == hashlib.sha256(rendered.encode()).hexdigest()
