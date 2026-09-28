"""Score the constrained-edit tests (suite 28-31) out of 5, from what actually changed in the scratch
vault and what the reply said - deterministic, so a tier comparison doesn't depend on who grades.

Five one-point checks per test, modelled on how the 2026-09-21 run docked points (an empty
confirmation cost a point, a missed second correction target cost a point). A test *passes* at
4/5 or better. The rubric is below, one list per test.
"""

from datetime import date

import yaml

from common import AREA_MONITOR, PRIYA, VENDOR_EMAIL

PASS_MARK = 4


def _frontmatter(text: str) -> tuple[dict, str]:
    if text and text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            try:
                return yaml.safe_load(text[4:end]) or {}, text[end + 5:]
            except yaml.YAMLError:
                pass
    return {}, text or ""


def _new_notes(changes, folder: str, note_type: str) -> list[tuple[dict, str]]:
    return [_frontmatter(c["after"]) for c in changes
            if c["before"] is None and c["after"] is not None and c["path"].startswith(folder)
            and _frontmatter(c["after"])[0].get("type") == note_type]


def _only(changes, allowed) -> bool:
    return all(allowed(c) for c in changes)


def _replied(record) -> bool:
    return bool(record.get("reply", "").strip()) and "(No reply was written.)" not in record.get("reply", "")


def score_28(record, changes, today):
    """Daily note for today: soak test running, going to the gym."""
    dailies = [c for c in changes if c["path"].startswith("user/") and c["after"] is not None
               and _frontmatter(c["after"])[0].get("type") == "daily"
               and str(_frontmatter(c["after"])[0].get("date")) == today]
    text = " ".join((c["after"] or "")[len(c["before"] or ""):] for c in dailies).lower()
    return [
        ("today's daily was created or appended to", len(dailies) == 1),
        ("it says the soak test is running", "soak" in text),
        ("it says you're going to the gym", "gym" in text),
        ("nothing else changed", _only(changes, lambda c: c in dailies)),
        ("the reply confirms it", _replied(record)),
    ]


def score_29(record, changes, today):
    """Append to Priya's note: prefers async messages on Fridays."""
    priya = [c for c in changes if c["path"] == "nuru/priya-raman.md"]
    appended = priya[0]["after"][len(priya[0]["before"]):].lower() if priya and priya[0]["after"] else ""
    return [
        ("Priya's note was appended to, existing text untouched",
         bool(priya) and priya[0]["after"] is not None and priya[0]["after"].startswith(priya[0]["before"])),
        ("the new line says async", "async" in appended),
        ("the new line says Fridays", "friday" in appended),
        ("nothing else changed", _only(changes, lambda c: c["path"] == "nuru/priya-raman.md")),
        ("the reply confirms it", _replied(record)),
    ]


def score_30(record, changes, today):
    """A correction: SARA-R510S lead time is 20 weeks, not 26."""
    corrections = _new_notes(changes, "user/", "correction")
    meta, body = corrections[0] if corrections else ({}, "")
    targets = set(meta.get("targets") or [])
    return [
        ("a correction note was created, author user", len(corrections) == 1 and meta.get("author") == "user"),
        ("it targets the vendor email note", VENDOR_EMAIL in targets),
        ("it targets the Area Monitor project", AREA_MONITOR in targets),
        ("it says 20 weeks", "20" in body),
        ("nothing else changed, and the reply confirms it",
         _replied(record) and _only(changes, lambda c: c["before"] is None and c["path"].startswith("user/"))),
    ]


def score_31(record, changes, today):
    """A transcript of today's standup: the soak test and the supercap."""
    transcripts = _new_notes(changes, "nuru/", "transcript")
    meta, body = transcripts[0] if transcripts else ({}, "")
    return [
        ("a transcript for today was created",
         len(transcripts) == 1 and str(meta.get("session", "")).startswith(today)),
        ("it mentions the soak test", "soak" in body.lower()),
        ("it mentions the supercap", "supercap" in body.lower()),
        ("nothing else changed", _only(changes, lambda c: c["before"] is None and c["path"].startswith("nuru/"))),
        ("the reply confirms it", _replied(record)),
    ]


SCORERS = {28: score_28, 29: score_29, 30: score_30, 31: score_31}


def score(record: dict, today: str | None = None) -> tuple[int, list[tuple[str, bool]]]:
    """(points out of 5, the checks) for one run of a constrained-edit test; 0 if it timed out or crashed."""
    if record.get("timed_out") or record.get("crashed"):
        return 0, [("the turn finished", False)]
    checks = SCORERS[record["test"]](record, record["vault_changes"], today or date.today().isoformat())
    return sum(ok for _, ok in checks), checks
