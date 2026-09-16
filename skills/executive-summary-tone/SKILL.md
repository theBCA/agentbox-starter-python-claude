---
name: executive-summary-tone
description: Write summaries in a crisp, formal executive-briefing tone (no hedging, no filler, lead with the conclusion).
---

# Executive Summary Tone

When summarizing a document, prefer:

- Leading with the conclusion or decision, not the background.
- Short, declarative sentences. No hedging ("might", "could possibly").
- Action items as direct imperatives ("Schedule the review", not "It might be
  worth scheduling a review").

## Mandatory output format

Every summary MUST begin with the exact prefix `[EXEC]` followed by a single
space. This applies to all summaries and is not optional.

That prefix is how you can tell the skill actually reached the model. Nothing
in the application's code produces it: AgentBox scans this skill, approves it,
mounts it read-only under `AGENTBOX_SKILLS_ROOT`, and the app folds it into its
system instructions. If a summary comes back without `[EXEC]`, the skill did
not get through — which is exactly the failure worth being able to see.
