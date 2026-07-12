"""
BudgetTracker – sleduje spotřebu network, CPU, storage (jednoduchý klouzavý okna).
"""
import time
from collections import deque

class BudgetTracker:
    __slots__ = tuple(('cpu_budget', 'cpu_usage', 'network_budget', 'network_usage'))

    def __init__(self, network_mb_per_hour: float=10.0, cpu_ms_per_min: float=100.0):
        self.network_budget = network_mb_per_hour
        self.cpu_budget = cpu_ms_per_min
        self.network_usage = deque(maxlen=1000)
        self.cpu_usage = deque(maxlen=1000)

    def can_afford(self, network_mb: float, cpu_ms: float) -> bool:
        now = time.time()
        while self.network_usage and now - self.network_usage[0][0] > 3600:
            self.network_usage.popleft()
        total_network = sum((mb for _, mb in self.network_usage))
        if total_network + network_mb > self.network_budget:
            return False
        while self.cpu_usage and now - self.cpu_usage[0][0] > 60:
            self.cpu_usage.popleft()
        total_cpu = sum((ms for _, ms in self.cpu_usage))
        if total_cpu + cpu_ms > self.cpu_budget:
            return False
        return True

    def record(self, network_mb: float, cpu_ms: float):
        now = time.time()
        self.network_usage.append((now, network_mb))
        self.cpu_usage.append((now, cpu_ms))