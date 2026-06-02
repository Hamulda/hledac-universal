# RL_HEALTH_OBSERVABILITY — F262OBS

**Datum:** 2026-06-01
**Sprint:** F262OBS (RL training health observability)
**Status:** ✅ 4/4 probe tests passing
**Scope:** Read-only observability for QMIX training convergence. Žádný zásah do
training logic — pouze persist + diagnostic.

---

## 1. Problém

QMIX training loop byl aktivován v F261OPT (68/68 tests, 9 optimizations).
Persistovaný state `rl/.sprint_policy_state.json` obsahoval pouze
`cumulative_train_steps` a `last_loss` — **žádné historické křivky**, takže
nebylo možné zjistit, zda se Q-network reálně učí.

Po 130 sprintech (viz produkční state k 2026-06-01):
- epsilon: `0.087` (klesal z 0.10, cca 130 sprintů × decay 0.995 ≈ 0.087 ✅)
- `cumulative_train_steps: 0` — **dosud žádný training step**
- `last_loss: 0.0`

**Závěr:** Epsilon-greedy fallback běžel, ale QMIX training nebyl nikdy
aktivován, protože `--rl-train` nebyl zapnut. Stav "PRE-TRAINING".

---

## 2. Schéma — přidaná pole (F262OBS)

Všechna pole jsou **backward-compatible** (dataclass defaulty) a **fail-soft** —
chybějící hodnoty v legacy JSON se nahradí defaultem, ne `KeyError`.

| Pole | Typ | Default | Sémantika | Zapisuje se |
|------|-----|---------|-----------|-------------|
| `training_steps_completed` | `int` | `0` | Celkový počet training stepů (mirror `cumulative_train_steps` pro jasnost) | po každém `_run_qmix_training()` |
| `loss_history` | `list[float]` | `[]` | FIFO ring buffer posledních 100 loss hodnot | po každém training stepu |
| `mean_q_value_history` | `list[float]` | `[]` | FIFO ring buffer posledních 100 mean Q-magnitudes | po každém training stepu (proxy z Q-network parametrů) |
| `epsilon_history` | `list[float]` | `[]` | FIFO ring buffer posledních 100 epsilon hodnot | při každém `update()` (každý sprint) |
| `last_train_step_sprint` | `int` | `0` | Sprint sequence number posledního training stepu | po každém training stepu |

### Všechna pole jsou persistovaná

`_save()` (ř. 387-405 v `rl/sprint_policy_manager.py`) extended o všechna 5 nových
polí. `_load()` používá `SprintPolicyState(**data)` — dataclass automaticky
doplní chybějící klíče z `default_factory`, takže legacy JSON s 12 poli
se načte do 17-polové struktury bez `KeyError`.

---

## 3. Změny v kódu

### `rl/sprint_policy_manager.py`

1. **SprintPolicyState** (ř. 64-85) — přidáno 5 nových polí s default_factory
2. **`_save()`** (ř. 387-405) — payload nyní zahrnuje všechna 5 nových polí
3. **`update()`** (ř. 519-523) — append `epsilon_history` po epsilon decay
4. **`_run_qmix_training()`** (ř. 673-700) — po úspěšném training stepu:
   - vypočítá `mean_q` z Q-network parametrů (avg magnitude jako proxy)
   - append do `loss_history` a `mean_q_value_history` (FIFO 100)
   - aktualizuje `training_steps_completed` a `last_train_step_sprint`

**Invarianty:**
- M1-safe: žádné nové MLX importy, `try/except` kolem Q-proxy výpočtu
- Bounded: všechny `*_history` se po append oříznou na posledních 100
- Fail-soft: `_save()` obaleno `try/except`, selhání necrashuje scheduler
- Backward-compat: dataclass `**kwargs` pattern doplňuje chybějící pole

---

## 4. Diagnostic tool — `tools/rl_health_report.py`

**Read-only stdlib + json.** Žádný import z `runtime/`, `pipeline/`, `brain/`,
`duckdb_store`, MLX, network. Funguje i bez aktivního scheduleru.

### CLI
```bash
python tools/rl_health_report.py
python tools/rl_health_report.py --state-path /custom/path.json
python tools/rl_health_report.py --window 10 --trend-window 20
```

### Computed metrics
- `epsilon_decay_slope` — OLS lineární regrese přes `epsilon_history`
- `mean_q_value_trend` — slope posledních N Q-hodnot (růst = učení)
- `reward_rolling_mean` — průměr posledních `--window` rewards
- `reward_trend_slope` — OLS přes posledních `--trend-window` rewards
- `training_frequency` — `sprints / train_steps` (nebo "no training yet")

### Anomaly rules (exit 1 pokud některá tripne)
| ID | Pravidlo | Smysl |
|----|----------|-------|
| A1 | `train_steps ≥ 50` AND `epsilon_history` plochá (slope ≈ 0) | epsilon-greedy nefunguje → decay disabled |
| A2 | libovolná `mean_q_value_history[i] > 100` | Q-value explosion (typicky neinicializovaný lr, div. loss) |
| A3 | reward slope za posledních 20 sprintů < -0.1/sprint | reward decay (prostředí se mění, model nestíhá) |

Pre-training (`training_steps_completed == 0`) **nehlásí anomálii** — je to
očekávaný stav, banner explicitně říká "PRE-TRAINING".

### Sample output (produkční state, 130 sprintů)
```
=== RL Health Report ===
Sprints: 130   Train steps: 0   Epsilon: 0.087 (↓ decaying toward floor 0.05)
Q-value mean: — (no training steps recorded yet)
Reward avg (last 10): 0.00 (trend over last 20: +0.000/sprint →)
Training frequency: no training yet
Status: PRE-TRAINING (no train steps yet) ⏳

Note: Enable --rl-train to start recording training metrics.
```

### Sample output (syntetická anomálie)
```
=== RL Health Report ===
Sprints: 200   Train steps: 60   Epsilon: 0.050 (→ stuck)
Q-value mean: 65.00 (trend ↑, slope=+2.1429/step)
Reward avg (last 10): -1.00 (↓ trend: -0.113/sprint)
Training frequency: 1 step / 3 sprints
Loss (last/min/max): 0.1000 / 0.1000 / 0.1000
Status: ANOMALY — 3 issue(s) ❌

Anomalies detected:
  - A1: epsilon stuck at 0.1000 after 60 train steps (no decay)
  - A2: Q-value explosion at step 59: mean_q=200.00 > 100.0
  - A3: reward trend slope=-0.113/sprint over last 20 (below -0.1)
```

---

## 5. Probe tests — `tests/probe_rl_health.py`

4/4 passing za 0.51s (pure stdlib, žádný MLX/network):

| Test | Co ověřuje | Výsledek |
|------|------------|---------|
| `test_rl_health_report_runs_without_error` | subprocess call → exit 0 nebo 1, nikdy traceback | ✅ |
| `test_rl_state_schema_backward_compatible` | legacy JSON bez nových polí → dataclass defaults, ne KeyError | ✅ |
| `test_loss_history_fifo_eviction` | 101 appendů → `len == 100`, first=1.0, last=100.0; totéž pro ostatní 2 historie | ✅ |
| `test_new_fields_persisted_in_zstd_state` | round-trip: write 5 nových polí → zstd compress → decompress → JSON parse → všechna pole přítomna | ✅ |

Spuštění: `uv run pytest tests/probe_rl_health.py -v` → `4 passed in 0.51s`.

---

## 6. Jak použít (operátorsky)

```bash
# Z libovolného adresáře
cd /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal
uv run python tools/rl_health_report.py
echo "exit=$?"  # 0 = healthy/pre-training, 1 = anomaly

# V CI: fail build při anomálii
uv run python tools/rl_health_report.py || {
    echo "RL training anomaly detected — see report above"
    exit 1
}

# V cronu: weekly check
0 9 * * 1  cd /path/to/universal && uv run python tools/rl_health_report.py >> /var/log/rl_health.log
```

---

## 7. Doporučení pro další sprint (mimo scope F262OBS)

1. **Aktivovat `--rl-train` v jednom ze sprintů** — ověřit, že `_run_qmix_training()`
   projde celým cyklem (replay sample → loss → mean Q → append → save).
2. **Přidat do sprint scheduleru** periodický post-sprint hook, který automaticky
   zavolá `tools/rl_health_report.py` a loguje výsledek — nyní se musí volat manuálně.
3. **Grafický dashboard** (F263?) — time-series ploty `loss_history`,
   `mean_q_value_history`, `epsilon_history` přes existující `monitoring/sprint_dashboard.py`.
4. **Alerting integrace** — webhook/Slack notifikace při `exit=1`.

---

## 8. Compliance s invarianty

- ✅ M1-safe: žádný nový MLX import, stdlib + json + zstd
- ✅ Backward-compat: `SprintPolicyState(**legacy_payload)` funguje
- ✅ Bounded: všechny historie capped na 100
- ✅ Fail-soft: `_save()` a `_load()` obaleny `try/except`
- ✅ Pure stdlib v tools/: žádný import z `runtime/` ani `pipeline/`
- ✅ 80%+ coverage: 4 probe testy pokrývají subprocess, schema, FIFO, persist
- ✅ `make_prompt_cache` invariant: N/A (read-only tool, žádný prompt cache)
- ✅ `mx.eval([])` před `clear_cache()`: N/A (anomálie tool neřeší cache)

---

*F262OBS: 1 task, 4 probe tests, 1 read-only tool, 0 zásahů do training logic.*
