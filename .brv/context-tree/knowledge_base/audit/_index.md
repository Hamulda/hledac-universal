---
children_hash: 2c6503e869c8e3d566a1194edf31f954b2ba715c39d77b55e5a7167fe4a0054d
compression_ratio: 0.34
condensation_order: 1
covers: [context.md, kb_audit_2026_07_11.md]
covers_token_total: 550
summary_level: d1
token_count: 187
type: summary
---
<think>
The user wants me to create a more concise structural summary of the audit-related knowledge entries. I need to aggressively compress while keeping critical facts.
</think>

# audit

Documents knowledge base audit findings and gap analysis for content maintenance.

## Key Findings (2026-07-11)
Four areas require updates:
1. **Exit codes tests** - references outdated 100-test sprint setup; actual: tests/test_exit_codes.py + smoke_runner.py
2. **Naming/conventions** - docs missing; content only in CLAUDE.md
3. **Integrations** - no dedicated doc; info scattered
4. **probe_tests** - referenced in context but empty

## Related
- `facts/project/technology_stack.md` - partial info exists here
- `testing/exit_codes/` - test coverage area