"""Versioned question sets, one YAML file per decision point (questions/*.yaml).

Each file carries the `POST /v1/systemone` question shape plus metadata: which checkpoint
answers it and how many state tokens it may take. Options that only exist at request time (the
live tool list, a caller's subject labels) are named with `criteria_from` and filled in by
`render()`. Everything is validated when the server starts, so a typo in a question fails there.
"""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

QUESTIONS_DIR = Path(__file__).parent / "questions"

# Context length per checkpoint, from each one's rl_agent_config.json (revision 55cf4c4e).
CHECKPOINT_MAX_LEN = {"english": 512, "multilingual": 1024, "typed-decisions": 1024}
# The multilingual checkpoint reads up to this many tokens when asked (`max_len=`); measured working.
LONG_MAX_LEN = 8192


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["choice", "score", "noul"]
    instructions: str = Field(min_length=1)
    criteria: dict[str, str] | list[str] | None = None
    # Name of the request-time option set that replaces `criteria` (choice questions only).
    criteria_from: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "Question":
        if self.type == "choice":
            if (self.criteria is None) == (self.criteria_from is None):
                raise ValueError("a choice question takes exactly one of criteria / criteria_from")
            if self.criteria is not None and not self.criteria:
                raise ValueError("a choice question needs at least one option")
        elif self.criteria_from is not None:
            raise ValueError("criteria_from is only for choice questions")
        if self.type == "score" and not (isinstance(self.criteria, list) and self.criteria):
            raise ValueError("a score question takes criteria as a non-empty list of levels, lowest first")
        if self.type == "noul" and self.criteria is not None:
            if not isinstance(self.criteria, dict) or not set(self.criteria) <= {"true", "false"}:
                raise ValueError("a noul question's criteria are keyed only 'true'/'false'")
        return self


class QuestionSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    version: int = Field(ge=1)
    checkpoint: Literal["english", "multilingual", "typed-decisions"]
    max_state_tokens: int = Field(ge=1)
    max_len: int | None = Field(None, le=LONG_MAX_LEN)
    head_max_len: int | None = None
    questions: dict[str, Question]

    @model_validator(mode="after")
    def _check(self) -> "QuestionSet":
        if not self.questions:
            raise ValueError("a question set needs at least one question")
        if self.max_len is not None and self.checkpoint != "multilingual":
            raise ValueError("only the multilingual checkpoint reads past its trained context length")
        if self.max_state_tokens >= self.context_length:
            raise ValueError(
                f"max_state_tokens ({self.max_state_tokens}) leaves no room for the questions in a "
                f"{self.context_length}-token context"
            )
        return self

    @property
    def context_length(self) -> int:
        return self.max_len or CHECKPOINT_MAX_LEN[self.checkpoint]

    @property
    def injected(self) -> set[str]:
        return {q.criteria_from for q in self.questions.values() if q.criteria_from}

    def render(self, options: dict[str, dict[str, str] | list[str]] | None = None) -> dict:
        """The questions in `/v1/systemone` shape, with request-time options filled in."""
        options = options or {}
        missing = self.injected - set(options)
        if missing:
            raise ValueError(f"{self.name} needs request-time options for {sorted(missing)}")
        rendered = {}
        for qid, q in self.questions.items():
            body: dict = {"type": q.type, "instructions": q.instructions}
            criteria = options[q.criteria_from] if q.criteria_from else q.criteria
            if not criteria and q.type == "choice":
                raise ValueError(f"{self.name}.{qid}: no options to choose from")
            if criteria is not None:
                body["criteria"] = dict(criteria) if isinstance(criteria, dict) else list(criteria)
            rendered[qid] = body
        return rendered


def load_question_sets(directory: Path = QUESTIONS_DIR) -> dict[str, QuestionSet]:
    """Every question set in `directory`, keyed by name. Raises on the first invalid file."""
    sets = {}
    for path in sorted(directory.glob("*.yaml")):
        with open(path) as f:
            try:
                qset = QuestionSet.model_validate(yaml.safe_load(f))
            except ValueError as e:
                raise ValueError(f"{path.name}: {e}") from e
        if qset.name != path.stem:
            raise ValueError(f"{path.name}: name {qset.name!r} does not match the file name")
        sets[qset.name] = qset
    return sets
