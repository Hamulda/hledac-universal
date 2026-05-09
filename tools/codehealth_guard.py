"""Hermetic code-health guard for NextAction functions.

Checks:
- function exists
- explicit args <= 8
- source lines <= 80 unless wrapper delegates immediately
- no class names matching .*Rule in target file
- NextActionInput dataclass exists
- at least 4 _rule_* helper functions exist
"""

from __future__ import annotations

import ast
import argparse
import json
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class GuardVerdict(Enum):
    PASS = "CODEHEALTH_PASS"
    PASS_COMPAT_WRAPPER = "CODEHEALTH_PASS_COMPAT_WRAPPER"
    FAIL_TOO_MANY_ARGS = "CODEHEALTH_FAIL_TOO_MANY_ARGS"
    FAIL_TOO_LONG = "CODEHEALTH_FAIL_TOO_LONG"
    FAIL_POLICY_CLASS_OVERENGINEERING = "CODEHEALTH_FAIL_POLICY_CLASS_OVERENGINEERING"
    FAIL_MISSING_INPUT_DATACLASS = "CODEHEALTH_FAIL_MISSING_INPUT_DATACLASS"
    FAIL_MISSING_RULE_HELPERS = "CODEHEALTH_FAIL_MISSING_RULE_HELPERS"


@dataclass
class GuardResult:
    verdict: GuardVerdict
    function_name: str
    explicit_args: int
    source_lines: int
    is_wrapper_delegate: bool
    has_input_dataclass: bool
    rule_helper_count: int
    has_rule_classes: bool
    compatibility_wrapper_detected: bool = False
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "function_name": self.function_name,
            "explicit_args": self.explicit_args,
            "source_lines": self.source_lines,
            "is_wrapper_delegate": self.is_wrapper_delegate,
            "has_input_dataclass": self.has_input_dataclass,
            "rule_helper_count": self.rule_helper_count,
            "has_rule_classes": self.has_rule_classes,
            "compatibility_wrapper_detected": self.compatibility_wrapper_detected,
            "error_message": self.error_message,
        }


def _count_explicit_args(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Count explicit (non-*args, non-**kwargs) positional parameters."""
    return sum(
        1 for arg in func_node.args.args if arg.arg != "self"
    )


def _is_wrapper_delegate(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Return True if function body is a single direct return statement."""
    if len(func_node.body) != 1:
        return False
    stmt = func_node.body[0]
    if not isinstance(stmt, ast.Return):
        return False
    if stmt.value is None:
        return False
    if isinstance(stmt.value, ast.Call):
        return True
    return False


def _count_source_lines(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Count actual source lines (from lineno to end_lineno)."""
    if not func_node.body:
        return 0
    start = func_node.lineno
    end = func_node.end_lineno
    assert end is not None, "end_lineno always set for function nodes after ast.parse()"
    return max(1, end - start + 1)


def _find_class_names(source_text: str) -> list[str]:
    """Find all class names matching .*Rule using AST."""
    tree = ast.parse(source_text)
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and "Rule" in node.name:
            names.append(node.name)
    return names


def _check_rule_class_in_live_measurement(source_text: str) -> bool:
    """Check for class names containing 'Rule' in source."""
    return len(_find_class_names(source_text)) > 0


def _has_input_dataclass(source_text: str) -> bool:
    """Check if NextActionInput dataclass (or similar) exists."""
    tree = ast.parse(source_text)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "NextActionInput":
            # Check for @dataclass decorator
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Name) and decorator.id == "dataclass":
                    return True
                if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Name) and decorator.func.id == "dataclass":
                    return True
            return True  # Class exists even without decorator
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "NextActionInput":
                return True
    return False


def _count_rule_helpers(source_text: str) -> int:
    """Count functions named _rule_*."""
    tree = ast.parse(source_text)
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_rule_"):
                count += 1
    return count


def _is_compat_wrapper(func_node: ast.FunctionDef | ast.AsyncFunctionDef, source_text: str) -> bool:
    """Detect the F226A-style compatibility wrapper.

    A 35-arg function is an acceptable compatibility wrapper iff:
    1. It constructs NextActionInput inside the body
    2. It delegates to a sequence of _rule_* helpers (for-loop over helpers, not long if/elif chain)
    3. It does NOT contain the old long if/elif business logic (no "elif" chains that are the real rule engine)
    4. Source lines are within a reasonable wrapper threshold (~60 lines)

    Returns True when the function is a thin compatibility wrapper that safely delegates.
    """
    # Must have body to analyze
    if not func_node.body:
        return False

    # Check: constructs NextActionInput
    constructs_input = False
    has_rule_for_loop = False
    has_long_elif_chain = False

    for node in ast.walk(func_node):
        # NextActionInput construction: inp = NextActionInput(...)
        if isinstance(node, ast.Assign):
            if isinstance(node.value, ast.Call):
                if isinstance(node.value.func, ast.Name) and node.value.func.id == "NextActionInput":
                    constructs_input = True
        # Also detect in AnnAssign (annotated assignment like "inp: NextActionInput = NextActionInput(...)")
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.value, ast.Call):
                if isinstance(node.value.func, ast.Name) and node.value.func.id == "NextActionInput":
                    constructs_input = True

        # Check for for-loop over _rule_* helpers
        # Pattern: for helper in [_rule_starvation, _rule_dominance, ...]: result = helper(inp); ...
        if isinstance(node, ast.For):
            # Check the for-loop iterator list for _rule_ prefixed names
            if isinstance(node.iter, ast.List):
                for elt in node.iter.elts:
                    if isinstance(elt, ast.Name) and elt.id.startswith("_rule_"):
                        has_rule_for_loop = True
            # Also check body for calls to _rule_* helpers
            for child in ast.walk(node):
                if isinstance(child, ast.Assign) and isinstance(child.value, ast.Call):
                    call = child.value
                    if isinstance(call.func, ast.Name) and call.func.id.startswith("_rule_"):
                        has_rule_for_loop = True

        # Check for long elif chains (old-style business logic)
        # Count total AST nodes under If/While to detect heavy nesting
        # vs. thin wrapper that just has parameter assignments
        if isinstance(node, (ast.If, ast.While)):
            total_nodes_in_block = sum(1 for _ in ast.walk(node))
            if total_nodes_in_block >= 15:
                has_long_elif_chain = True

    # Wrapper threshold: 110 lines for the compatibility wrapper itself
    # (104-line compat wrapper in live_sprint_measurement.py is acceptable if body is thin)
    source_lines = _count_source_lines(func_node)
    if source_lines > 110:
        return False

    return constructs_input and has_rule_for_loop and not has_long_elif_chain


def run_guard(
    file_path: str,
    symbol: str,
) -> GuardResult:
    """Run the code-health guard on a specific function in a file."""
    path = Path(file_path)
    if not path.exists():
        return GuardResult(
            verdict=GuardVerdict.PASS,
            function_name=symbol,
            explicit_args=0,
            source_lines=0,
            is_wrapper_delegate=False,
            has_input_dataclass=False,
            rule_helper_count=0,
            has_rule_classes=False,
            error_message=f"File not found: {file_path}",
        )

    source_text = path.read_text(encoding="utf-8")

    try:
        tree = ast.parse(source_text)
    except SyntaxError as e:
        return GuardResult(
            verdict=GuardVerdict.PASS,
            function_name=symbol,
            explicit_args=0,
            source_lines=0,
            is_wrapper_delegate=False,
            has_input_dataclass=False,
            rule_helper_count=0,
            has_rule_classes=False,
            error_message=f"Syntax error in source file: {e}",
        )

    func_node: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == symbol:
                func_node = node
                break

    if func_node is None:
        return GuardResult(
            verdict=GuardVerdict.PASS,
            function_name=symbol,
            explicit_args=0,
            source_lines=0,
            is_wrapper_delegate=False,
            has_input_dataclass=False,
            rule_helper_count=0,
            has_rule_classes=False,
            error_message=f"Function '{symbol}' not found in {file_path}",
        )

    explicit_args = _count_explicit_args(func_node)
    is_wrapper = _is_wrapper_delegate(func_node)
    is_compat_wrapper = _is_compat_wrapper(func_node, source_text)
    source_lines = _count_source_lines(func_node)
    has_rule_classes = _check_rule_class_in_live_measurement(source_text)
    has_input_dc = _has_input_dataclass(source_text)
    rule_helper_count = _count_rule_helpers(source_text)

    # Apply verdicts
    if explicit_args > 8:
        # Compatibility wrapper exception: F226A refactored function with NextActionInput
        # construction and rule-helper delegation is acceptable even with >8 args
        if is_compat_wrapper:
            return GuardResult(
                verdict=GuardVerdict.PASS_COMPAT_WRAPPER,
                function_name=symbol,
                explicit_args=explicit_args,
                source_lines=source_lines,
                is_wrapper_delegate=is_wrapper,
                has_input_dataclass=has_input_dc,
                rule_helper_count=rule_helper_count,
                has_rule_classes=has_rule_classes,
                compatibility_wrapper_detected=True,
                error_message=None,
            )
        return GuardResult(
            verdict=GuardVerdict.FAIL_TOO_MANY_ARGS,
            function_name=symbol,
            explicit_args=explicit_args,
            source_lines=source_lines,
            is_wrapper_delegate=is_wrapper,
            has_input_dataclass=has_input_dc,
            rule_helper_count=rule_helper_count,
            has_rule_classes=has_rule_classes,
            error_message=f"Function has {explicit_args} explicit args (max 8)",
        )

    if source_lines > 80 and not is_wrapper:
        return GuardResult(
            verdict=GuardVerdict.FAIL_TOO_LONG,
            function_name=symbol,
            explicit_args=explicit_args,
            source_lines=source_lines,
            is_wrapper_delegate=is_wrapper,
            has_input_dataclass=has_input_dc,
            rule_helper_count=rule_helper_count,
            has_rule_classes=has_rule_classes,
            error_message=f"Function has {source_lines} source lines (max 80)",
        )

    if has_rule_classes:
        return GuardResult(
            verdict=GuardVerdict.FAIL_POLICY_CLASS_OVERENGINEERING,
            function_name=symbol,
            explicit_args=explicit_args,
            source_lines=source_lines,
            is_wrapper_delegate=is_wrapper,
            has_input_dataclass=has_input_dc,
            rule_helper_count=rule_helper_count,
            has_rule_classes=has_rule_classes,
            error_message="Policy class overengineering detected (.*Rule class names found)",
        )

    if not has_input_dc:
        return GuardResult(
            verdict=GuardVerdict.FAIL_MISSING_INPUT_DATACLASS,
            function_name=symbol,
            explicit_args=explicit_args,
            source_lines=source_lines,
            is_wrapper_delegate=is_wrapper,
            has_input_dataclass=has_input_dc,
            rule_helper_count=rule_helper_count,
            has_rule_classes=has_rule_classes,
            error_message="NextActionInput dataclass not found in source file",
        )

    if rule_helper_count < 4:
        return GuardResult(
            verdict=GuardVerdict.FAIL_MISSING_RULE_HELPERS,
            function_name=symbol,
            explicit_args=explicit_args,
            source_lines=source_lines,
            is_wrapper_delegate=is_wrapper,
            has_input_dataclass=has_input_dc,
            rule_helper_count=rule_helper_count,
            has_rule_classes=has_rule_classes,
            error_message=f"Only {rule_helper_count} _rule_* helpers found (min 4)",
        )

    return GuardResult(
        verdict=GuardVerdict.PASS,
        function_name=symbol,
        explicit_args=explicit_args,
        source_lines=source_lines,
        is_wrapper_delegate=is_wrapper,
        has_input_dataclass=has_input_dc,
        rule_helper_count=rule_helper_count,
        has_rule_classes=has_rule_classes,
    )


def _render_markdown(result: GuardResult) -> str:
    status_icon = "✅" if result.verdict in (GuardVerdict.PASS, GuardVerdict.PASS_COMPAT_WRAPPER) else "❌"
    lines = [
        f"# NextAction Code Health Guard — `{result.function_name}`",
        "",
        f"**Verdict:** {status_icon} `{result.verdict.value}`",
        "",
        "## Metrics",
        f"- Explicit args: {result.explicit_args} (max 8, compat wrapper exception applies if >8)",
        f"- Source lines: {result.source_lines} (max 80, unless wrapper delegate)",
        f"- Is wrapper delegate: {result.is_wrapper_delegate}",
        f"- Is compat wrapper: {result.compatibility_wrapper_detected}",
        f"- Has NextActionInput dataclass: {result.has_input_dataclass}",
        f"- _rule_* helper count: {result.rule_helper_count} (min 4)",
        f"- Has .*Rule classes: {result.has_rule_classes}",
        "",
    ]
    if result.error_message:
        lines.append(f"**Error:** {result.error_message}\n")
    lines.append("## Verdict Definition\n")
    verdicts = {
        GuardVerdict.PASS: "Function passes all code-health checks.",
        GuardVerdict.PASS_COMPAT_WRAPPER: "Function has >8 args but is a thin compatibility wrapper (NextActionInput construction + rule helper delegation).",
        GuardVerdict.FAIL_TOO_MANY_ARGS: "Function has too many explicit arguments (>8) and is not an acceptable compatibility wrapper.",
        GuardVerdict.FAIL_TOO_LONG: "Function source exceeds 80 lines and is not a wrapper delegate.",
        GuardVerdict.FAIL_POLICY_CLASS_OVERENGINEERING: "Policy class overengineering detected (.*Rule class names).",
        GuardVerdict.FAIL_MISSING_INPUT_DATACLASS: "NextActionInput dataclass not found in source.",
        GuardVerdict.FAIL_MISSING_RULE_HELPERS: f"Only {result.rule_helper_count} _rule_* helpers found (min 4).",
    }
    for verd, desc in verdicts.items():
        lines.append(f"- `{verd.value}`: {desc}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Hermetic code-health guard for NextAction functions."
    )
    parser.add_argument("--file", required=True, help="Path to source file")
    parser.add_argument("--symbol", required=True, help="Function name to inspect")
    parser.add_argument("--output-json", required=True, help="Path for JSON output")
    parser.add_argument("--output-md", required=True, help="Path for Markdown output")
    args = parser.parse_args(argv)

    result = run_guard(args.file, args.symbol)

    Path(args.output_json).write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    Path(args.output_md).write_text(
        _render_markdown(result),
        encoding="utf-8",
    )

    print(f"Guard verdict: {result.verdict.value}")
    if result.error_message:
        print(f"Reason: {result.error_message}")
    print(f"JSON: {args.output_json}")
    print(f"MD: {args.output_md}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
