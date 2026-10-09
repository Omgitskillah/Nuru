from core.eval_artifacts import read_json, write_json


def test_compressed_artifacts_are_deterministic_and_replayable(tmp_path):
    path = tmp_path / 'evidence.json.gz'
    evidence = {'summary': {'passes': 1}, 'runs': [{'reply': 'caf\u00e9', 'failed': True}]}
    write_json(path, evidence)
    first = path.read_bytes()
    write_json(path, evidence)
    assert path.read_bytes() == first
    assert read_json(path) == evidence
    plain = tmp_path / 'evidence.json'
    write_json(plain, evidence)
    assert read_json(plain) == evidence
