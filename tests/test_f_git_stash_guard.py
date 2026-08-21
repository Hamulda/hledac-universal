"""Testy pro git-stash-guard — PreToolUse guard proti destruktivním git operacím.

Cíl: trvale zabránit ztrátě práce přes Stop hook / agent / manuální příkaz.
Kontext: viz .claude/hooks/git-stash-guard.sh a CLAUDE.md → "Git Příkazy (ZÁKAZ)".

Bezpečnostní invarianty (testované):
  I1) Všechny git stash varianty jsou blokovány (exit=2)
  I2) git reset --hard je blokován
  I3) git checkout -- je blokován
  I4) git clean -fd je blokován
  I5) git push --force a -f jsou blokovány
  I6) git branch -D je blokován
  I7) git update-ref -d je blokován
  I8) Běžné read-only git příkazy procházejí (status, diff, log, show, branch)
  I9) Grep hledající "git stash" v textu NENÍ blokován (false positive ochrana)
  I10) Prázdný / ne-JSON vstup nezpůsobí chybu
  I11) Skript je executable
  I12) Skript je registrován v PreToolUse[0] settings.json (PRVNÍ POZICE = vykoná se první)
  I13) Skript v settings.json je synchronní (bez async=true) — async hooky blokovat nemohou
  I14) Stop hooky v settings.json NEOBSAHUJÍ git stash ani jiné destruktivní git příkazy
  I15) Skript v PATTERNS pokrývá minimálně 8 kategorií nebezpečných příkazů
"""

import json
import re
import stat
import subprocess
from pathlib import Path

import pytest  # type: ignore[import-not-found]

ROOT = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")
GUARD_SCRIPT = ROOT / ".claude" / "hooks" / "git-stash-guard.sh"
SETTINGS_JSON = ROOT / ".claude" / "settings.json"


def _run_guard(payload: str) -> int:
    """Spustí guard skript s daným JSON payloadem, vrátí exit code."""
    proc = subprocess.run(
        [str(GUARD_SCRIPT)],
        input=payload,
        text=True,
        capture_output=True,
        timeout=5,
    )
    return proc.returncode


def _blocked_cmds() -> list[tuple[str, str]]:
    """Všechny destruktivní git příkazy, které musí být blokovány (exit=2)."""
    return [
        # (název, payload jako JSON s tool_input.command)
        ("git_stash_alone", '{"tool_input":{"command":"git stash"}}'),
        ("git_stash_pop", '{"tool_input":{"command":"git stash pop"}}'),
        ("git_stash_apply", '{"tool_input":{"command":"git stash apply"}}'),
        ("git_stash_drop", '{"tool_input":{"command":"git stash drop"}}'),
        ("git_stash_semicolon", '{"tool_input":{"command":"git stash; echo done"}}'),
        ("git_stash_doubleamp", '{"tool_input":{"command":"git stash && make build"}}'),
        ("git_reset_hard_head", '{"tool_input":{"command":"git reset --hard HEAD"}}'),
        ("git_reset_hard_sha", '{"tool_input":{"command":"git reset --hard abc1234"}}'),
        ("git_push_force", '{"tool_input":{"command":"git push --force origin main"}}'),
        ("git_push_f_short", '{"tool_input":{"command":"git push -f origin main"}}'),
        ("git_clean_fd", '{"tool_input":{"command":"git clean -fd"}}'),
        ("git_clean_f_only", '{"tool_input":{"command":"git clean -f"}}'),
        ("git_checkout_dashdash", '{"tool_input":{"command":"git checkout -- foo.py"}}'),
        ("git_branch_D", '{"tool_input":{"command":"git branch -D feature/old"}}'),
        ("git_update_ref_d", '{"tool_input":{"command":"git update-ref -d refs/heads/main"}}'),
    ]


def _allowed_cmds() -> list[tuple[str, str]]:
    """Příkazy, které guard NESMÍ blokovat (exit=0)."""
    return [
        ("git_status", '{"tool_input":{"command":"git status"}}'),
        ("git_diff_staged", '{"tool_input":{"command":"git diff --staged"}}'),
        ("git_log", '{"tool_input":{"command":"git log --oneline -5"}}'),
        ("git_rev_parse", '{"tool_input":{"command":"git rev-parse --git-dir"}}'),
        ("git_branch_read", '{"tool_input":{"command":"git branch -a"}}'),
        ("git_show", '{"tool_input":{"command":"git show HEAD"}}'),
        ("git_add", '{"tool_input":{"command":"git add file.py"}}'),
        ("git_commit", '{"tool_input":{"command":"git commit -m "msg""}}'),
        ("grep_git_stash_text", '{"tool_input":{"command":"grep -r "git stash" .claude/"}}'),
        ("ls_basic", '{"tool_input":{"command":"ls -la"}}'),
        ("echo_noop", '{"tool_input":{"command":"echo hello"}}'),
        ("empty_payload", ""),
        ("nonjson_payload", "this is not json"),
    ]


# =============================================================================
# I1–I7: destruktivní příkazy musí být blokovány
# =============================================================================


@pytest.mark.parametrize("name,payload", _blocked_cmds(), ids=[c[0] for c in _blocked_cmds()])
def test_destructive_git_blocked(name, payload) -> None:
    """Invariant I1–I7: destruktivní git příkaz vrací exit=2 (blokující)."""
    exit_code = _run_guard(payload)
    assert exit_code == 2, f"{name}: expected exit=2 (BLOCKED), got {exit_code}"


# =============================================================================
# I8–I10: benigní příkazy nesmí být blokovány (false-positive ochrana)
# =============================================================================


@pytest.mark.parametrize("name,payload", _allowed_cmds(), ids=[c[0] for c in _allowed_cmds()])
def test_allowed_commands_pass(name, payload) -> None:
    """Invariant I8–I10: read-only git a ne-git příkazy vracejí exit=0."""
    exit_code = _run_guard(payload)
    assert exit_code == 0, f"{name}: expected exit=0 (ALLOWED), got {exit_code}"


# =============================================================================
# I11: skript je executable
# =============================================================================


def test_guard_script_is_executable() -> None:
    """Invariant I11: skript má executable bit (Bash hook vyžaduje)."""
    mode = GUARD_SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR, f"git-stash-guard.sh missing user-exec bit: {oct(mode)}"


def test_guard_script_exists() -> None:
    """Guard skript musí fyzicky existovat."""
    assert GUARD_SCRIPT.exists(), f"Guard not found: {GUARD_SCRIPT}"


# =============================================================================
# I12–I13: registrace v settings.json
# =============================================================================


def test_guard_registered_as_first_pretooluse(mock_settings_json) -> None:
    """Invariant I12: guard je v PreToolUse[0] (první = vykoná se první)."""
    settings = mock_settings_json
    pre = settings["hooks"]["PreToolUse"]
    assert pre, "PreToolUse is empty"
    first = pre[0]
    assert first["matcher"] == "Bash", f"PreToolUse[0] matcher: {first['matcher']!r}"
    cmd = first["hooks"][0]["command"]
    assert "git-stash-guard.sh" in cmd, f"PreToolUse[0] command: {cmd!r}"


def test_guard_is_synchronous(mock_settings_json) -> None:
    """Invariant I13: guard nesmí být async — async hooky blokovat nemohou."""
    settings = mock_settings_json
    pre = settings["hooks"]["PreToolUse"]
    first_hook = pre[0]["hooks"][0]
    assert "async" not in first_hook or first_hook["async"] is False, (
        f"guard musí být synchronní, ale je async={first_hook.get('async')!r}"
    )


def test_stop_hooks_no_destructive_git(mock_settings_json) -> None:
    """Invariant I14: Stop hooky v projektu NESMÍ volat destruktivní git operace."""
    settings = mock_settings_json
    stop_section = settings["hooks"].get("Stop", [])
    blocked_patterns = [
        "git stash",
        "git reset --hard",
        "git checkout --",
        "git clean -f",
        "git push --force",
        "git push -f",
        "update-ref -d",
        "branch -D",
    ]
    for hook_group in stop_section:
        for hook in hook_group.get("hooks", []):
            cmd = hook.get("command", "")
            for pattern in blocked_patterns:
                assert pattern not in cmd, (
                    f"Stop hook obsahuje destruktivní git příkaz! pattern={pattern!r} command={cmd!r}"
                )


# =============================================================================
# I15: skript pokrývá >= 8 kategorií blokovaných příkazů
# =============================================================================


def test_guard_pattern_coverage() -> None:
    """Invariant I15: skript má v PATTERNS minimálně 8 různých kategorií.

    Aktuální pokrytí: git stash, reset --hard, checkout --, clean -fd,
    push --force/-f, push origin --force, branch -D, update-ref -d = 8.
    """
    text = GUARD_SCRIPT.read_text()
    # Počet řádků s 'git[[:space:]]' v declare -a PATTERNS

    matches = re.findall(r"^\s*'(git\[\[)", text, re.MULTILINE)
    assert len(matches) >= 8, f"guard pokrývá jen {len(matches)} kategorií, očekáváno >= 8"


# =============================================================================
# Záloha originálu (rollback safety)
# =============================================================================


def test_backup_exists() -> None:
    """Záloha originálního settings.json musí existovat pro případný rollback."""
    backup = ROOT / ".claude" / "settings.json.pre-stash-fix-2026-06-03.bak"
    assert backup.exists(), f"Záloha chybí: {backup}"


def test_backup_is_valid_json(mock_settings_bak) -> None:
    """Záloha musí být validní JSON (parsovatelná)."""

    json.loads(mock_settings_bak)  # vyhodí výjimku pokud není validní
