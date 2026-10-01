# Categorisation training data v3: the 190 added requests, for audit

Written and labelled by Claude on 2026-10-01 under the agreed rules (see [categorise_train.py](categorise_train.py) and [results-2026-10-01-laya-v2.md](results-2026-10-01-laya-v2.md)). The source is [categorise_train_v3.py](categorise_train_v3.py). The user audits these after the v3 training run.

**How to audit:** put ✓ in the *OK?* column, or write the labels you would give. I'll apply corrections to `categorise_train_v3.py` and retrain.

**Rules used:**
- `expert` covers judgement over notes (contradictions, what's missing or risky, root causes, recommendations), plus designs, proofs and plans. Gathering, listing and summarising is `sweep` or `lookup` alone.
- `answer` goes on any request that needs no notes, pleasantries included.
- "this note" requests assume an earlier turn identified the note, so they're lookups. An "it", "that" or "them" with nothing to point at stays `clarify`.
- A write to an existing note is lookup+write.
- Numbers from one note are lookup+calculate; numbers from many notes are sweep+calculate.

**Soft labels** (p = probability for categories not listed) are shown where a request is borderline.

**Independence note:** these requests target the kinds of prompt the user's 100-prompt test set exposed, such as short clear questions, judgement over notes and "this note" requests. None is a copy or near-copy of a test prompt: every one was checked against both test sets with a similarity cut-off of 0.7. But on those patterns, the user's set is now less independent than it was.


## judgement over many notes: sweep + expert (40)

| # | Request | Labels | Soft | OK? |
|---:|---|---|---|---|
| 1 | Which of my project notes make claims that no experiment backs up? | sweep+expert |  |  |
| 2 | Go through my research notes and tell me which conclusions look shakiest. | sweep+expert |  |  |
| 3 | What patterns show up in the bugs I've logged this year? | sweep+expert |  |  |
| 4 | Where do my meeting notes and my project notes disagree about deadlines? | sweep+expert |  |  |
| 5 | Which of my ideas from last spring turned out to matter most? | sweep+expert |  |  |
| 6 | Rank my open projects by how close they are to done, with reasons. | sweep+expert | calculate 0.2 |  |
| 7 | What risks am I not tracking anywhere in my project notes? | sweep+expert |  |  |
| 8 | How has my thinking on battery chemistry evolved across my notes? | sweep+expert |  |  |
| 9 | Which decisions in my notes were made without enough information? | sweep+expert |  |  |
| 10 | What would a skeptical reviewer say about the claims in my research folder? | sweep+expert |  |  |
| 11 | Spot the gaps in my notes on the sensor calibration procedure. | sweep+expert |  |  |
| 12 | Do my habit-tracking notes suggest I'm heading for burnout? | sweep+expert |  |  |
| 13 | Do my notes on the two vendor quotes point to a clear winner? | sweep+expert |  |  |
| 14 | What recurring problems keep coming up in my weekly reviews? | sweep+expert |  |  |
| 15 | Which open questions in my notes block the most other work? | sweep+expert |  |  |
| 16 | Across my reading notes, which authors contradict each other? | sweep+expert |  |  |
| 17 | What's the weakest link in my thesis argument, based on my notes? | sweep+expert |  |  |
| 18 | Which of my experiments produced results I never explained? | sweep+expert |  |  |
| 19 | Look over my goals notes and tell me which goals conflict. | sweep+expert |  |  |
| 20 | What lessons from past projects am I ignoring in the current one? | sweep+expert |  |  |
| 21 | Which concerns raised in my meeting notes have gone unanswered, and which matter most? | sweep+expert |  |  |
| 22 | From my health log, what seems to affect my sleep the most? | sweep+expert |  |  |
| 23 | What's the real reason Project Lumen keeps slipping, judging by my notes? | sweep+expert |  |  |
| 24 | Review my notes on the pricing model and tell me what doesn't add up. | sweep+expert |  |  |
| 25 | Which of my notes have aged badly and now give wrong advice? | sweep+expert |  |  |
| 26 | Group my scattered ideas into themes and say which theme deserves my focus. | sweep+expert |  |  |
| 27 | Where am I overcommitted, judging by my task lists and calendar notes? | sweep+expert |  |  |
| 28 | Which hypotheses in my lab notebook have the strongest evidence? | sweep+expert |  |  |
| 29 | What do my notes suggest I should stop doing? | sweep+expert |  |  |
| 30 | How did my description of the system architecture change since January, and what drove it? | sweep+expert |  |  |
| 31 | Find claims in my notes that rest on a single source. | sweep+expert |  |  |
| 32 | What are the hidden dependencies between my active projects? | sweep+expert |  |  |
| 33 | Which of my notes on the outage point to the true root cause? | sweep+expert |  |  |
| 34 | Judge which of my side projects is most likely to succeed. | sweep+expert |  |  |
| 35 | What did I get wrong in last year's predictions notes? | sweep+expert |  |  |
| 36 | Where do Jordan and I disagree in my meeting notes, and who had the stronger case? | sweep+expert |  |  |
| 37 | Identify the riskiest bets across my startup notes. | sweep+expert |  |  |
| 38 | What is quietly falling behind across my projects? | sweep+expert |  |  |
| 39 | Which of my literature notes undermine my main hypothesis? | sweep+expert |  |  |
| 40 | Which unanswered questions in my notes matter most for the thesis? | sweep+expert |  |  |

## judgement over one note: lookup + expert (30)

| # | Request | Labels | Soft | OK? |
|---:|---|---|---|---|
| 41 | Read my grant proposal draft and tell me where reviewers will push back. | lookup+expert |  |  |
| 42 | Check the reasoning in my note on the cooling fan failure. | lookup+expert |  |  |
| 43 | Is the conclusion in my Experiment 9 note actually supported by its data? | lookup+expert |  |  |
| 44 | Look at my budget note and tell me where I'm likely to overspend. | lookup+expert |  |  |
| 45 | Using my note on the client's requirements, propose an architecture. | lookup+expert |  |  |
| 46 | What's wrong with the test plan in my firmware release note? | lookup+expert |  |  |
| 47 | Given the symptoms in my laptop troubleshooting note, what's the likely cause? | lookup+expert |  |  |
| 48 | Critique the argument in my essay draft. | lookup+expert |  |  |
| 49 | Based on my note on the new hire's background, how should I onboard them? | lookup+expert |  |  |
| 50 | Review the risk register in my Project Lumen note and say what's missing. | lookup+expert |  |  |
| 51 | Does the schedule in my renovation note make sense? | lookup+expert |  |  |
| 52 | From my interview notes with Dana, should I make an offer? | lookup+expert |  |  |
| 53 | Find the flaw in the derivation in my thermal model note. | lookup+expert |  |  |
| 54 | Using my training log, design next month's running plan. | lookup+expert |  |  |
| 55 | My note on the API outage lists three theories. Which is most likely? | lookup+expert |  |  |
| 56 | Weigh the trade-offs I listed in my database choice note and pick one. | lookup+expert |  |  |
| 57 | Read my summary of the sparse attention paper and tell me if its method generalises. | lookup+expert |  |  |
| 58 | What should I change in my presentation outline to make it more convincing? | lookup+expert |  |  |
| 59 | Look at my note on the sensor noise and suggest a filtering approach. | lookup+expert |  |  |
| 60 | Given my notes on the two job offers, which fits my goals better? | lookup+expert |  |  |
| 61 | Is the sample size in my survey note big enough for its conclusions? | lookup+expert |  |  |
| 62 | Review the protocol in my Experiment 21 note for confounders. | lookup+expert |  |  |
| 63 | Based on my diet note, what's a realistic plan to lower my cholesterol? | lookup+expert |  |  |
| 64 | Assess the security of the setup described in my home server note. | lookup+expert |  |  |
| 65 | What's the strongest objection to the proposal in my strategy note? | lookup+expert |  |  |
| 66 | Using my note on the motor specs, choose a suitable driver circuit. | lookup+expert |  |  |
| 67 | Read my negotiation prep note and tell me where I'm weakest. | lookup+expert |  |  |
| 68 | Are the units in my fluid dynamics note consistent throughout? | lookup+expert | calculate 0.4 |  |
| 69 | My Experiment 3 note records a value far from the textbook one. What could explain it? | lookup+expert |  |  |
| 70 | Turn the requirements in my robot arm note into a design proposal. | lookup+expert |  |  |

## short, clear questions: sweep (25)

| # | Request | Labels | Soft | OK? |
|---:|---|---|---|---|
| 71 | What's due this week? | sweep |  |  |
| 72 | What's overdue? | sweep |  |  |
| 73 | Which tasks are still open? | sweep |  |  |
| 74 | What's on my plate? | sweep |  |  |
| 75 | What did I finish last week? | sweep |  |  |
| 76 | Who owes me a reply? | sweep |  |  |
| 77 | What changed since Friday? | sweep | lookup 0.3 |  |
| 78 | Anything urgent? | sweep |  |  |
| 79 | What deadlines are coming up? | sweep |  |  |
| 80 | What have I been reading lately? | sweep |  |  |
| 81 | Which bugs are still open? | sweep |  |  |
| 82 | What's new in my vault this week? | sweep |  |  |
| 83 | What did I promise Sam? | sweep |  |  |
| 84 | Who have I met with this month? | sweep |  |  |
| 85 | What's left to do on the website? | sweep |  |  |
| 86 | What books did I finish this year? | sweep |  |  |
| 87 | Which projects are active? | sweep |  |  |
| 88 | Any open questions about the launch? | sweep |  |  |
| 89 | What did I log at the gym this week? | sweep |  |  |
| 90 | Which ideas have I starred? | sweep |  |  |
| 91 | How busy is next week? | sweep | lookup 0.3 |  |
| 92 | What did I work on in August? | sweep |  |  |
| 93 | Which meetings got cancelled this month? | sweep |  |  |
| 94 | What's still unpaid? | sweep |  |  |
| 95 | Who did I talk to about the grant? | sweep |  |  |

## short, clear questions: lookup (15)

| # | Request | Labels | Soft | OK? |
|---:|---|---|---|---|
| 96 | What did I do yesterday? | lookup |  |  |
| 97 | Any meetings today? | lookup |  |  |
| 98 | When's my dentist appointment? | lookup |  |  |
| 99 | What's the Wi-Fi password at the cabin? | lookup |  |  |
| 100 | What's Jordan's email? | lookup |  |  |
| 101 | When does my lease end? | lookup |  |  |
| 102 | Where's the spare key? | lookup |  |  |
| 103 | What was the error code from the failed build? | lookup |  |  |
| 104 | How long is the warranty on the fridge? | lookup |  |  |
| 105 | What's the plan for Saturday? | lookup | sweep 0.25 |  |
| 106 | What's on tomorrow's meeting agenda? | lookup |  |  |
| 107 | What's my locker combination? | lookup |  |  |
| 108 | When is Mia's birthday? | lookup |  |  |
| 109 | What's the license key for the CAD software? | lookup |  |  |
| 110 | What's still pending from Monday's meeting? | lookup | sweep 0.25 |  |

## three labels (25)

| # | Request | Labels | Soft | OK? |
|---:|---|---|---|---|
| 111 | Across my experiment logs, is the improvement in yield statistically significant? | sweep+calculate+expert |  |  |
| 112 | Use my sales notes to forecast next quarter's revenue and explain the assumptions. | sweep+calculate+expert |  |  |
| 113 | From my time logs, work out where my hours go and recommend what to cut. | sweep+calculate+expert |  |  |
| 114 | Analyse the failure rates across my test logs and decide which component to redesign. | sweep+calculate+expert |  |  |
| 115 | From my reading log, estimate how many books I'll finish this year and whether my goal is realistic. | sweep+calculate+expert |  |  |
| 116 | Do my measurements across all runs support a linear relationship? Fit a line and judge it. | sweep+calculate+expert |  |  |
| 117 | Check the load calculation in my bridge model note and say whether the design is safe. | lookup+calculate+expert |  |  |
| 118 | Recompute the ROI in my solar panel note and tell me if it's worth it. | lookup+calculate+expert |  |  |
| 119 | Verify the error budget in my sensor note and tell me which term dominates. | lookup+calculate+expert |  |  |
| 120 | Rerun the numbers in my mortgage note and advise whether to refinance. | lookup+calculate+expert |  |  |
| 121 | Are the power figures in my motor test note physically plausible? Work it out. | lookup+calculate+expert |  |  |
| 122 | Distil all my notes on negotiation into a principles note. | sweep+write+expert |  |  |
| 123 | Write a strategy note for next year based on all my project retrospectives. | sweep+write+expert |  |  |
| 124 | Turn my scattered lab observations into a draft results section. | sweep+write+expert |  |  |
| 125 | Create a decision memo weighing the options discussed across my meeting notes. | sweep+write+expert |  |  |
| 126 | Build a literature review note synthesising my paper summaries on battery ageing. | sweep+write+expert |  |  |
| 127 | Rewrite the abstract in my paper draft to be more convincing. | lookup+write+expert |  |  |
| 128 | Restructure my thesis outline so the argument flows better. | lookup+write+expert |  |  |
| 129 | Improve the test plan in my firmware release note and save the new version. | lookup+write+expert |  |  |
| 130 | Redesign the schedule in my renovation note to finish two weeks sooner. | lookup+write+expert |  |  |
| 131 | Convert the recipe in my bread note to grams and update it. | lookup+calculate+write |  |  |
| 132 | Add a total row to the expense table in my trip note. | lookup+calculate+write |  |  |
| 133 | Recompute the averages in my sleep log and write them in. | lookup+calculate+write |  |  |
| 134 | Update my budget note with the monthly totals. | lookup+calculate+write |  |  |
| 135 | Fill in the missing percentages in my survey results note. | lookup+calculate+write |  |  |

## "this note", resolved by an earlier turn (20)

| # | Request | Labels | Soft | OK? |
|---:|---|---|---|---|
| 136 | What does this note say about the deadline? | lookup |  |  |
| 137 | Summarise this note. | lookup | sweep 0.1 |  |
| 138 | Who is mentioned in this note? | lookup |  |  |
| 139 | What's the main conclusion of this paper note? | lookup |  |  |
| 140 | List the action items in this meeting note. | lookup |  |  |
| 141 | Which links in this note are broken? | lookup |  |  |
| 142 | What tags does this note have? | lookup |  |  |
| 143 | When was this note last updated? | lookup |  |  |
| 144 | Add a references heading to this note. | lookup+write |  |  |
| 145 | Fix the table formatting in this note. | lookup+write |  |  |
| 146 | Tag this note as #draft. | lookup+write |  |  |
| 147 | Add today's date to the log in this note. | lookup+write |  |  |
| 148 | Shorten the introduction of this note. | lookup+write |  |  |
| 149 | Total the amounts in this note. | lookup+calculate |  |  |
| 150 | What's the median of the scores in this table? | lookup+calculate |  |  |
| 151 | What are the distances in this note in miles? | lookup+calculate |  |  |
| 152 | How many tasks in this note are done? | lookup+calculate |  |  |
| 153 | Is the argument in this note sound? | lookup+expert |  |  |
| 154 | What's missing from the plan in this note? | lookup+expert |  |  |
| 155 | Suggest a better structure for this note. | lookup+expert |  |  |

## calculations over notes (25)

| # | Request | Labels | Soft | OK? |
|---:|---|---|---|---|
| 156 | Double-check the sum in my invoice note. | lookup+calculate |  |  |
| 157 | What's the average rating in my restaurant list? | lookup+calculate |  |  |
| 158 | How much did the kitchen renovation cost in total, per my renovation note? | lookup+calculate |  |  |
| 159 | From my fuel log, what's my car's average consumption? | lookup+calculate |  |  |
| 160 | What's the spread between the highest and lowest readings in my Experiment 12 note? | lookup+calculate |  |  |
| 161 | How many weeks of runway does my budget note imply? | lookup+calculate |  |  |
| 162 | What's the subscriber growth rate in my newsletter note? | lookup+calculate |  |  |
| 163 | Work out the efficiency from the input and output power in my motor test note. | lookup+calculate |  |  |
| 164 | What percentage of my savings goal have I reached, per my finance note? | lookup+calculate |  |  |
| 165 | Compute the mean response time from the logs in my server incident note. | lookup+calculate |  |  |
| 166 | How many days did the shipment take, according to my order note? | lookup+calculate |  |  |
| 167 | What's my weighted grade from the scores in my course note? | lookup+calculate |  |  |
| 168 | Plot the temperatures in my greenhouse note over time. | lookup+calculate |  |  |
| 169 | What's my total spending on books this year across my notes? | sweep+calculate |  |  |
| 170 | How many experiments have I run in total? | sweep+calculate |  |  |
| 171 | Chart my weight across all my health log entries. | sweep+calculate |  |  |
| 172 | How often did the build fail last month, from my daily notes? | sweep+calculate |  |  |
| 173 | Average the scores from all my code review notes. | sweep+calculate |  |  |
| 174 | How many hours of deep work did I log each week this month? | sweep+calculate |  |  |
| 175 | What's the median length of my meeting notes? | sweep+calculate |  |  |
| 176 | Count the open issues across all my project notes. | sweep+calculate |  |  |
| 177 | Which month did I spend the most on travel? | sweep+calculate | expert 0.1 |  |
| 178 | Track how my reading speed changed across my reading logs. | sweep+calculate | expert 0.15 |  |
| 179 | What's the average turnaround on my pull requests, from my work logs? | sweep+calculate |  |  |
| 180 | How many new contacts did I add per month this year? | sweep+calculate |  |  |

## pleasantries and wrap-ups: answer (10)

| # | Request | Labels | Soft | OK? |
|---:|---|---|---|---|
| 181 | Okay, all done here. | answer |  |  |
| 182 | Okay, great. | answer |  |  |
| 183 | Cheers! | answer |  |  |
| 184 | Perfect, thank you. | answer |  |  |
| 185 | Got it. | answer | clarify 0.1 |  |
| 186 | Never mind, I'm done for today. | answer |  |  |
| 187 | Bye for now. | answer |  |  |
| 188 | Nice work. | answer |  |  |
| 189 | That's helpful. | answer |  |  |
| 190 | Ok cool, talk later. | answer |  |  |
