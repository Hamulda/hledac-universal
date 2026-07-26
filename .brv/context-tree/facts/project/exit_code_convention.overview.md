<key_points>
- Exit 0 = clean success, Exit 1 = runtime error (unexpected exceptions), Exit 2 = config/validation error (F221-ABORT windup guard), Exit 3 = programmer error (NameError, AttributeError, ImportError), Exit 130 = SIGINT
- sys.exit(N) raised inside run_sprint() must propagate as exit N (not exit 1)
- 6 regression tests validate subprocess exit codes: test_nameerror_in_run_sprint_exits_3, test_importerror_in_run_sprint_exits_3, test_windup_guard_short_duration_exits_2, test_help_exits_0, test_keyboardinterrupt_exits_130, test_systemexit_not_swallowed_by_catchall
- _MAIN_FATAL prefix required in logs for log-parser compatibility
- Tests patch core.__main__.run_sprint (not core.run_sprint)
</key_points>
<structure>
Exit code convention in __main__.main() catch-all envelope. Flow: run_sprint() -> catch exceptions -> map to exit code -> sys.exit(code). Tests run as subprocesses to observe actual exit codes.
</structure>
<entities>
tests/test_exit_codes.py (6 tests), core/__main__.py, F221-ABORT windup guard, HLEDAC_ACQUISITION_PROFILE, HLEDAC_LOG_LEVEL
</entities>
<patterns>
^_MAIN_FATAL log prefix, sys.exit(N) propagation, subprocess test validation
</patterns>
<decisions>
Use exit code envelope in __main__.py and core/__main__.py; Test patches core.__main__.run_sprint; windup_guard_threshold uses --duration 300 to pass (active_window=120 > MIN_ACTIVE_WINDOW_S=30)
</decisions>