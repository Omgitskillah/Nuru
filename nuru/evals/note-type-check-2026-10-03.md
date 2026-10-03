# Note type: the model or Laya? 2026-10-03

`write_note` now leaves the type optional:

- New notes go in `nuru/` by default, or in any folder under `nuru/` or `shared/`.
- A missing type defaults to `topic`; the type is enforced, the path isn't.

Laya has a new decision point, `note_type`, asked by Nuru before each `write_note` that names a note. It predicts the type from the note's title and text, choosing among one-line descriptions of each type (`TYPE_DESCRIPTIONS`). It runs in shadow: logged next to the model's own choice, never acted on. Laya won't enforce it until its threshold is calibrated.

This run asks two questions:
1. Can the model be trusted to pick the type?
2. Can Laya's background decision be trusted more?

**Setup:**
- **Prompts:** 18 note-creation prompts with a known right type: 3 person, 3 topic, 2 each of project, session, transcript, index and preferences, 1 profile, 1 correction.
- **Model and vault:** `granite4.2:3b`, `THINK=low`, the seeded scratch vault, one turn per prompt with a reset before each.
- **Decision log:** each run used its own scratch decision database, not Nuru's real one.
- **What was compared:** the model's type is the `type` of its last `write_note`; Laya's is its choice for that write.
- **Time** is the turn itself; median 14.3 s.

## Summary

| | Right | Notes |
|---|---|---|
| Model, of the 18 prompts | **9 / 18** | wrong on 7; on 2 more (716, 717) it wrote nothing |
| Model, on the 13 writes Laya also judged | 9 / 13 | |
| Laya, top choice, same 13 | 9 / 13 | |
| Laya, when its probability was ≥ 0.50 (the uncalibrated threshold) | 7 / 8 | the miss was exactly 0.50 |
| Laya, when ≥ 0.60 | **7 / 7** | covers 7 of the 13 |
| Laya when ≥ 0.60, else the model's type | **11 / 13** | |

**Answers to the two questions, from this small sample (one run, 18 prompts):**

1. **The model is not reliable with types: 9 of 18.**
   - **Mistakes:**
     - a session written into the daily note (709, 710);
     - a standup transcript called a session (712);
     - an index called a topic (713);
     - a profile called a person (715);
     - a topic called an index (705);
     - a correction done as a rewrite of the note itself (718).
   - **No write at all:** 716 and 717.
   - **Two flaws apart from the type:** six times (703–708, 711, 712, 715) it first left out `note` and had to retry after the error. It also put every new note in `nuru/` root, except 701, which went to `nuru/People/`.
2. **Laya is better when it's confident, and no better when it isn't.**
   - Its top choice was right 9 of 13 times, the same as the model.
   - At probability ≥ 0.60 it was right every time (7 of 7, including 713, 715 and 718, where the model was wrong).
   - Below 0.50 it is close to guessing: "correction" for a landlord's contact note (0.31), "preferences" for a learning topic (0.29).
   - The best rule here: **use Laya's type when it's at least 0.60 sure, otherwise the model's.** That would have scored 11 of 13.

**Before relying on this:**
- **Calibrate first.** 18 prompts from one author isn't a calibration. The 0.60 cut-off needs a proper labelled set before `note_type` can be enforced; the config refuses enforcing an uncalibrated point anyway. These runs' decisions are in the scratch databases and can seed that set.
- **Laya can't help when the model chooses the wrong action.** It only sees a `write_note` that names a note. When the model writes a session into the daily note (709, 710), rewrites the note it should correct (718), or doesn't write at all (716, 717), there is no type decision to fix.
- **Laya can be unavailable.** In 702 it was still loading, so there's nothing to compare. The fallback has to be the model's type, or the default.

## A new problem: `replace` can edit the note a correction is about (718)

Asked "write a correction", the model searched, read the FreeRTOS note, and called `write_note(note="nuru/Topics/freertos-task-management.md", mode="replace", type="topic")`. It rewrote that note with the correction folded in, which is the edit corrections exist to prevent. The server only blocks this when `type="correction"` is passed for an existing note.

Options:
- a Modelfile rule against using `replace` for corrections;
- Laya's `gate_write` flagging a `replace` when the request asks for a correction;
- the server refusing `replace` on a note while the request is a correction. The server can't see the request, so this would need Nuru's harness.

## Results

`!` marks a `write_note` call that errored. Laya's entry is its top choice and probability for the successful write.

| # | Right type | Prompt (short) | Model's type | Laya | Created | Time |
|---|---|---|---|---|---|---|
| 701 | person | Dana Lee, new firmware intern | person ✓ | person 0.92 ✓ | nuru/People/dana-lee.md | 26.1 s |
| 702 | person | Marcus Weber, supplier contact | person ✓ | - (Laya loading) | nuru/marcus-weber.md | 7.6 s |
| 703 | person | Landlord Herr Schulz, rent and phone | person ✓ (after !) | correction 0.31 ✗ | nuru/landlord-herr-schulz.md | 16.0 s |
| 704 | topic | LoRaWAN duty cycle limits | topic ✓ (after !) | index 0.50 ✗ | nuru/lorawan-duty-cycle-limits.md | 13.4 s |
| 705 | topic | What I learn about Rust embedded HALs | index ✗ (after !) | preferences 0.29 ✗ | nuru/rust-embedded-hals-learning-notes.md | 12.6 s |
| 706 | topic | Decoupling capacitor placement tips | topic ✓ (after !) | topic 0.34 ✓ | nuru/decoupling-capacitor-placement-tips.md | 12.7 s |
| 707 | project | Balcony weather station, deadline November | project ✓ (after !) | project 1.00 ✓ | nuru/weather-station-on-balcony.md | 15.0 s |
| 708 | project | Q4 firmware release: date, owners, tasks | project ✓ (after !) | project 0.95 ✓ | nuru/q4-firmware-release.md | 13.6 s |
| 709 | session | Today's working session: PSM debugging | daily ✗ | - (daily isn't asked) | nuru/daily-2026-10-03.md | 8.1 s |
| 710 | session | Paired with Alice on the PCB review | daily ✗ | - | nuru/daily-2026-10-03.md | 10.4 s |
| 711 | transcript | Save this call as a transcript | transcript ✓ (after !) | transcript 0.48 ✓ | nuru/call-transcript.md | 16.2 s |
| 712 | transcript | What was said at standup | session ✗ (after !) | project 0.41 ✗ | nuru/standup.md | 20.7 s |
| 713 | index | Note linking my battery notes | topic ✗ | index 0.97 ✓ | nuru/battery-notes.md | 20.6 s |
| 714 | index | Hub note linking my Berlin notes | index ✓ | index 0.99 ✓ | nuru/berlin-hub.md | 31.8 s |
| 715 | profile | A note about me | person ✗ (after !) | profile 0.98 ✓ | nuru/embedded-engineer-profile.md | 16.7 s |
| 716 | preferences | Note my preferences | - (no write) | - | - | 3.0 s |
| 717 | preferences | How I like code reviews done | - (asked to confirm) | - | - | 7.9 s |
| 718 | correction | FreeRTOS 0.58 vs 0.36: write a correction | topic ✗ (`replace` on the original) | correction 0.67 ✓ | - (rewrote the FreeRTOS note) | 73.9 s |
