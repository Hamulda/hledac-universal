# universal Karpathy Wiki Index

This karpathy wiki is the structured knowledge layer for `universal`.

## Core Loop

- ingest: move new source material into `raw/`, summarize it, and connect it into the wiki
- query: answer from the wiki first, then deepen or repair pages if the answer exposed gaps
- lint: clean contradictions, weak links, stale summaries, and missing source attribution

## Core Pages

- [[overview]] - High-level summary of the project, goals, and architecture.
- [[schema]] - Rules for how the wiki should be maintained.
- [[log]] - Chronological ingest/query/lint history.

## Collections

- `entities/` - People, modules, services, products, repos, owners.
- `concepts/` - Themes, systems, protocols, domain ideas.
- `sources/` - Processed source summaries linked back to raw inputs.
- `decisions/` - Key technical and product decisions.

## Maintenance Rules

- Add new raw materials to `raw/`.
- Summaries and synthesized notes belong in `wiki/`.
- Update this index whenever a new durable page is created.
- Cross-link pages with markdown links so graph view stays useful.
