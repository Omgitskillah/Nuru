"""A fake predictor, so no test needs Laya's weights, and fixtures for a layer built around it.
Imported by conftest.py; its own module so the name can't clash with other packages' conftest."""

import json

import pytest

from laya_mcp.config import load_config
from laya_mcp.decision_log import DecisionLog
from laya_mcp.decisions import DecisionLayer
from laya_mcp.predictor import OverBudget, Prediction, Unavailable
from laya_mcp.question_sets import load_question_sets


class FakePredictor:
    """Answers from a script: {question id: {option: p}} for choice/score, {question id: p} for
    noul. An unscripted choice answers its first option with p=1; an unscripted noul p=0.5.
    Tokens are counted as characters / 3, against the set's max_state_tokens."""

    def __init__(self, script=None, available=True):
        self.script = script or {}
        self.available = available
        self.calls = []

    def status(self):
        return {"state": "ready" if self.available else "loading"}

    def predict(self, qset, state, questions):
        if not self.available:
            raise Unavailable("Laya is loading")
        tokens = len(json.dumps(state)) // 3
        if tokens > qset.max_state_tokens:
            raise OverBudget(tokens, qset.max_state_tokens)
        self.calls.append((qset.name, state, questions))
        script = self.script(state) if callable(self.script) else self.script
        answers = {}
        for qid, q in questions.items():
            scripted = script.get(qid)
            if q["type"] == "noul":
                answers[qid] = {"type": "noul", "noul": 0.5 if scripted is None else scripted}
                continue
            options = list(q["criteria"])
            probabilities = scripted or {options[0]: 1.0}
            probabilities = {o: probabilities.get(o, 0.0) for o in options}
            best = max(probabilities, key=probabilities.get)
            answers[qid] = {"type": q["type"], "choice": best, "probabilities": probabilities}
        return Prediction(answers, qset.checkpoint, tokens, 1.0)


@pytest.fixture
def log(tmp_path):
    log = DecisionLog(tmp_path / "decisions.sqlite3")
    yield log
    log.close()


@pytest.fixture
def make_layer(log):
    def make(script=None, available=True):
        predictor = FakePredictor(script, available)
        return DecisionLayer(load_config(), load_question_sets(), predictor, log), predictor

    return make


def rows(log, **where):
    query = "SELECT * FROM decisions" + (" WHERE " + " AND ".join(f"{k} = ?" for k in where) if where else "")
    return log._conn.execute(query + " ORDER BY id", list(where.values())).fetchall()
