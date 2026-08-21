"""
Helper: Apply @dataclass(slots=True) to bare @dataclass declarations.

Safety rules:
- Skip classes that inherit from non-dataclass parents (Protocol, ABC, Exception base, etc.)
- Skip classes with cached_property
- Skip classes where __dict__ is accessed (checked externally)
- For class hierarchies (parent + children), add slots to ALL or NONE.
- frozen=True classes get slots=True, frozen=True (compatible combo)

Usage: python tools/_py314_apply_slots.py <file> [file ...]
"""

import ast
import sys
from pathlib import Path


def has_cached_property(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and any(
                    isinstance(d, ast.Name) and d.id == "cached_property" for d in item.decorator_list
                ):
                    return True
    return False


def has_dict_access(tree: ast.Module) -> bool:
    """Check if any code accesses self.__dict__ or instance.__dict__"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "__dict__":
            return True
    return False


def find_dataclass_classes(tree: ast.Module) -> list[tuple[ast.ClassDef, ast.expr | None, bool]]:
    """Return (class_def, decorator_args, already_has_slots) for each @dataclass class."""
    result = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name) and dec.func.id == "dataclass":
                    has_slots = any(
                        kw.arg == "slots" and isinstance(kw.value, ast.Constant) and kw.value.value is True
                        for kw in dec.keywords
                    )
                    result.append((node, dec, has_slots))
                    break
                elif isinstance(dec, ast.Name) and dec.id == "dataclass":
                    result.append((node, None, False))
                    break
    return result


def get_parent_names(class_node: ast.ClassDef) -> list[str]:
    """Return list of parent class names (string repr)."""
    names = []
    for base in class_node.bases:
        if isinstance(base, ast.Name):
            names.append(base.id)
        elif isinstance(base, ast.Attribute):
            names.append(ast.unparse(base))
    return names


def is_trivial_inheritance(parents: list[str]) -> bool:
    """True if all parents are dataclass-friendly (object, or other dataclasses in the same file)."""
    safe_parents = {"object", "Enum", "IntEnum", "Flag", "IntFlag"}
    for p in parents:
        if p in safe_parents:
            continue
        # Allow inheritance from other dataclasses in the same module (we'll process the parent too)
        # Conservative: assume non-trivial if parent is not in safe set
        return False
    return True


def rewrite_decorator(dec_node: ast.expr | None, add_slots: bool) -> ast.expr:
    """Rewrite a dataclass decorator to add slots=True."""
    if dec_node is None:
        # bare @dataclass → @dataclass(slots=True)
        if add_slots:
            return ast.Call(
                func=ast.Name(id="dataclass", ctx=ast.Load()),
                args=[],
                keywords=[ast.keyword(arg="slots", value=ast.Constant(value=True))],
            )
        return ast.Name(id="dataclass", ctx=ast.Load())
    if isinstance(dec_node, ast.Call):
        new_keywords = list(dec_node.keywords)
        has_slots = any(kw.arg == "slots" for kw in new_keywords)
        if not has_slots and add_slots:
            new_keywords.append(ast.keyword(arg="slots", value=ast.Constant(value=True)))
        return ast.Call(
            func=dec_node.func,
            args=list(dec_node.args),
            keywords=new_keywords,
        )
    return dec_node


def process_file(path: Path, verbose: bool = True) -> tuple[int, int]:
    """Returns (added_count, skipped_count)."""
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        if verbose:
            print(f"  SKIP {path}: syntax error {e}")
        return 0, 0

    if has_cached_property(tree):
        if verbose:
            print(f"  SKIP {path}: has cached_property")
        return 0, 0

    classes = find_dataclass_classes(tree)
    if not classes:
        return 0, 0

    class_parents: dict[str, list[str]] = {}
    for cls, _, _ in classes:
        class_parents[cls.name] = get_parent_names(cls)

    # Identify which classes can be safely slotted:
    # - No non-trivial parent
    # - Parent is also being slotted (or parent is in safe set)
    eligible: dict[int, bool] = {}  # class id → can slot
    parent_to_children: dict[str, list[str]] = {}
    for cls, _, _ in classes:
        for parent in class_parents.get(cls.name, []):
            parent_to_children.setdefault(parent, []).append(cls.name)

    for cls, _, has_slots in classes:
        if has_slots:
            eligible[id(cls)] = False  # already has slots
            continue
        parents = class_parents.get(cls.name, [])
        # If class has no parents or only object/Enum → eligible
        if not parents or all(p in {"object", "Enum", "IntEnum", "Flag", "IntFlag"} for p in parents):
            eligible[id(cls)] = True
        else:
            # Inherits from another class. Check if parent is being slotted too.
            {id(c) for c, _, _ in classes}
            parent_in_file = any(p in {cc.name for cc, _, _ in classes} for p in parents)
            if parent_in_file:
                # Parent exists in this file - we'll slot it too
                eligible[id(cls)] = True
            else:
                # External parent - skip
                eligible[id(cls)] = False

    line_rewrites: dict[int, str] = {}
    added = 0
    skipped = 0
    for cls, dec, has_slots in classes:
        if not eligible.get(id(cls), False):
            if not has_slots:
                skipped += 1
            continue
        new_dec = rewrite_decorator(dec, add_slots=True)
        line_rewrites[cls.decorator_list[0].lineno] = ast.unparse(new_dec)
        added += 1

    if not line_rewrites:
        return 0, skipped

    # Apply line-by-line rewrites
    lines = src.splitlines(keepends=True)
    new_lines = []
    for i, line in enumerate(lines, start=1):
        if i in line_rewrites:
            # Preserve leading whitespace
            stripped = line.lstrip()
            indent = line[: len(line) - len(stripped)]
            new_lines.append(f"{indent}@{line_rewrites[i]}\n")
        else:
            new_lines.append(line)

    new_src = "".join(new_lines)

    # Sanity: re-parse to ensure no syntax error
    try:
        ast.parse(new_src)
    except SyntaxError as e:
        if verbose:
            print(f"  ABORT {path}: re-parse failed {e}")
        return 0, skipped

    path.write_text(new_src, encoding="utf-8")
    if verbose:
        print(f"  {path}: added={added} skipped={skipped}")
    return added, skipped


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python tools/_py314_apply_slots.py <file> [file ...]")
        sys.exit(1)
    total_added = 0
    total_skipped = 0
    for arg in sys.argv[1:]:
        path = Path(arg)
        if not path.exists():
            print(f"MISSING: {path}")
            continue
        added, skipped = process_file(path)
        total_added += added
        total_skipped += skipped
    print(f"\nTotal: added={total_added} skipped={total_skipped}")


if __name__ == "__main__":
    main()
