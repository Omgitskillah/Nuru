"""The decision points: Laya supplies probabilities, this module applies the config's thresholds.

Each method returns what the policy would do plus the probabilities behind it, and logs one row
per Laya call (or per code-only verdict) with that proposed action. Whether the harness acts on it
is the harness's business: in shadow mode it only records it (`DecisionLog.set_action_taken`).

Fail safe, everywhere: if Laya is unavailable, the input is over budget, or Laya raises, the
method still answers - with the conservative default - and says `source: "fallback"`.
"""

from typing import Any


from obsidian_mcp.frontmatter_template import TYPE_DESCRIPTIONS

from laya_mcp import checks
from laya_mcp.config import DecisionConfig
from laya_mcp.decision_log import DecisionLog, canonical, sha256
from laya_mcp.predictor import OverBudget, Prediction, Predictor, Unavailable
from laya_mcp.question_sets import QuestionSet

Context = dict[str, Any]  # session_id, task_id, step - whatever the harness passes


class DecisionLayer:
    def __init__(
        self,
        config: DecisionConfig,
        question_sets: dict[str, QuestionSet],
        predictor: Predictor,
        log: DecisionLog,
    ):
        for qset, questions in config.thresholds.items():
            if qset not in question_sets:
                raise ValueError(f"thresholds name question set {qset!r}, which does not exist")
            unknown = set(questions) - set(question_sets[qset].questions)
            if unknown:
                raise ValueError(f"thresholds for {qset} name unknown questions {sorted(unknown)}")
        self.config = config
        self.sets = question_sets
        self.predictor = predictor
        self.log = log

    # ------------------------------------------------------------------ plumbing
    def _ask(self, qset_name: str, state: Any, options: dict | None = None):
        """(questions, prediction or None, status, detail). Never raises for a Laya failure."""
        qset = self.sets[qset_name]
        questions = qset.render(options)
        try:
            return questions, self.predictor.predict(qset, state, questions), "ok", None
        except Unavailable as e:
            return questions, None, "unavailable", str(e)
        except OverBudget as e:
            return questions, None, "over_budget", str(e)
        except Exception as e:  # a Laya bug must not take a turn down with it
            return questions, None, "error", f"{type(e).__name__}: {e}"

    def _record(
        self,
        point: str,
        qset_name: str,
        state: Any,
        questions: dict,
        prediction: Prediction | None,
        status: str,
        detail: str | None,
        action: str,
        context: Context | None,
        mode: str | None = None,
    ) -> int:
        return self.log.record_decision(
            decision_point=point,
            question_set=qset_name,
            question_set_version=self.sets[qset_name].version,
            state=state,
            questions=questions,
            status=status,
            detail=detail,
            # A point set to off isn't asked by Nuru; asked anyway (a test, the inspector), nothing acts on it.
            mode=mode or ("shadow" if self.config.mode(point) == "off" else self.config.mode(point)),
            context=context,
            checkpoint=prediction.checkpoint if prediction else None,
            answers=prediction.answers if prediction else None,
            proposed_action=action,
            latency_ms=prediction.latency_ms if prediction else None,
        )

    def _clears(self, qset: str, question: str, option: str, p: float) -> bool:
        threshold = self.config.threshold(qset, question, option)
        return threshold is not None and p >= threshold

    @staticmethod
    def _choice(prediction: Prediction, qid: str) -> tuple[str, float, dict[str, float]]:
        answer = prediction.answers[qid]
        return answer["choice"], answer["probabilities"][answer["choice"]], answer["probabilities"]

    @staticmethod
    def _noul(prediction: Prediction, qid: str) -> float:
        return prediction.answers[qid]["noul"]

    # ------------------------------------------------------------------ tool_gate
    def tool_gate_start(self, request: str, context: Context | None = None) -> dict[str, Any]:
        """The request's mode (answer/read/write/ask_user), before the model runs, and the first tool
        for it. Skipping tools is the expensive mistake, so `answer` needs its (high) threshold and
        the default is `read`. The first tool is not Laya's to pick: for a read or a write it is the
        configured search (`tool_gate.default_first_read`) - search first."""
        qset = "tool_gate_start"
        state = {"request": request}
        questions, prediction, status, detail = self._ask(qset, state)
        mode, probabilities = "read", {}
        if prediction:
            _, _, probabilities = self._choice(prediction, "mode")
            for option in ("answer", "write", "ask_user"):
                if self._clears(qset, "mode", option, probabilities[option]):
                    mode = option
                    break
        first_tool = self.config.tool_gate.default_first_read if mode in ("read", "write") else None
        decision_id = self._record("tool_gate", qset, state, questions, prediction, status, detail, mode, context)
        return {
            "decision_id": decision_id,
            "mode": mode,
            "first_tool": first_tool,
            "source": "laya" if prediction else "fallback",
            "status": status,
            "probabilities": probabilities,
        }

    def tool_gate_next(self, request: str, steps: list[dict], context: Context | None = None) -> dict[str, Any]:
        """After a tool result: `continue` (another tool - which one is the model's choice), `answer`
        or `ask_user` - or `defer` (the model decides) when Laya is unsure or down."""
        qset = "tool_gate_next"
        state = {"request": request, "steps": steps}
        questions, prediction, status, detail = self._ask(qset, state)
        action, probabilities = "defer", {}
        if prediction:
            choice, p, probabilities = self._choice(prediction, "next")
            if self._clears(qset, "next", choice, p):
                action = choice
        decision_id = self._record("tool_gate", qset, state, questions, prediction, status, detail, action, context)
        return {
            "decision_id": decision_id,
            "next": action,
            "source": "laya" if prediction else "fallback",
            "status": status,
            "probabilities": probabilities,
        }

    # ------------------------------------------------------------------ assess_effort
    def assess_effort(
        self,
        request: str,
        step_type: str,
        steps: list[dict],
        failures: dict[str, int],
        context: Context | None = None,
    ) -> dict[str, Any]:
        """The cheapest tier predicted to get this step right, if its probability clears that
        tier's threshold; the configured default otherwise. The within-task rules (only upward,
        failure signals, expert budget) belong to the harness, which knows the task's history."""
        if step_type not in ("arguments", "answer"):
            raise ValueError("step_type is 'arguments' or 'answer'")
        qset = "assess_effort"
        state = {"request": request, "step_type": step_type, "steps": steps, "failures": failures}
        questions, prediction, status, detail = self._ask(qset, state)
        tier, probabilities = self.config.tiers.default, {}
        if prediction:
            choice, p, probabilities = self._choice(prediction, "tier")
            if self._clears(qset, "tier", choice, p):
                tier = choice
        decision_id = self._record("assess_effort", qset, state, questions, prediction, status, detail, tier, context)
        return {
            "decision_id": decision_id,
            "tier": tier,
            "source": "laya" if prediction else "fallback",
            "status": status,
            "probabilities": probabilities,
        }

    # ------------------------------------------------------------------ gate_write
    def gate_write(
        self, request: str, tool: str, arguments: dict, known_ids: list[str], context: Context | None = None
    ) -> dict[str, Any]:
        """proceed / ask / block. Code checks first; Laya only for writes code has no objection to,
        and `proceed` only when both of its answers clear their thresholds."""
        qset = "gate_write"
        state = {"request": request, "tool": tool, "arguments": arguments}
        reasons = checks.check_write(tool, arguments, set(known_ids))
        if reasons:
            decision_id = self._record(
                "gate_write", qset, state, {}, None, "code_blocked", "; ".join(reasons), "block", context
            )
            return {"decision_id": decision_id, "verdict": "block", "reasons": reasons, "source": "code",
                    "status": "code_blocked", "probabilities": {}}
        questions, prediction, status, detail = self._ask(qset, state)
        verdict, probabilities = "ask", {}
        if prediction:
            matches = self._noul(prediction, "matches_request")
            _, _, verdict_p = self._choice(prediction, "verdict")
            probabilities = {"matches_request": matches, "verdict": verdict_p}
            if self._clears(qset, "matches_request", "true", matches) and self._clears(
                qset, "verdict", "proceed", verdict_p["proceed"]
            ):
                verdict = "proceed"
        decision_id = self._record("gate_write", qset, state, questions, prediction, status, detail, verdict, context)
        return {"decision_id": decision_id, "verdict": verdict, "reasons": [], "source": "laya" if prediction else "fallback",
                "status": status, "probabilities": probabilities}

    # ------------------------------------------------------------------ check_claim
    def check_claim(
        self, reply: str, log: list[dict], known_ids: list[str], context: Context | None = None
    ) -> dict[str, Any]:
        """Does the reply match what actually happened? `log` is the harness's own execution log
        (tool, ok, ...); `known_ids` every note ID a tool call or search result contained.

        Code first: a note ID nobody returned fails at once. Then Laya says what the reply claims -
        a change made, a failure reported, or no change - and code compares that with the log: a
        claimed change with no successful write behind it fails (and says so more pointedly when
        the log shows the write failing)."""
        qset = "check_claim"
        known = set(known_ids) | {i for call in log for i in checks.find_note_ids(canonical(call.get("arguments", {})))}
        invented = checks.invented_ids(reply, known)
        writes = [call for call in log if call.get("tool") in checks.WRITE_TOOLS]
        succeeded = any(call.get("ok") for call in writes)
        failed = [call for call in writes if not call.get("ok")]
        if invented:
            failures = [f"invented note ID {i}" for i in invented]
            decision_id = self._record(
                "check_claim", qset, {"reply": reply}, {}, None, "code_failed", "; ".join(failures), "fail", context
            )
            return {"decision_id": decision_id, "passed": False, "verified": True, "failures": failures,
                    "source": "code", "status": "code_failed", "probabilities": {}}
        state = {"reply": reply}
        questions, prediction, status, detail = self._ask(qset, state)
        failures, probabilities = [], {}
        if prediction:
            _, _, probabilities = self._choice(prediction, "claim")
            claims_change = self._clears(qset, "claim", "made_change", probabilities["made_change"])
            if claims_change and not succeeded:
                failures.append("presents a failed write as successful" if failed
                                else "claims a change the log does not show")
        action = "fail" if failures else "pass"
        decision_id = self._record("check_claim", qset, state, questions, prediction, status, detail, action, context)
        return {
            "decision_id": decision_id,
            "passed": not failures,
            "verified": prediction is not None,  # code-only when Laya couldn't answer
            "failures": failures,
            "failed_writes": len(failed),
            "source": "laya" if prediction else "code",
            "status": status,
            "probabilities": probabilities,
        }

    # ------------------------------------------------------------------ rerank
    def rerank(self, query: str, matches: list[dict], context: Context | None = None) -> dict[str, Any]:
        """search's matches by meaning reordered by Laya's `relevant` probability, top k kept. If any
        chunk can't be scored, the search's own order stands - a partial ranking would be worse."""
        top_k = self.config.rerank.top_k
        scored = []
        for match in matches:
            state = {"query": query, "passage": match.get("text", "")}
            questions, prediction, status, detail = self._ask("rerank", state)
            p = self._noul(prediction, "relevant") if prediction else None
            self._record("rerank", "rerank", state, questions, prediction, status, detail,
                         f"{p:.4f}" if p is not None else "unscored", context)
            if p is None:
                return {"matches": matches[:top_k], "reranked": False, "status": status}
            scored.append((p, match))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return {
            "matches": [{**match, "relevance": round(p, 4)} for p, match in scored[:top_k]],
            "reranked": True,
            "status": "ok",
        }

    # ------------------------------------------------------------------ note_type
    def note_type(self, title: str, content: str, context: Context | None = None) -> dict[str, Any]:
        """A new note's type, judged from its title and text. `decided` says whether the choice clears
        its threshold; when it doesn't (or Laya can't be asked), `type` is None and the caller keeps
        its default."""
        qset = "note_type"
        state = {"note": f"# {title}\n\n{content}".strip()}
        questions, prediction, status, detail = self._ask(qset, state, {"note_types": TYPE_DESCRIPTIONS})
        choice, p, probabilities = (None, None, {})
        if prediction:
            choice, p, probabilities = self._choice(prediction, "note_type")
        decided = choice is not None and self._clears(qset, "note_type", choice, p)
        decision_id = self._record("note_type", qset, state, questions, prediction, status, detail,
                                   choice if decided else "undecided", context)
        return {"decision_id": decision_id, "type": choice if decided else None, "choice": choice, "p": p,
                "probabilities": probabilities, "source": "laya" if prediction else "fallback", "status": status}

    # ------------------------------------------------------------------ watch_thinking
    def watch_thinking(
        self, request: str, steps: list[dict], thinking: str, context: Context | None = None
    ) -> dict[str, Any]:
        """Mid-reasoning: `repeating`, `ready` or `stuck` when that clears its threshold, else
        `progressing` (keep going) - which is also the answer when Laya can't be asked."""
        qset = "watch_thinking"
        state = {"request": request, "steps": steps, "thinking": thinking}
        questions, prediction, status, detail = self._ask(qset, state)
        signal, probabilities = "progressing", {}
        if prediction:
            choice, p, probabilities = self._choice(prediction, "state")
            if choice != "progressing" and self._clears(qset, "state", choice, p):
                signal = choice
        # Logged under watch_thinking, in watch_laya's mode: the watch's code checks can be enforced
        # while this question is still only watched. Off, but asked anyway, it is logged as shadow.
        watch_mode = self.config.mode("watch_laya")
        decision_id = self._record("watch_thinking", qset, state, questions, prediction, status, detail, signal, context,
                                   mode="shadow" if watch_mode == "off" else watch_mode)
        return {"decision_id": decision_id, "signal": signal, "source": "laya" if prediction else "fallback",
                "status": status, "probabilities": probabilities}
