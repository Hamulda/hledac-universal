#!/usr/bin/env python3
"""Direct migration of project_types.py @dataclass → msgspec.Struct"""
import re
from core import aclose

root = "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal"
filepath = f"{root}/project_types.py"

with open(filepath) as f:
    src = f.read()

# Lines for each migration:
# @dataclass(slots=True) + class Foo:        → remove @dataclass, class Foo(msgspec.Struct, gc=False):
# @dataclass(frozen=True, slots=True) + class → remove @dataclass, class Foo(msgspec.Struct, frozen=True, gc=False):
# @dataclass + class Foo:                     → remove @dataclass, class Foo(msgspec.Struct, gc=False):
# NeuralEvent: keep as dataclass (has __setattr__ in __post_init__)

lines = src.split('\n')
new_lines = []
i = 0
migrated = 0
skipped = 0

while i < len(lines):
    line = lines[i]
    # Detect @dataclass decorator followed by class def on next line(s)
    if re.match(r'@dataclass(\(.*?\))?\s*$', line.strip()):
        # Check next non-blank line for class definition
        j = i + 1
        while j < len(lines) and lines[j].strip() == '':
            j += 1
        if j < len(lines) and re.match(r'class \w+(\(.*?\))?:', lines[j].strip()):
            cls_line = lines[j].strip()
            cls_match = re.match(r'class (\w+)(\(.*?\))?:', cls_line)
            if not cls_match:
                new_lines.append(line)
                i += 1
                continue
            cls_name = cls_match.group(1)
            existing_bases = cls_match.group(2) or ""

            # Skip NeuralEvent
            if cls_name == 'NeuralEvent':
                # Keep both lines as-is
                new_lines.append(line)
                new_lines.append(lines[j])
                skipped += 1
                print(f"  SKIP {cls_name}")
                i = j + 1
                continue

            # Determine decorator kwargs
            deco_match = re.match(r'@dataclass(\(.*?\))?', line.strip())
            deco_args = deco_match.group(1) if deco_match and deco_match.group(1) else ""
            has_frozen = 'frozen=True' in deco_args
            # has_slots is present but irrelevant for msgspec

            # Build new class line
            if has_frozen:
                new_cls = f"class {cls_name}(msgspec.Struct, frozen=True, gc=False):"
            else:
                new_cls = f"class {cls_name}(msgspec.Struct, gc=False):"

            if existing_bases:
                # Has existing bases — prepend msgspec.Struct
                new_cls = f"class {cls_name}(msgspec.Struct, frozen=True, gc=False, {existing_bases[1:-1]}):" if has_frozen else f"class {cls_name}(msgspec.Struct, gc=False, {existing_bases[1:-1]}):"

            print(f"  MIGRATE {cls_name}: {has_frozen=}")
            new_lines.append(new_cls)
            migrated += 1
            # Skip @dataclass line AND the class line (consumed both)
            i = j + 1
            continue
        else:
            # @dataclass not followed by class def — keep as-is
            new_lines.append(line)
    else:
        new_lines.append(line)
    i += 1

new_src = '\n'.join(new_lines)
print(f"\nMigrated: {migrated}, Skipped: {skipped}")
print(f"Lines: {len(lines)} -> {len(new_lines)}")

# Verify no @dataclass decorators remain
remaining = [l for l in new_lines if '@dataclass' in l]
print(f"Remaining @dataclass lines: {len(remaining)}")
if remaining:
    for l in remaining[:5]:
        print(f"  {l}")

# Backup and write
with open(filepath + ".bak2", "w") as f:
    f.write(src)
with open(filepath, "w") as f:
    f.write(new_src)
print(f"\nWritten to {filepath}")
