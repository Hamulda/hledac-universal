# universal Karpathy Wiki Schema

This file defines how the karpathy wiki for this project should be maintained.

## Layering

- `raw/` contains immutable source material or lightly cleaned source captures.
- `wiki/` contains LLM-maintained summaries, syntheses, indexes, and decisions.
- The wiki should summarize and connect knowledge, not duplicate raw sources unnecessarily.

## Page Rules

- Prefer one topic per page.
- Use clear H1 titles.
- Add short summaries near the top.
- Link related pages with markdown links.
- When new evidence changes an older conclusion, update both the destination page and `log.md`.

## Recommended Workflows

1. Ingest a new source from `raw/`.
2. Create or update a source summary page in `wiki/sources/`.
3. Update `overview.md`, related entity/concept pages, and `index.md`.
4. Append a short entry to `log.md`.

## Query / Repair Rules

- Start from the wiki before scanning raw material again.
- If a question reveals a missing page, create it.
- If a question reveals stale or contradictory knowledge, fix the page and log the repair.
- Prefer small durable updates over one giant catch-all note.

## Quality Checks

- Orphan pages with no useful incoming links
- Contradictory claims that need reconciliation
- Missing source attribution
- Pages that should be split because they cover multiple topics
