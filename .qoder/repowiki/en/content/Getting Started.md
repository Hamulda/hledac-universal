# Getting Started

<cite>
**Referenced Files in This Document**
- [README.md](file://README.md)
- [pyproject.toml](file://pyproject.toml)
- [requirements.txt](file://requirements.txt)
- [requirements-optional.txt](file://requirements-optional.txt)
- [paths.py](file://paths.py)
- [__main__.py](file://__main__.py)
- [config.py](file://config.py)
- [assert_py314_runtime.py](file://tools/assert_py314_runtime.py)
- [cp314_wheel_gate.py](file://tools/cp314_wheel_gate.py)
- [smoke_runner.py](file://smoke_runner.py)
</cite>

## Table of Contents
1. [Introduction](#introduction)
2. [System Requirements](#system-requirements)
3. [Installation](#installation)
4. [Environment Setup](#environment-setup)
5. [Initial Configuration](#initial-configuration)
6. [Basic Usage](#basic-usage)
7. [First Research Cycle](#first-research-cycle)
8. [Runtime Directory Structure and Path Management](#runtime-directory-structure-and-path-management)
9. [Quick Start Examples](#quick-start-examples)
10. [Troubleshooting](#troubleshooting)
11. [Performance Considerations](#performance-considerations)
12. [Conclusion](#conclusion)

## Introduction
This guide helps you install, configure, and run Hledac Universal for the first time. It covers Python version requirements, dependency installation, environment setup, configuration basics, and a minimal first research cycle. It also explains the runtime directory structure, provides troubleshooting guidance, and offers quick-start examples.

## System Requirements
- Python version: 3.13 or 3.14 (recommended). The project enforces a minimum of 3.13 and targets 3.14+ features.
- Operating system: macOS 14 Sonoma or 15 Sequoia on Apple Silicon (ARM64).
- Hardware: M1/M2 Mac with at least 8 GB RAM recommended for baseline operation; 16 GB+ recommended for heavier modes.

Key indicators in the project:
- Python requirement and classifiers specify 3.13 and 3.14 support.
- Apple Silicon extras (MLX, uvloop) are guarded for Darwin+arm64.
- M1 8GB presets are built-in for memory-constrained environments.

**Section sources**
- [pyproject.toml:41-41](file://pyproject.toml#L41)
- [pyproject.toml:26-41](file://pyproject.toml#L26-L41)
- [config.py:36-56](file://config.py#L36-L56)

## Installation
There are two supported installation approaches: pip and uv. Both are reproducible and validated by the project’s packaging configuration.

### Option A: Install with pip
- Install the default dependency set:
  - pip install .
- Install optional extras as needed:
  - pip install .[light]
  - pip install .[apple-accel]
  - pip install .[osint-html]
  - pip install .[graph-storage]
  - pip install .[torch]
  - pip install .[dev]
  - pip install .[all] (everything except torch)

Notes:
- Torch is not included in default dependencies; install separately if needed.
- Apple Silicon acceleration extras require Darwin+arm64.

**Section sources**
- [pyproject.toml:7-16](file://pyproject.toml#L7-L16)
- [pyproject.toml:130-138](file://pyproject.toml#L130-L138)
- [pyproject.toml:105-112](file://pyproject.toml#L105-L112)

### Option B: Install with uv (recommended for speed and reproducibility)
- The project is configured to prefer managed Python installations and package resolution.
- Use uv to resolve and install with the same extras as above.

Verification:
- The project includes a tool to validate Python 3.14+ runtime features before use.

**Section sources**
- [pyproject.toml:216-219](file://pyproject.toml#L216-L219)
- [assert_py314_runtime.py:58-72](file://tools/assert_py314_runtime.py#L58-L72)

### Optional Dependencies
Install optional extras from requirements-optional.txt or via extras:
- Acceleration: rapidfuzz
- Graph storage: pyarrow, duckdb, polars
- OSINT HTML: selectolax, curl_cffi, h2
- Security: cryptography
- Transport: h2, aiohttp-socks
- Torch: torch, torchvision (install separately)

**Section sources**
- [requirements-optional.txt:1-54](file://requirements-optional.txt#L1-L54)
- [pyproject.toml:153-185](file://pyproject.toml#L153-L185)

## Environment Setup
- Ensure your active Python interpreter is 3.14+ and has the required features (uuid.uuid7, annotationlib, InterpreterPoolExecutor).
- Use a virtual environment to isolate dependencies.

Validation script:
- Run the Python 3.14 runtime assertion tool to confirm your environment meets requirements.

**Section sources**
- [assert_py314_runtime.py:58-72](file://tools/assert_py314_runtime.py#L58-L72)

## Initial Configuration
Hledac Universal supports environment-based configuration and presets tailored for M1 8GB systems.

- Research modes: QUICK, STANDARD, DEEP, EXTREME, AUTONOMOUS
- M1 8GB optimization presets are applied automatically when enabled
- Environment variables:
  - HLEDAC_RESEARCH_MODE (quick, standard, deep, extreme, autonomous)
  - HLEDAC_MEMORY_LIMIT_MB
  - HLEDAC_MAX_STEPS
  - HLEDAC_LOG_LEVEL
  - HLEDAC_M1_OPTIMIZED (true/false)

Example usage:
- Create a configuration programmatically or load from environment
- Adjust model stacks and concurrency for M1 8GB

**Section sources**
- [config.py:394-431](file://config.py#L394-L431)
- [config.py:466-498](file://config.py#L466-L498)
- [config.py:432-464](file://config.py#L432-L464)

## Basic Usage
Run Hledac Universal with the built-in CLI. The default entry point is the “universal” runner.

Common commands:
- python -m hledac.universal
- python -m hledac.universal --sprint "<your query>" --duration 1800
- python -m hledac.universal --export-dir "<path>"
- python -m hledac.universal --aggressive
- python -m hledac.universal --deep-probe
- python -m hledac.universal --ui

Notes:
- The CLI supports Python 3.14 features like suggestion-on-error and colored help when available.
- The canonical sprint owner path is core.__main__.run_sprint(); the universal entry point delegates to it.

**Section sources**
- [__main__.py:211-245](file://__main__.py#L211-L245)
- [__main__.py:70-81](file://__main__.py#L70-L81)

## First Research Cycle
Follow these steps to run your first research cycle:

1. Verify Python 3.14+ runtime:
   - python tools/assert_py314_runtime.py

2. Run a smoke test to validate imports and basic runtime:
   - python smoke_runner.py

3. Start a short sprint:
   - python -m hledac.universal --sprint "initial test query" --duration 180

4. Review the generated report in ~/.hledac/reports/<sprint_id>.md

Optional:
- Enable aggressive mode for tighter budgets
- Enable UI dashboard for live telemetry
- Use --export-dir to customize report location

**Section sources**
- [assert_py314_runtime.py:58-72](file://tools/assert_py314_runtime.py#L58-L72)
- [smoke_runner.py:59-151](file://smoke_runner.py#L59-L151)
- [__main__.py:211-245](file://__main__.py#L211-L245)
- [paths.py:326-363](file://paths.py#L326-L363)

## Runtime Directory Structure and Path Management
All runtime data resides under hledac/universal/runtime/ (gitignored). The project defines canonical path constants and initializes directories at import time.

- runtime/cti/: CTI export bundle directory
- runtime/runs/: diagnostic/markdown/stix bundle runs
- runtime/state/: sprint state and reports
- runtime/embeddings/: vector embedding cache
- runtime/benchmarks/: benchmark results

Environment overrides:
- GHOST_EXPORT_DIR can override CTI export directory (backward compatibility)

Initialization:
- All runtime directories are created with mkdir(parents=True, exist_ok=True) at import time.

**Section sources**
- [README.md:8-48](file://README.md#L8-L48)
- [paths.py:266-283](file://paths.py#L266-L283)
- [paths.py:420-436](file://paths.py#L420-L436)

## Quick Start Examples
- Basic query:
  - python -m hledac.universal --sprint "cyber threat landscape"

- Custom configuration via environment:
  - HLEDAC_RESEARCH_MODE=DEEP HLEDAC_M1_OPTIMIZED=true python -m hledac.universal --sprint "deep research"

- Verification:
  - python tools/assert_py314_runtime.py
  - python smoke_runner.py

- Export location:
  - python -m hledac.universal --sprint "test" --export-dir "/custom/reports"

**Section sources**
- [config.py:466-498](file://config.py#L466-L498)
- [assert_py314_runtime.py:58-72](file://tools/assert_py314_runtime.py#L58-L72)
- [smoke_runner.py:293-330](file://smoke_runner.py#L293-L330)

## Troubleshooting
Common issues and resolutions:

- Python version mismatch:
  - Symptom: Errors about missing uuid.uuid7 or annotationlib.
  - Resolution: Ensure Python 3.14+ is active and reinstall dependencies in a fresh virtual environment.

- Missing Apple Silicon acceleration:
  - Symptom: mlx or uvloop not available on non-Darwin/arm64.
  - Resolution: Install .[apple-accel] only on Apple Silicon; otherwise, rely on defaults.

- RAM disk not available:
  - Symptom: Warning about fallback to SSD; runtime artifacts written to ~/.hledac_fallback_ramdisk.
  - Resolution: Set GHOST_RAMDISK to an active ramdisk or mount /Volumes/ghost_tmp.

- Lock files or stale sockets:
  - Symptom: LMDB lock errors or stale socket files.
  - Resolution: Use the boot guard and cleanup helpers in paths.py to remove stale locks and sockets.

- Torch installation:
  - Symptom: Missing torch/torchvision.
  - Resolution: Install separately as documented in pyproject.toml and requirements-optional.txt.

- Dependency validation:
  - Use the cp314 wheel gate tool to dry-run and validate wheel downloads for specific extras before installation.

**Section sources**
- [assert_py314_runtime.py:58-72](file://tools/assert_py314_runtime.py#L58-L72)
- [paths.py:134-142](file://paths.py#L134-L142)
- [paths.py:495-537](file://paths.py#L495-L537)
- [paths.py:565-590](file://paths.py#L565-L590)
- [pyproject.toml:130-138](file://pyproject.toml#L130-L138)
- [requirements-optional.txt:14-18](file://requirements-optional.txt#L14-L18)
- [cp314_wheel_gate.py:202-264](file://tools/cp314_wheel_gate.py#L202-L264)

## Performance Considerations
- Apple Silicon optimization:
  - M1 8GB presets reduce memory footprint and agent concurrency.
  - Enable .[apple-accel] for MLX and uvloop acceleration on Darwin+arm64.

- Memory management:
  - M1Presets caps memory usage and thermal thresholds.
  - Consider disabling heavy features (knowledge graph, RAG) on constrained systems.

- Concurrency:
  - Adjust max_concurrent_agents and agent timeouts according to your hardware.

- Disk I/O:
  - Prefer a ramdisk via GHOST_RAMDISK for runtime artifacts to avoid SSD wear.

**Section sources**
- [config.py:36-56](file://config.py#L36-L56)
- [config.py:432-464](file://config.py#L432-L464)
- [pyproject.toml:105-112](file://pyproject.toml#L105-L112)
- [paths.py:116-142](file://paths.py#L116-L142)

## Conclusion
You are now ready to install Hledac Universal, configure it for your environment, and run your first research cycle. Use the quick-start examples to validate your setup, and consult the troubleshooting section if you encounter issues. For deeper customization, explore the configuration presets and optional extras.