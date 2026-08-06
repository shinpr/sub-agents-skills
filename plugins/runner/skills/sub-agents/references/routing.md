# Routing: Selecting an Agent by Judgment, Not Keywords

Applies when Step 1 finds 2+ agent definitions and the user has not named one.
Default behavior is to ask the user. This reference describes when it is safe
to select automatically instead, and how.

## Why not keyword matching

Matching words in the prompt against words in agent names/descriptions
(`"design"`, `"UI"`, `"logic"`) breaks on paraphrase ("make this feel nicer"),
non-English phrasing, jargon, and any task that touches more than one concern.
It also silently misroutes instead of failing loud, which is worse than asking.

## What to match instead

Classify the task by its **dominant demand**, then compare that against each
agent's **capability profile** — provided the agent description states one.

Dominant-demand categories (not exhaustive, use judgment):

- **Subjective/aesthetic judgment** — visual design, copy tone, UX feel. No
  single verifiably-correct answer; quality is a matter of taste and
  convention.
- **Deterministic/verifiable correctness** — algorithms, data correctness,
  security-sensitive logic. Has a right answer; a taste-optimized model that
  cuts corners on rigor is the wrong tool.
- **Balanced/exploratory** — investigation, architecture tradeoffs, tasks
  where both matter comparably.

An agent's description is a capability profile if it states what kind of
judgment the backend is tuned for and where it's weak — not what topics it
covers. Compare:

- Keyword-style (avoid): "Handles UI, CSS, design, styling tasks."
- Profile-style (use): "Strong aesthetic/subjective judgment, fast iteration
  on visual variants; not tuned for strict correctness proofs or edge-case
  exhaustiveness."

If existing agent definitions only have keyword-style descriptions, routing
quality degrades to keyword-matching regardless of this procedure — the fix
is to rewrite the descriptions, not to add a classifier on top.

## Selection procedure

1. Classify the task's dominant demand (see categories above).
2. Compare against each available agent's capability profile.
3. One agent clearly dominates → select it, no prompt.
4. Task decomposes into sequential phases with different demands (e.g.
   "design a component, then wire its logic") → run agents in sequence, one
   call per phase, instead of forcing a single pick.
5. Genuinely balanced between two comparably-strong candidates:
   - Task is destructive, expensive, or hard to reverse → ask the user.
   - Otherwise → default to the higher-rigor (correctness-oriented) backend.
     Wrong logic costs more to fix later than suboptimal taste.
6. Never fall back to substring/keyword matching against the raw prompt text.

## Example

```markdown
---
run-agent: kimi-cli
model: kimi-k2
permission: safe-edit
---

# design
Strong aesthetic/subjective judgment: layout, typography, color, motion,
copy tone. Fast iteration on visual variants. Not tuned for strict
correctness proofs, security review, or exhaustive edge-case handling —
route those elsewhere even if they touch UI code.
```

```markdown
---
run-agent: codex
model: gpt-5.1-codex-max
permission: safe-edit
---

# logic
Rigorous step-by-step correctness: algorithms, data integrity, concurrency,
security-sensitive code. Verifies edge cases explicitly. Not tuned for
subjective taste calls — treat its aesthetic opinions as default-only.
```

A task like "the checkout button placement feels off" routes to `design`
(dominant demand: aesthetic judgment) even though it never says a design
keyword and the code it touches is arguably "UI logic." A task like "figure
out why totals round wrong 1 in 10,000 times" routes to `logic` even if the
bug lives in a component styled by `design`.
