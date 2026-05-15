# AGENTS (universal scope)

## HARD SCOPE
- Work ONLY inside: /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal
- Never read/edit/write outside.
- If info outside scope is needed: ask to expand scope.

## Evidence discipline
- Before edits: rg/fd/read for context.
- After edits: ruff + show diff (diff -u | delta).

@RTK.md

<!-- lean-ctx-compression -->
OUTPUT STYLE: dense
- Each statement = one atomic fact line
- Use abbreviations: fn, cfg, impl, deps, req, res, ctx, err, ret
- Diff lines only (+/-/~), never repeat unchanged code
- Symbols: → (causes), + (adds), − (removes), ~ (modifies), ∴ (therefore)
- No narration, no filler, no hedging
- BUDGET: ≤200 tokens per response unless code block required
<!-- /lean-ctx-compression -->

<!-- lean-ctx -->
## lean-ctx

Prefer lean-ctx MCP tools over native equivalents for token savings.
Full rules: @LEAN-CTX.md
<!-- /lean-ctx -->
