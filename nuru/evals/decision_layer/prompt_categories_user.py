"""The second categorisation test set: 100 prompts written and labelled by the user (2026-10-01).

Hypothetical throughout; the names are random. Labels are the user's and are the gold, as written,
including where they follow different conventions from the first set and the training data
(categorise_train.py). GROUPS names the prompts that are reported separately:

- `context`: refers to "this note / task / number ..." that an earlier turn would have resolved
  (the user's intent); Laya and the 3B see the prompt alone.
- `convention`: labelled by the user's own convention where the training data used another
  (`answer` alongside `calculate`/`expert` for self-contained questions, #52, 54, 57, 59; today's daily note as
  lookup+write).
- `overlap`: also in the training data, word for word or nearly (computed below).

Revised 2026-10-01 at the user's request: `expert` removed from #2, 14, 16, 27, 37, 46, 47 and 58
(summaries, side-by-side comparisons, link audits and standard explanations a small model can do).

Only for testing - never train on these.
"""

import difflib

from categorise_train import TRAIN
from categorise_train_v3 import ADDED_V3
from categorise_train_v4 import ADDED_V4

PROMPTS = [
    ("What was I working on this time last year?", {"sweep"}),
    ("Give me a 5-minute briefing on everything I currently have in progress.", {"sweep"}),
    ("What am I forgetting?", {"sweep", "expert"}),
    ("Find every commitment I made to someone in the last 30 days.", {"sweep"}),
    ("What decisions did I make this week, and why?", {"sweep"}),
    ("What have I changed my mind about over the last year?", {"sweep", "expert"}),
    ("Find contradictions in my notes.", {"sweep", "expert"}),
    ("What assumptions am I repeatedly making without evidence?", {"sweep", "expert"}),
    ("Which notes contain ideas that I never followed up on?", {"sweep"}),
    ("Show me the most important connections between notes that I haven't linked.", {"sweep", "expert"}),
    ("What topics have I been thinking about unusually often lately?", {"sweep", "calculate"}),
    ("What are my longest-running unresolved questions?", {"sweep", "calculate"}),
    ("Build a timeline of everything I've written about Chemical composition of Mars.", {"sweep"}),
    ("What do I already know about Jungle Gyms?", {"sweep"}),
    ("What do I *not* know about German tax codes?", {"sweep", "expert"}),
    ("Compare my notes on approaches A, B, and C.", {"sweep"}),
    ("Turn my scattered notes about nature rights into a proper technical note.", {"sweep", "write", "expert"}),
    ("Find every experiment relevant to this hypothesis.", {"sweep", "expert"}),
    ("Which experiments should I probably rerun?", {"sweep", "expert"}),
    ("What parameter values have I tried for this system?", {"sweep"}),
    ("Plot how this measurement has changed across my experiments.", {"sweep", "calculate"}),
    ("Recalculate the result in this note and check my math.", {"lookup", "calculate"}),
    ("Where did this number come from?", {"lookup", "expert"}),
    ("Find numbers in my notes that appear inconsistent with each other.", {"sweep", "calculate", "expert"}),
    ("Convert all the measurements in this note to SI units.", {"lookup", "calculate", "write"}),
    ("Extract every equation I've used for [topic].", {"sweep"}),
    ("Which equations use this variable, directly or indirectly?", {"sweep"}),
    ("If I change parameter X to 0.35, what downstream calculations need updating?",
     {"sweep", "calculate", "expert"}),
    ("Reproduce the calculation that led to this figure/table.", {"lookup", "calculate", "expert"}),
    ("Create a summary table of all my benchmark results for this project.", {"sweep", "write"}),
    ("What is blocking Project X right now?", {"sweep", "expert"}),
    ("What is the highest-leverage thing I could work on next for Project X?", {"sweep", "expert"}),
    ("Turn my Project X notes into a concrete plan for the next two weeks.", {"sweep", "expert", "write"}),
    ("Which projects haven't moved in more than a month?", {"sweep", "calculate"}),
    ("Which projects am I maintaining that no longer seem connected to my stated goals?", {"sweep", "expert"}),
    ("Find tasks that I keep postponing.", {"sweep"}),
    ("What can I finish in about 30 minutes?", {"sweep"}),
    ("I'm meeting Alice tomorrow. What should I remember?", {"sweep", "expert"}),
    ("Who am I waiting on right now?", {"sweep"}),
    ("Who is waiting on me?", {"sweep"}),
    ("Remind me Friday if I still haven't completed this task.", {"lookup", "write"}),
    ("Move all unfinished tasks from today's daily note to tomorrow, except low-priority ones.",
     {"lookup", "write"}),
    ("Clean up this note without changing what I mean.", {"lookup", "write"}),
    ("Split this giant note into sensible atomic notes.", {"lookup", "write", "expert"}),
    ("Find notes that probably describe the same concept under different names.", {"sweep", "expert"}),
    ("Which notes are becoming knowledge-base dead ends?", {"sweep"}),
    ("Generate my weekly review from everything I recorded this week.", {"sweep", "write"}),
    ("What did I learn this month that would have surprised me a month ago?", {"sweep", "expert"}),
    ("If you had to reconstruct my current mental model of [topic], what would it look like?",
     {"sweep", "expert"}),
    ("Read my entire vault and tell me the five questions I should be asking but haven't asked yet.",
     {"sweep", "expert"}),
    ("What is the difference between TCP and UDP?", {"answer"}),
    ("Convert 72°F to Celsius.", {"answer", "calculate"}),
    ("Explain Bayesian inference in simple terms.", {"answer"}),
    ("What's the derivative of x² sin(x)?", {"answer", "calculate"}),
    ("Give me three reasons Rust's ownership model prevents memory errors.", {"answer"}),
    ("What's the difference between correlation and causation?", {"answer"}),
    ("Calculate the compound annual growth rate from 120 to 185 over 4 years.", {"answer", "calculate"}),
    ("How does a Kalman filter work?", {"answer"}),
    ("What are the tradeoffs between PostgreSQL and SQLite for a local-first application?", {"answer", "expert"}),
    ("Explain what eigenvectors represent intuitively.", {"answer"}),
    ("Find the note where I wrote down my passport expiry date.", {"lookup"}),
    ("What was the IP address I assigned to the Raspberry Pi?", {"lookup"}),
    ("Find my notes from the meeting with Alice on September 12.", {"lookup"}),
    ("What command did I use to fix that Docker DNS issue?", {"lookup"}),
    ("What restaurant did Sam recommend to me?", {"lookup"}),
    ("Find the paper where I noted that accuracy improved by 4.2%.", {"lookup"}),
    ("What was the serial number of the sensor I used in Experiment 14?", {"lookup"}),
    ("What voltage did I use in the final motor test?", {"lookup"}),
    ("Find my recipe for sourdough pancakes.", {"lookup"}),
    ("What did I write about the cause of the server outage on March 3?", {"lookup"}),
    ("Add today's experiment result to the Project Aurora note: efficiency was 87.4%.", {"lookup", "write"}),
    ("Mark the “order replacement filters” task as complete.", {"lookup", "write"}),
    ("Add a TODO to my house note to check the smoke detectors this weekend.", {"lookup", "write"}),
    ("Create a note called “Ideas for distributed sensor calibration” with these three bullets.", {"write"}),
    ("Add a link from my Kalman filter note to my sensor fusion note.", {"lookup", "write"}),
    ("Rename the “ML stuff” note to “Machine Learning Experiments”.", {"lookup", "write"}),
    ("Add a `status: archived` property to the old Project Mercury note.", {"lookup", "write"}),
    ("Append “Ask about calibration drift” to tomorrow's meeting agenda.", {"lookup", "write"}),
    ("Create a daily note for today with headings for Work, Research, Personal, and Tasks.", {"write"}),
    ("Replace every occurrence of “Project Falcon” in this note with “Project Kestrel”.", {"lookup", "write"}),
    ("Average the five latency measurements in this note.", {"lookup", "calculate"}),
    ("Calculate the standard deviation of the temperature readings in Experiment 8.", {"lookup", "calculate"}),
    ("What's the percentage improvement between the baseline and final result in this note?",
     {"lookup", "calculate"}),
    ("Sum all expenses in my Berlin trip note.", {"lookup", "calculate"}),
    ("How many hours did I spend on Project Orion last month?", {"sweep", "calculate"}),
    ("Calculate my average weekly running distance over the last three months.", {"sweep", "calculate"}),
    ("Which experiment has the best signal-to-noise ratio based on these recorded measurements?",
     {"sweep", "calculate"}),
    ("Calculate the correlation between temperature and battery life in my experiment logs.",
     {"sweep", "calculate", "expert"}),
    ("Estimate how long Project Atlas will take based on my recorded task estimates and completion rates.",
     {"sweep", "calculate", "expert"}),
    ("Check whether the measurements in this table satisfy conservation of energy within a 5% tolerance.",
     {"lookup", "calculate", "expert"}),
    ("Design an experiment to distinguish between my two explanations for the sensor drift.", {"lookup", "expert"}),
    ("Given the constraints in my Project Atlas note, propose three viable architectures.", {"lookup", "expert"}),
    ("Why might my latest results disagree with the theoretical prediction in this note?", {"lookup", "expert"}),
    ("Help me decide what measurements I need before I can validate this hypothesis.", {"lookup", "expert"}),
    ("Rewrite my experiment plan to address the confounding variables I identified.",
     {"lookup", "expert", "write"}),
    ("Fix it.", {"clarify"}),
    ("Add that to my notes.", {"clarify"}),
    ("Compare them.", {"clarify"}),
    ("Calculate the result for me.", {"clarify"}),
    ("Update the project with the new numbers.", {"clarify"}),
]

# 1-based numbers, as in the user's table.
_CONTEXT = [18, 20, 21, 22, 23, 25, 27, 29, 41, 43, 44, 80, 81, 83, 87, 90, 93, 94]
_CONVENTION = [42, 52, 54, 57, 59]
_TRAIN = [r for r, _ in TRAIN]
GROUPS = {
    "context": {PROMPTS[n - 1][0] for n in _CONTEXT},
    "convention": {PROMPTS[n - 1][0] for n in _CONVENTION},
    "overlap": {p for p, _ in PROMPTS if difflib.get_close_matches(p, _TRAIN, n=1, cutoff=0.85)},
}

assert len(PROMPTS) == 100 and len({p for p, _ in PROMPTS}) == 100
assert not {p for p, _ in PROMPTS} & set(_TRAIN) - GROUPS["overlap"], "a user test prompt is in the training data"
assert not {p for p, _ in PROMPTS} & {r for r, _ in ADDED_V3 + ADDED_V4}, "a user test prompt is in the v3/v4 additions"
