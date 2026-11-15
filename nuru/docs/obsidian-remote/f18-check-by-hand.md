# F18 — Check by hand

Use an isolated test vault on macOS and Windows. These checks are pending, not performed by the implementation tests.

- Send a question. When a model call finishes, the ring beside the model/thinking controls fills in the theme accent colour. Its tooltip and accessible text show “About … of … tokens”. Check zero (grey track with no accent), half (half accent, half grey), full and overfull (full accent circle, unclipped count). This is last-call usage, not cumulative billing or a guarantee that the next call fits. Local prompt size is estimated; Claude input includes cached tokens once.
- Start a second model call through a tool. During it, any previous value is subdued and labelled “Last call”; missing usage shows no made-up zero. A new turn/conversation/model must never inherit another turn's meter.
- With a small configured window, ask a question after enough old exchanges and retrieve a large tool result. Verify the notice names older messages and tool results actually omitted, suggests a new conversation, and does not start one. Confirm original history and vault notes remain intact.
- Reopen the conversation and reconnect during a turn. The meter and sticky omission notice survive replay and saved history without duplicating. Older saved conversations without context still open normally.
- Check Stop, offline/error, manual model switches, phone/remote viewing, draft text, caret selection and copied reply text. The ring and per-turn omission notice are outside the reply and do not redraw the composer. A model picker change must clear readings belonging to another model. F40 adds the ring’s click-to-size control in its separate PR. Check keyboard navigation and light/dark theme.
- A full-looking estimate alone must not produce a claim that the server cut content. Existing refusal of oversized requests stays in place.

- In light and dark themes, compare the unused ring track (`--interactive-normal`) with the inactive Send button. Check the accent remains clear against the grey track. Capture the real Obsidian view for the user; note any theme override or contrast mismatch before approval.
