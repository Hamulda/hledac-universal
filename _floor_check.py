from hledac.universal.runtime.sprint_scheduler import SprintScheduler, SprintSchedulerConfig

cfg = SprintSchedulerConfig(sprint_duration_s=300)
print(f"_MIN_BRANCH_REMAINING_S_DEFAULT={cfg._MIN_BRANCH_REMAINING_S_DEFAULT}")
print(f"_MIN_BRANCH_REMAINING_S_CAP={cfg._MIN_BRANCH_REMAINING_S_CAP}")

instance = SprintScheduler.__new__(SprintScheduler)
instance._config = cfg
instance._cycle_time_ema = 0.0

print("\nPrimary formula (remaining_s passed):")
for rs in (150.0, 90.0, 60.0, 30.0, 15.0, 10.0):
    floor = instance._min_branch_remaining_s(rs)
    print(f"  remaining_s={rs} -> floor={floor}")

print("\nFallback formula (remaining_s=None, cycle_ema varies):")
for ema in (0.0, 5.0, 10.0, 20.0, 30.0, 60.0):
    instance._cycle_time_ema = ema
    floor = instance._min_branch_remaining_s(None)
    print(f"  cycle_ema={ema} -> floor={floor}")
