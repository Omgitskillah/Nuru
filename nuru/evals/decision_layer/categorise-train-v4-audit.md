# Categorisation training data v4: the 132 added requests, for audit

Written and labelled by Claude on 2026-10-01, after the user approved all 190 v3 additions and asked for requests that are "worth reasoning about but not too complex to require an expert". The source is [categorise_train_v4.py](categorise_train_v4.py).

**How to audit:** put ✓ in the *OK?* column, or write the labels you would give.

**What this batch teaches:** a short inference, a side-by-side comparison, a standard explanation, a simple word problem or a light reorganisation is *not* `expert`. `expert` stays for designs, proofs, plans and real judgement: contradictions, root causes, what's missing, what to do. Borderline requests carry a small soft `expert` (0.1–0.2) instead of the usual 0.04.

**Rules otherwise as before:** `answer` goes on any request that needs no notes. A write to an existing note is lookup+write. Numbers from one note are lookup+calculate; from many notes, sweep+calculate.

**Kept apart from the tests:** no request is within a 0.7 similarity cut-off of a test prompt. Eight were reworded to get there, mostly away from the "What's the difference between X and Y?" template that three test prompts use.


## explanations that take some thought: answer (30)

| # | Request | Labels | Soft | OK? |
|---:|---|---|---|---|
| 1 | Why does ice float on water? | answer |  |  |
| 2 | Why is the sky blue? | answer |  |  |
| 3 | When would I use a tuple instead of a list in Python? | answer |  |  |
| 4 | How does compound interest differ from simple interest? | answer |  |  |
| 5 | Why do we have leap years? | answer |  |  |
| 6 | How is climate different from the weather on a given day? | answer |  |  |
| 7 | How does a capacitor store energy? | answer |  |  |
| 8 | Why does a recursive function need a base case? | answer |  |  |
| 9 | Can a measurement be precise but not accurate? | answer |  |  |
| 10 | How does a hash function help with password storage? | answer | expert 0.1 |  |
| 11 | Why do batteries lose capacity in the cold? | answer |  |  |
| 12 | What does a p-value tell you? | answer |  |  |
| 13 | Explain the difference between a virus and a bacterium. | answer |  |  |
| 14 | Why is it harder to cool a room than to heat it? | answer | expert 0.15 |  |
| 15 | What does the S in HTTPS add? | answer |  |  |
| 16 | How do noise-cancelling headphones work? | answer |  |  |
| 17 | Why does a ball curve when it spins? | answer |  |  |
| 18 | What's the difference between median and mean, and when does it matter? | answer |  |  |
| 19 | How does a binary search work? | answer |  |  |
| 20 | Why does time zone math get confusing around daylight saving changes? | answer | expert 0.1 |  |
| 21 | What makes a good password? | answer |  |  |
| 22 | Why is the median house price more useful than the average? | answer |  |  |
| 23 | How does a transistor act as a switch? | answer |  |  |
| 24 | Should a home NAS use RAID 0 or RAID 1, and why? | answer |  |  |
| 25 | Why do planes fly at around 35,000 feet? | answer |  |  |
| 26 | What's the point of version control? | answer |  |  |
| 27 | How do vaccines train the immune system? | answer |  |  |
| 28 | Why does a Bluetooth connection drop through walls? | answer |  |  |
| 29 | Why is a loan's APR higher than its interest rate? | answer |  |  |
| 30 | Is it a bad idea to run a lithium battery down to 0% regularly? Why? | answer | expert 0.15 |  |

## word problems that take a step or two: answer + calculate (12)

| # | Request | Labels | Soft | OK? |
|---:|---|---|---|---|
| 31 | If I read 30 pages a day, how long will a 450-page book take? | answer+calculate |  |  |
| 32 | Is a 20% discount followed by another 10% the same as 30% off? | answer+calculate | expert 0.1 |  |
| 33 | Which is cheaper per litre: 2 litres for $3.20 or 3 litres for $4.50? | answer+calculate |  |  |
| 34 | If a meeting starts at 9:40 and runs 95 minutes, when does it end? | answer+calculate |  |  |
| 35 | How much do I save a year by cutting a $14 monthly subscription? | answer+calculate |  |  |
| 36 | A recipe for 4 needs 300 g of flour. How much for 6 people? | answer+calculate |  |  |
| 37 | If my phone loses 8% charge an hour, how long until it's at 20% from 100%? | answer+calculate |  |  |
| 38 | Driving 240 km at 80 km/h, how long will it take with a 20-minute stop? | answer+calculate |  |  |
| 39 | Is paying $1,200 a year better than $110 a month? | answer+calculate |  |  |
| 40 | What's the tip on a $64 bill at 15%, rounded up to the nearest dollar? | answer+calculate |  |  |
| 41 | Three people split a $90 cab and one pays $10 extra. What do the others pay? | answer+calculate |  |  |
| 42 | If a file downloads at 12 MB/s, how long does a 3 GB file take? | answer+calculate |  |  |

## a short inference from one note: lookup (25)

| # | Request | Labels | Soft | OK? |
|---:|---|---|---|---|
| 43 | Does my lease note say I can have pets? | lookup |  |  |
| 44 | According to my car note, which service is due next? | lookup |  |  |
| 45 | Is my passport still valid for the trip, per my travel note? | lookup | calculate 0.3 |  |
| 46 | Does my packing list include anything for rain? | lookup |  |  |
| 47 | Based on my recipe note, is the chili vegetarian? | lookup |  |  |
| 48 | Does my insurance note say I'm covered for water damage? | lookup |  |  |
| 49 | Is the venue in my offsite note wheelchair accessible? | lookup |  |  |
| 50 | Which option did I lean towards in my laptop comparison note? | lookup | expert 0.1 |  |
| 51 | Does my meeting note with Dana say who owns the follow-up? | lookup |  |  |
| 52 | What result did the soak test end with? | lookup |  |  |
| 53 | What's the main takeaway of my note on the sparse attention paper? | lookup |  |  |
| 54 | From my gym note, which day is leg day? | lookup |  |  |
| 55 | Does my warranty note cover accidental damage? | lookup |  |  |
| 56 | Which supplier does my BOM note say is the backup? | lookup |  |  |
| 57 | Is the Lisbon conference talk in the morning or afternoon, per my note? | lookup |  |  |
| 58 | What reason did I give for picking PostgreSQL in my database note? | lookup |  |  |
| 59 | According to my plant care note, should I water the fern today? | lookup | calculate 0.2 |  |
| 60 | Does my onboarding note say when laptops are handed out? | lookup |  |  |
| 61 | What did the doctor say to avoid, according to my health note? | lookup |  |  |
| 62 | Is the bug in my incident note fixed or just worked around? | lookup | expert 0.1 |  |
| 63 | Which of the two flights in my travel note is the cheaper one? | lookup |  |  |
| 64 | What does my style guide note say about Oxford commas? | lookup |  |  |
| 65 | Who did I say should review the design, in my project kickoff note? | lookup |  |  |
| 66 | Does my contract note mention a notice period? | lookup |  |  |
| 67 | What's the next step listed in my job application note? | lookup |  |  |

## light synthesis across notes: sweep (30)

| # | Request | Labels | Soft | OK? |
|---:|---|---|---|---|
| 68 | Summarise what I decided across this week's meetings. | sweep | expert 0.1 |  |
| 69 | Which of my projects mention the same vendor? | sweep |  |  |
| 70 | Compare what I wrote about the two laptops I'm considering. | sweep | expert 0.2 |  |
| 71 | Group my reading list notes by topic. | sweep |  |  |
| 72 | What themes come up in my journal entries this month? | sweep | expert 0.15, calculate 0.15 |  |
| 73 | Which recipes in my notes use chickpeas? | sweep |  |  |
| 74 | What have I said about Jordan across my meeting notes? | sweep |  |  |
| 75 | Give me a quick overview of my notes on home renovation. | sweep |  |  |
| 76 | Which trips have I written notes about? | sweep |  |  |
| 77 | What are the main points across my notes on sleep? | sweep |  |  |
| 78 | Which of my notes mention both the soak test and the supercap? | sweep |  |  |
| 79 | Collect the tips I've saved about public speaking. | sweep |  |  |
| 80 | What did each of my one-on-ones this month focus on? | sweep |  |  |
| 81 | How do my notes describe the onboarding process, step by step? | sweep |  |  |
| 82 | Pull together everything I know about the Lisbon office. | sweep |  |  |
| 83 | Which of my book notes are about productivity? | sweep |  |  |
| 84 | What questions did people ask in my last three demo notes? | sweep |  |  |
| 85 | Line up what my notes say about each of the three apartment options. | sweep | expert 0.2 |  |
| 86 | Which projects have I tagged as on hold, and what reason did I give for each? | sweep |  |  |
| 87 | Give me the highlights from my conference notes. | sweep |  |  |
| 88 | Which of my notes talk about Raspberry Pi projects? | sweep |  |  |
| 89 | What did I learn from each of the courses in my learning notes? | sweep |  |  |
| 90 | Which people have I noted as experts in firmware? | sweep |  |  |
| 91 | Sort my open tasks by project. | sweep |  |  |
| 92 | What gift ideas have I noted for my family? | sweep |  |  |
| 93 | Which meetings this month had action items for me? | sweep |  |  |
| 94 | Compare my notes from the two vendor calls. | sweep | expert 0.2 |  |
| 95 | What restaurants have friends recommended across my notes? | sweep |  |  |
| 96 | Summarise my notes on the garden by season. | sweep |  |  |
| 97 | What changed in the area monitor project over the last month? | sweep | expert 0.15 |  |

## dates, ages and simple numbers from notes (20)

| # | Request | Labels | Soft | OK? |
|---:|---|---|---|---|
| 98 | How many days since my last run, according to my training log? | lookup+calculate |  |  |
| 99 | How long ago did I last service the car? | lookup+calculate |  |  |
| 100 | How old will the laptop be when the warranty in my note expires? | lookup+calculate |  |  |
| 101 | How many days until my passport expires? | lookup+calculate |  |  |
| 102 | Is my gym membership due for renewal within the next month? | lookup+calculate |  |  |
| 103 | How much more do I need to save to reach the goal in my finance note? | lookup+calculate |  |  |
| 104 | How many items are left on my packing list? | lookup+calculate |  |  |
| 105 | What's the price difference between the two flights in my travel note? | lookup+calculate |  |  |
| 106 | How many hours does the itinerary in my trip note leave free on Saturday? | lookup+calculate |  |  |
| 107 | By how much did the quote in my renovation note go up? | lookup+calculate |  |  |
| 108 | Find notes I haven't edited since March. | sweep+calculate |  |  |
| 109 | Which projects have had no updates in the last two weeks? | sweep+calculate |  |  |
| 110 | How many books have I finished each month this year? | sweep+calculate |  |  |
| 111 | Which topic have I written the most notes about this month? | sweep+calculate | expert 0.1 |  |
| 112 | How long has my oldest open task been waiting? | sweep+calculate |  |  |
| 113 | Which friend haven't I contacted for the longest, according to my notes? | sweep+calculate |  |  |
| 114 | How many meetings did I have with Jordan this quarter? | sweep+calculate |  |  |
| 115 | What share of my tasks this week are work versus personal? | sweep+calculate |  |  |
| 116 | Which week this month did I log the most exercise? | sweep+calculate |  |  |
| 117 | How many days did the longest bug stay open, from my bug notes? | sweep+calculate |  |  |

## light edits that take a little thought (15)

| # | Request | Labels | Soft | OK? |
|---:|---|---|---|---|
| 118 | Remind me on Monday to send the quarterly report. | write | lookup 0.3 |  |
| 119 | Rename my 'misc' note to something that fits its contents. | lookup+write | expert 0.1 |  |
| 120 | Sort my reading list note by priority. | lookup+write |  |  |
| 121 | Reorder the sections in my CV note so the most recent job comes first. | lookup+write |  |  |
| 122 | Split the shopping list in my daily note into groceries and hardware. | lookup+write |  |  |
| 123 | Tidy the bullet points in my meeting note into decisions and actions. | lookup+write |  |  |
| 124 | Move the finished items in my to-do note to a Done section. | lookup+write |  |  |
| 125 | Give my untitled note from yesterday a sensible title. | lookup+write |  |  |
| 126 | Add a short summary at the top of my research note. | lookup+write |  |  |
| 127 | Turn the paragraph of steps in my setup note into a numbered list. | lookup+write |  |  |
| 128 | Mark the overdue tasks in my project note with #late. | lookup+write |  |  |
| 129 | Merge the two duplicate grocery lists in my notes into one. | sweep+write |  |  |
| 130 | Add a tag to each of this week's daily notes for its main project. | sweep+write |  |  |
| 131 | Create a note listing my open tasks grouped by project. | sweep+write |  |  |
| 132 | Write a short status note for Project Lumen from its latest updates. | sweep+write | expert 0.2 |  |
