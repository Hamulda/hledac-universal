#!/usr/bin/env python3
"""Fix syntax errors in evidence_network_analyzer.py"""

with open('advanced_web/evidence_network_analyzer.py', 'r') as f:
    content = f.read()

# Fix: list(g.strength(...)), weights=...)) -> list(g.strength(...), weights=...))
# The bug is: TWO )) at end of g.strength call
content = content.replace(
    'list(g.strength(vertices=list(range(n)), weights="weight"))',
    'list(g.strength(vertices=list(range(n)), weights="weight"))'
)
# Also fix betweenness
content = content.replace(
    'list(g.betweenness(vertices=None, directed=False, weights="weight", cutoff=k))',
    'list(g.betweenness(vertices=None, directed=False, weights="weight", cutoff=k))'
)
content = content.replace(
    'list(g.betweenness(vertices=None, directed=False, cutoff=k))',
    'list(g.betweenness(vertices=None, directed=False, cutoff=k))'
)

with open('advanced_web/evidence_network_analyzer.py', 'w') as f:
    f.write(content)

print("Fixed!")
