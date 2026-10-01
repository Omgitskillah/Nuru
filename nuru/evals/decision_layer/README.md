# Decision-layer evals

Runnable evaluations for the Laya decision layer (see [servers/laya](../../../servers/laya)). Each writes its raw results to `.runs/` here - hidden and gitignored, like everything generated - and prints a summary.

Run them one at a time, under `caffeinate -i` (the Mac sleeps mid-run otherwise, and timings balloon), and not while anything else uses the GPU: several measure latency. They ask the local model real questions, and the suite runs can call the cloud expert (`ask_an_expert`) - ask before running them.

| # | Script | What it measures | Model runs |
|---|---|---|---|
| 1 | `think_modes.py` | Thinking tokens, latency and correctness per thinking level (off / low / medium / high) on argument-only steps, with `think` passed top-level and inside `options`. Temperature 0 and a fixed seed, so "two levels are the same" means byte-identical output. | 30 calls |
| 2 | `format_enforcement.py` | Whether the output always matches the forced argument schema (`core.forced_schema`), in every tier the config maps, at Nuru's own sampling - thinking off above all. | 5 steps x 3 tiers x `--repeats` |
| 3 | `run_suite.py` + `tier_labels.py` | Suite tests 28-31 (the constrained edits) at every local tier, three runs each, scored out of 5 by `score_runs.py`; a test's label is the cheapest tier that passes (4/5+) in two of three runs. | 36 turns |
| 4 | `baseline_compare.py` | Always-`high` against the `assess_effort` policy, replayed from eval 3's batch: the candidate's score and latency are the measured ones at the tier Laya picked for the first step. | none (reuses 3) |
| 5 | `run_suite.py` + `shadow_report.py` | The whole 34-prompt suite in shadow mode, then Laya's tool choice next to the model's at every step, disagreements included. | 34 turns |
| 6 | `check_claim_set.py` | check_claim on planted false claims (a write claimed that failed, one never attempted, an invented note ID) mixed with honest replies: catches and false alarms. | Laya only |
| 7 | `laya_latency.py` | Laya's per-call latency per question set, MPS against CPU. | Laya only |
| 8 | `categorise.py` | Sorting 50 prompts (`prompt_categories.py`) into 7 categories, several per prompt: Laya in a loop of up to 3 calls (each pick removed from the options) against one granite4.2:3b call. Exact set, first pick, recall, precision and time. `--set user` runs the user's 100 prompts (`prompt_categories_user.py`); `--laya-checkpoint` tests a fine-tuned checkpoint. Notes in `categorisation-notes.md`. | 50 calls + Laya |
| 9 | `train_categorise.py` | Fine-tunes Laya's English checkpoint on the yes/no categorisation question from `categorise_train.py` (+ `categorise_train_v3.py`), on MPS, with Convai's notebook recipe; saves a checkpoint under `.runs/`. Results: `results-2026-10-01-laya-*.md`. | training, ~30 min |

```bash
cd nuru/evals/decision_layer
caffeinate -i uv run python think_modes.py
caffeinate -i uv run python format_enforcement.py
caffeinate -i uv run python run_suite.py --tests 28-31 --tiers off,low,high --repeats 3 --label tiers
uv run python tier_labels.py .runs/suite-tiers-<time>
uv run python baseline_compare.py .runs/suite-tiers-<time>
caffeinate -i uv run python run_suite.py --tests 1-34 --label shadow
uv run python shadow_report.py .runs/suite-shadow-<time>
uv run python check_claim_set.py
uv run python laya_latency.py
caffeinate -i uv run python categorise.py --think low
caffeinate -i uv run python train_categorise.py
caffeinate -i uv run python categorise.py --set user --only laya --laya-checkpoint .runs/laya-categorise-<time>
```

## Iterating quickly

Most changes don't need the two-hour suite:

- **A change on Laya's side** - question wording, thresholds, the checkpoint, calibration - is judged by **`replay.py`**, which re-asks Laya on the states earlier runs logged: about a minute for a full suite's worth, no model runs. It reports accuracy against your labels (`nuru label`) where there are any, how often Laya's tool choice matches the model's, and how many answers changed. `--questions <folder>` tries variant question sets without touching the real ones.
- **A change to the model, its Modelfile or Nuru's loop** needs live runs: **`run_suite.py --tests quick`** runs eight tests, one per behaviour worth watching, each of which has failed in an earlier scored run (bar the no-tool control) - about 15 minutes.
- **The full 34** stay the occasional regression check: before switching a decision point to `enforce`, or after changing models.

**Collecting labelled data**: run the quick suite at every tier, then `autolabel.py` labels the decisions whose right answer the outcome settles (source `claude`: the rules are the test rubrics in `score_runs.py` plus each test's intent) and leaves the rest for `nuru label --db .runs/<batch>/decisions.sqlite3`.

```bash
caffeinate -i uv run python run_suite.py --tests quick --tiers off,low,high --repeats 3 --label data
uv run python autolabel.py .runs/suite-data-<time>
uv run python replay.py .runs/suite-shadow-<time> .runs/suite-tiers-<time>
caffeinate -i uv run python run_suite.py --tests quick --label quick
```

**Testing the enforce paths**: `run_suite.py --enforce all` (or a comma-separated list of decision points) enforces them in that batch only. Its config - written into the batch folder, headed TEST ONLY - marks the thresholds calibrated so that it loads; they aren't, so such a run tests the machinery, not how a calibrated Nuru would do. The real `config/decision_layer.yaml` is never touched.

## How the suite runs are isolated

`run_suite.py` runs one fresh Nuru process per turn (`drive_turn.py`), each on a fresh scratch copy of dummyVault inside the batch folder, with the batch's own conversation history, session logs and decision log - the real vault, history and decision log are never touched. The semantic index is built once from the pristine copy and restored before every turn. A turn gets 10 minutes; past that its whole process tree is killed and it counts as a failure. The driver waits for Laya to finish loading before asking (a fresh process otherwise answers every first decision with a fallback).

## What these can't measure yet

The **expert tier** and the **assess_effort policy actually steering a run** (tiers rising on failure signals, escalation to Claude) arrive with the enforcement paths in Phase 4. Until then, eval 3 labels a test `expert` when no local tier passes (untested), and eval 4 is a replay rather than a live A/B. Both should be re-run once Phase 4 lands.
