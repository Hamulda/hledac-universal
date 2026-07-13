import sys
sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')

from unittest.mock import MagicMock, patch
from hledac.universal.knowledge import graph_service
from hledac.universal.knowledge.graph_service import GraphService

# Check what method graph_service.upsert_ioc calls on the graph
import inspect
src = inspect.getsource(graph_service.GraphService.upsert_ioc)
# Find graph.add_ioc vs graph.upsert_ioc
for line in src.split('\n'):
    if 'graph.' in line and ('add_ioc' in line or 'upsert_ioc' in line):
        print(repr(line.strip()))
