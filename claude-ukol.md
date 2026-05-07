SPRINT F217C — DETERMINISTIC PUBLIC EVIDENCE BOOTSTRAP

ABSOLUTE REPO ROOT:
 /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal

WORKDIR RULE:
 Work only inside repo root.

NO-GIT RULE:
 Do not run git commands.
 Do not commit.
 Do not checkout.
 Do not reset.

BACKUP RULE:
 Before editing existing files:
 cp <file> <file>.bak_F217C_PUBLIC_BOOTSTRAP

CONTEXT:
 PUBLIC lane often reports 0 accepted and ambiguous timeout/fetch-zero states.
 Search providers can fail or be blocked.
 For domain queries, there are safe deterministic public endpoints:
 - https://domain/
 - https://www.domain/
 - https://domain/.well-known/security.txt
 - https://domain/robots.txt
 - https://domain/sitemap.xml
 These are bounded, passive, low-cost, and do not require search provider success.

GOAL:
 Add deterministic PUBLIC bootstrap candidates for domain/URL queries inside canonical runtime, not benchmark.

OWNED FILES:
 - pipeline/live_public_pipeline.py
 - fetching/public_fetcher.py
 - runtime/acquisition_strategy.py
 - runtime/sprint_scheduler.py
 - tests/probe_f217c_public_bootstrap/*
 - probe_f217c_public_bootstrap/*

READ-ONLY FILES:
 - transport/*
 - network/session_runtime.py
 - tools/live_result_sanity.py

MUST NOT EDIT:
 - CT bridge files
 - feed pipeline
 - knowledge/*
 - brain/*
 - stealth/*
 - dependency files
 - .qoder/*

TASKS:

1. Add deterministic bootstrap URL generator:
   - input: query/domain/url
   - output: bounded list of URLs
   - for domain:
       https://domain/
       https://www.domain/
       https://domain/.well-known/security.txt
       https://domain/robots.txt
       https://domain/sitemap.xml
   - max bootstrap URLs default 5
   - no brute force
   - no wordlists
   - no JS/browser
   - no stealth

2. Integrate into PUBLIC lane:
   - nonfeed_diagnostic profile must run bootstrap even if search discovery fails.
   - default profile may keep current behavior or use bootstrap only if already allowed by policy.
   - bootstrap candidates must be marked:
       public_discovery_source="deterministic_bootstrap"
   - stage machine must distinguish:
       BOOTSTRAP_ATTEMPTED
       BOOTSTRAP_ZERO_SUCCESS
       BOOTSTRAP_ACCEPTED

3. Preserve safety:
   - respect existing fetch semaphores/timeouts
   - no added concurrency explosion
   - no aggressive crawling
   - one request per endpoint max

4. Add telemetry:
   - public_bootstrap_enabled
   - public_bootstrap_candidates_count
   - public_bootstrap_fetch_attempted
   - public_bootstrap_fetch_success
   - public_bootstrap_accepted_findings
   - public_bootstrap_errors bounded

5. Tests:
   - domain query generates 5 bootstrap URLs
   - URL query generates canonical URL + related safe endpoints if domain parseable
   - non-domain query generates 0
   - candidates bounded
   - no search provider required for bootstrap path
   - bootstrap success can produce public accepted evidence through existing parser path
   - bootstrap failure gives explicit terminal stage
   - no live network, use fake fetcher
   - no browser
   - no dependency install

6. Reports:
   Create:
    probe_f217c_public_bootstrap/REPORT_PUBLIC_BOOTSTRAP.md
    probe_f217c_public_bootstrap/public_bootstrap.json

VERIFY:
 rtk proxy python -m pytest -q tests/probe_f217c_public_bootstrap
 rtk proxy python -m pytest -q \
   tests/probe_f216c_public_stage_machine \
   tests/probe_f217b_nonfeed_mission_controller \
   --tb=short

ABORT CONDITIONS:
 - Brute force or wordlist expansion
 - Timeout increase
 - Stealth/browser launch
 - Live network in tests
 - Provider policy broad rewrite

SUCCESS DEFINITION:
 - PUBLIC can attempt bounded deterministic evidence even when search provider fails
 - PUBLIC=0 has better diagnosis and real recovery path
