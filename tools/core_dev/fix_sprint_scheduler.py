#!/usr/bin/env python3
"""Direct migration of runtime/sprint_scheduler.py @dataclass → msgspec.Struct"""
import re
from core import aclose

root = "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal"
filepath = f"{root}/runtime/sprint_scheduler.py"

with open(filepath) as f:
    src = f.read()

lines = src.split('\n')
new_lines = []
i = 0
migrated = 0
skipped = 0

# Classes to SKIP (inherit from other dataclasses or have complex post_init)
SKIP = {'SprintSchedulerResult', 'FeedSprintResult', 'PublicSprintResult',
        'CtSprintResult', 'NonfeedSprintResult'}

while i < len(lines):
    line = lines[i]
    if re.match(r'@dataclass(\(.*?\))?\s*$', line.strip()):
        j = i + 1
        while j < len(lines) and lines[j].strip() == '':
            j += 1
        if j < len(lines) and re.match(r'class \w+(\(.*?\))?:', lines[j].strip()):
            cls_match = re.match(r'class (\w+)(\(.*?\))?:', lines[j].strip())
            if cls_match:
                cls_name = cls_match.group(1)
                if cls_name in SKIP:
                    new_lines.append(line)
                    new_lines.append(lines[j])
                    skipped += 1
                    print(f"  SKIP {cls_name}")
                    i = j + 1
                    continue

                deco_args = ""
                m = re.match(r'@dataclass(\(.*?\))?', line.strip())
                if m and m.group(1):
                    deco_args = m.group(1)
                has_frozen = 'frozen=True' in deco_args

                if has_frozen:
                    new_cls = f"class {cls_name}(msgspec.Struct, frozen=True, gc=False):"
                else:
                    new_cls = f"class {cls_name}(msgspec.Struct, gc=False):"

                print(f"  MIGRATE {cls_name}: frozen={has_frozen}")
                new_lines.append(new_cls)
                migrated += 1
                i = j + 1
                continue
    new_lines.append(line)
    i += 1

new_src = '\n'.join(new_lines)
print(f"\nMigrated: {migrated}, Skipped: {skipped}")

# Verify
remaining = [l for l in new_lines if re.match(r'\s*@dataclass', l)]
print(f"Remaining @dataclass: {len(remaining)}")
for l in remaining[:5]:
    print(f"  {l}")

with open(filepath, "w") as f:
    f.write(new_src)
print(f"Written to {filepath}")
