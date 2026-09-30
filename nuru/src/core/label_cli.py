"""`nuru label` and `nuru export`: review the decision layer's logged decisions and label them, for
calibrating thresholds now and fine-tuning Laya later.

    uv run nuru label [--point tool_gate] [--limit 20] [--source user|claude]
    uv run nuru export labels.jsonl

`label` shows the unlabelled decisions Laya answered - the ones where what Nuru did differs from
what Laya proposed first - and asks for the right answer to each of its questions. A label is
stored as JSON, {question: answer}; a question left blank is left out. The source is `user` or
`claude`, never the local model.
"""

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from laya_mcp.config import load_config
from laya_mcp.decision_log import LABEL_SOURCES, DecisionLog

_STATE_PREVIEW_CHARS = 1500


class _Quit(Exception):
    pass


def _options(question: dict) -> list[str]:
    if question["type"] == "noul":
        return ["true", "false"]
    if question["type"] == "score":
        return [str(i) for i in range(len(question["criteria"]))]
    return list(question["criteria"])


def _probabilities(answer: dict | None) -> dict[str, float]:
    if not answer:
        return {}
    if answer.get("type") == "noul":
        return {"true": answer["noul"], "false": round(1 - answer["noul"], 4)}
    return answer.get("probabilities", {})


def _show(row, out: Callable[[str], None]) -> None:
    out("")
    out(f"#{row['id']}  {row['decision_point']} / {row['question_set']} v{row['question_set_version']}"
        f"  [{row['checkpoint']}, {row['mode']}]  {row['ts']}")
    out(f"  Laya proposed: {row['proposed_action']}    Nuru did: {row['action_taken'] or '(not recorded)'}"
        + (f"    outcome: {row['outcome']}" if row["outcome"] else ""))
    state = row["state_text"]
    try:
        state = json.dumps(json.loads(state), indent=2, ensure_ascii=False)
    except ValueError:
        pass
    if len(state) > _STATE_PREVIEW_CHARS:
        state = state[:_STATE_PREVIEW_CHARS] + f"\n  ... ({len(state) - _STATE_PREVIEW_CHARS} more characters)"
    out("  state:")
    out("\n".join("    " + line for line in state.splitlines()))


def _ask_label(row, read: Callable[[str], str], out: Callable[[str], None]) -> dict[str, str]:
    questions = json.loads(row["questions_json"])
    answers = json.loads(row["answers_json"] or "{}")
    label = {}
    for qid, question in questions.items():
        options = _options(question)
        p = _probabilities(answers.get(qid))
        out(f"  {qid}: {question['instructions']}")
        for i, option in enumerate(options, 1):
            out(f"    {i:>2}. {option}" + (f"  (Laya {p[option]:.2f})" if option in p else ""))
        while True:
            reply = read(f"  {qid} - number or name, Enter to skip, q to quit: ").strip()
            if reply == "q":
                raise _Quit
            if not reply:
                break
            if reply.isdigit() and 1 <= int(reply) <= len(options):
                label[qid] = options[int(reply) - 1]
                break
            if reply in options:
                label[qid] = reply
                break
            out(f"  not one of the options: {reply!r}")
    return label


def label(log: DecisionLog, point: str | None, limit: int, source: str,
          read: Callable[[str], str] = input, out: Callable[[str], None] = print) -> int:
    rows = log.review_queue(limit, point)
    if not rows:
        out("Nothing to label.")
        return 0
    done = 0
    try:
        for row in rows:
            _show(row, out)
            answer = _ask_label(row, read, out)
            if answer:
                log.set_label(row["id"], json.dumps(answer, sort_keys=True), source)
                done += 1
    except (_Quit, EOFError):
        pass
    out(f"\nLabelled {done} decision{'s' if done != 1 else ''}.")
    return done


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="nuru", description="Review and export the decision layer's log.")
    parser.add_argument("--db", type=Path, help="decision log (default: `database` in config/decision_layer.yaml)")
    commands = parser.add_subparsers(dest="command", required=True)
    label_cmd = commands.add_parser("label", help="label logged decisions, disagreements first")
    label_cmd.add_argument("--point", help="only this decision point (tool_gate, assess_effort, ...)")
    label_cmd.add_argument("--limit", type=int, default=20)
    label_cmd.add_argument("--source", choices=LABEL_SOURCES, default="user")
    export_cmd = commands.add_parser("export", help="write labelled decisions as JSONL for fine-tuning")
    export_cmd.add_argument("path", type=Path)
    args = parser.parse_args(argv)

    db = args.db or load_config().database_path
    if not db.exists():
        sys.exit(f"No decision log at {db} yet - run Nuru first.")
    log = DecisionLog(db)
    try:
        if args.command == "label":
            label(log, args.point, args.limit, args.source)
        else:
            count = log.export_labelled(args.path)
            print(f"Wrote {count} labelled decision{'s' if count != 1 else ''} to {args.path}.")
    finally:
        log.close()
