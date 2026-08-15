#!/usr/bin/env python3
"""Pre-commit guard: blokuje commit souboru 'None' nebo 'None.*'"""

import re
import subprocess
import sys
from core import aclose

staged = subprocess.run(
    ["git", "diff", "--cached", "--name-only"],
    capture_output=True, text=True
).stdout.strip().splitlines()

bad = [f for f in staged if re.match(r'^None(\.|$)', f)]
if bad:
    print(f"[PRE-COMMIT GUARD] Blokuji commit: {bad}", file=sys.stderr)
    print("Soubor 'None' nebo 'None.*' nesmí být commitnut.", file=sys.stderr)
    sys.exit(1)
sys.exit(0)
