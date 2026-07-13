import sys
sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')

from unittest.mock import MagicMock, patch
from hledac.universal.knowledge import graph_service
from hledac.universal.knowledge.graph_service import GraphService

def _make_fake_graph():
    fake = MagicMock()
    fake.upsert_ioc.return_value = True
    fake.upsert_relation.return_value = True
    fake.upsert_identity_edge.return_value = True
    fake.query.return_value = []
    fake.upsert_ioc_batch.return_value = 0
    fake.add_ioc.return_value = 1
    return fake

# Check what upsert_ioc actually calls
import inspect
src = inspect.getsource(graph_service.GraphService.upsert_ioc)
print("=== upsert_ioc source (key lines) ===")
for i, line in enumerate(src.split('\n'), 1):
    stripped = line.strip()
    if stripped and (stripped.startswith('if ') or stripped.startswith('graph') or stripped.startswith('return') or 'add_ioc' in stripped or '_seen_iocs' in stripped or 'Rust' in stripped or 'contains' in stripped):
        print(f"  {i:3d}: {stripped}")
