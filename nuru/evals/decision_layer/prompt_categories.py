"""The prompt-categorisation set: seven categories, and 50 prompts with the categories each needs.

The categories' definitions are shared word for word by both contenders (Laya's options, the 3B's
system prompt). Each category has 6 prompts of its own and 8 borderline prompts at the end need two.
A write to a note that already exists needs `lookup` too (decided 2026-10-01), so 3 of the write
prompts carry two labels; a new note, or today's daily note (found by date), is `write` alone.
The prompts are never resolved - only sorted. See categorisation-notes.md.
"""

CATEGORIES = {
    "answer": "Reply directly; the user's notes aren't needed",
    "lookup": "Find one specific note or fact in the user's notes",
    "sweep": "Look across many notes: list, audit, compare or summarise a set",
    "calculate": "Arithmetic or statistics, on numbers from the notes or the request",
    "write": "Create, add to or change a note",
    "expert": "Deeper reasoning or planning than a small local model can do",
    "clarify": "Too unclear to act on; ask the user",
}

# (prompt, the categories it needs)
PROMPTS = [
    # answer
    ("What's the difference between a mutex and a semaphore?", {"answer"}),
    ("Give me a synonym for 'resilient'.", {"answer"}),
    ("How do I write a Python for loop that skips even numbers?", {"answer"}),
    ("Thanks, that's all for now.", {"answer"}),
    ("What does SPI stand for?", {"answer"}),
    ("Explain what a frontmatter block in a markdown file is.", {"answer"}),
    # lookup
    ("What did I write about the Berlin trip?", {"lookup"}),
    ("What's Priya's role on the team?", {"lookup"}),
    ("When is the soak test supposed to finish, according to my notes?", {"lookup"}),
    ("Find my note on the area monitor redesign.", {"lookup"}),
    ("What lead time did the vendor quote for the SARA-R510S?", {"lookup"}),
    ("What did we decide about the supercap in yesterday's standup?", {"lookup"}),
    # sweep
    ("Which of my project notes don't have a status tag?", {"sweep"}),
    ("List every note that mentions FreeRTOS.", {"sweep"}),
    ("Summarise all my daily notes from last week.", {"sweep"}),
    ("Are there any broken links in my vault?", {"sweep"}),
    ("What open todos do I have across all my notes?", {"sweep"}),
    ("Which people notes haven't been updated in over a month?", {"sweep"}),
    # calculate
    ("A 2,400 mAh battery and an average draw of 18 mA: how many days does it last?", {"calculate"}),
    ("What's the mean and standard deviation of 12, 15, 11, 19 and 14?", {"calculate"}),
    ("Split a $186 dinner bill between 4 people, with an 18% tip.", {"calculate"}),
    ("How many weeks are there between 3 March and 20 October?", {"calculate"}),
    ("A 20% price rise followed by a 15% discount: what's the net change?", {"calculate"}),
    ("Convert 72°F to Celsius.", {"calculate"}),
    # write (a write to an existing note needs a lookup first)
    ("Add 'buy milk' to today's daily note.", {"write"}),
    ("Create a new note called 'Ideas for the offsite'.", {"write"}),
    ("Add a line to Priya's note saying she prefers async messages on Fridays.", {"lookup", "write"}),
    ("Rename the tag #wip to #in-progress in my reading list note.", {"lookup", "write"}),
    ("Create a transcript of today's standup: we discussed the soak test and the supercap.", {"write"}),
    ("Mark the 'order parts' todo in my area monitor note as done.", {"lookup", "write"}),
    # expert
    ("Design a power-management strategy for a battery IoT sensor that wakes every 10 minutes over LTE-M.",
     {"expert"}),
    ("What are the trade-offs between rate-monotonic and earliest-deadline-first scheduling for a hard "
     "real-time system?", {"expert"}),
    ("Prove, step by step, that the halting problem is undecidable.", {"expert"}),
    ("Draft a migration plan from a monolith to microservices for a 5-person team, with risks and milestones.",
     {"expert"}),
    ("Derive the Kalman filter update equations for a 2-state position and velocity model.", {"expert"}),
    ("Should I use Rust or C for a new safety-critical firmware project? Weigh certification, tooling and "
     "team skills.", {"expert"}),
    # clarify
    ("Fix that thing from before.", {"clarify"}),
    ("Do it again.", {"clarify"}),
    ("Change it to the other one.", {"clarify"}),
    ("Can you update the note?", {"clarify"}),
    ("What about him?", {"clarify"}),
    ("Make it better.", {"clarify"}),
    # borderline: two categories each
    ("What's the average of the run times in my training log?", {"lookup", "calculate"}),
    ("Summarise my FreeRTOS notes into a new note.", {"sweep", "write"}),
    ("Review the task design in my FreeRTOS note and suggest a better scheduler.", {"lookup", "expert"}),
    ("Add up all the expenses I logged this month across my daily notes.", {"sweep", "calculate"}),
    ("Look up the SARA-R510S lead time and add it to the area monitor note.", {"lookup", "write"}),
    ("Add a note about it.", {"write", "clarify"}),
    ("Go through all my project notes and propose a priority order for next quarter.", {"sweep", "expert"}),
    ("Explain what a Kalman filter is and save the explanation as a new note.", {"answer", "write"}),
]

assert len(PROMPTS) == 50 and all(labels <= set(CATEGORIES) for _, labels in PROMPTS)
