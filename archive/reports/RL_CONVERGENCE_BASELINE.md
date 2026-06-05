# RL Convergence Baseline — F263QCB Sprint

**Datum:** 2026-06-02
**Sprint:** F263QCB
**Status:** ✅ BASELINE ESTABLISHED

---

## 1. Dryrun Setup

| Parametr | Hodnota |
|----------|---------|
| Source state | `rl/.sprint_policy_state.json` (ZSTD, 286 B) |
| Temp state | `/tmp/hledac_dryrun_state.json` (kopie, produkce nezměněna) |
| Sprints | 90 syntetických |
| Training interval | 10 (default `_QMIX_TRAIN_INTERVAL`) |
| Min replay size | 64 (train_step gate) |
| RAM gate | `HLEDAC_RL_SKIP_RAM_GATE=1` (off, CI/low-RAM safe) |
| Train cooldown bypass | `pm._last_train_at = 0.0` po každém update (dryrun pouze — viz §5) |
| RNG seed | 42 (reproducible) |
| Wall time | **0.81 s** pro 90 sprintů (~9 ms/sprint) — pod 30 s M1 budgetem |

Dryrun skript: `tools/rl_training_dryrun.py` (runnable, idempotent, production-safe).

---

## 2. Baseline Metrics

### 2.1 Loss Trajectory

| Train step | Sprint | Loss | Δ vs prev |
|------------|--------|------|-----------|
| 1 | 69 | 10.1790 | — |
| 2 | 79 | 10.7879 | **+6.0 %** (rebound) |
| 3 | 89 | 8.0327 | **-25.5 %** (drop) |

Loss 1→3 celkově: **+21.1 %** (kolísavá křivka, ne divergence — step 3 ukazuje pokles).
Loss 1→2 nestabilní (6 % spike), ale 2→3 silný drop — síť se ustálila.

### 2.2 Mean Q Trajectory

| Train step | Mean Q | Δ vs prev |
|------------|--------|-----------|
| 1 | 0.9572 | — |
| 2 | 1.2004 | **+25.4 %** |
| 3 | 1.5918 | **+32.6 %** |

Q roste stabilně — učení probíhá, double-DQN target síť drží krok.

### 2.3 Epsilon Decay

- Start: 0.0995
- End (sprint 90): 0.0630
- Floor: 0.05 (nikdy nedosáhne — na ~300+ sprintů)
- Decay rate: 0.995/sprint (multiplikativní)

### 2.4 Reward Průměr

- Last 10 sprintů: **3.587** (vysoké — saturated log1p() z 10-50 accepted findings)
- Reward clamp: [-1.0, 5.0] — dobře uvnitř rozsahu

---

## 3. Convergence Estimate Refinement

| Kritérium | Pozorované | Výsledek |
|-----------|-----------|----------|
| Loss drop > 50 % | NE (1→2 stoupl) | — |
| Loss drop > 20 % | NE (1→2) | — |
| Loss drop > 20 % (1→3) | ANO (1→3 = +21 %, ale 2→3 = -25 %) | normal convergence |
| Loss increases | ANO (1→2) | guard DOPORUČEN |

### 3.1 Finální odhad

**Původní odhad (RL_TRAINING_ACTIVATION.md):** 200-400 sprintů
**Refinovaný odhad:** **200-400 sprintů** (beze změny — drop 1→3 = 21 %, edge případ)

Mean Q roste konzistentně (bez ohledu na loss volatilitu), epsilon decayuje pomalu k 0.05 floor — typická QMIX křivka na středně komplexním rewardu. 200-400 sprintů je realistický odhad pro konvergenci na M1 8GB s 3-min sprint cykly.

---

## 4. Loss Spike Guard — Implementace

**Rozhodnutí:** **PŘIDAT** guard (konzervativní přístup).

I když divergence v dryrun nenastala, 6% spike v 1→2 ukazuje nestabilitu. Reálné sprinty mají
větší variabilitu odměn než syntetické (extrémní abort, memory emergency) — guard je ochrana
proti skokům target sítě.

### 4.1 Specifikace

V `rl/sprint_policy_manager.py::_run_qmix_training()`, **před** append do `loss_history`:

```python
# F263QCB: loss spike guard — skip weight update if new_loss > 2.5x prev_loss
if len(self._state.loss_history) >= 1:
    prev_loss = self._state.loss_history[-1]
    if prev_loss > 0 and loss > 2.5 * prev_loss:
        log.warning(
            "[SprintPolicyManager] QMIX loss spike %.4f > 2.5x prev %.4f — skipping weight update",
            loss, prev_loss,
        )
        return  # do NOT increment training_steps, do NOT append history
```

### 4.2 Rationale

| Aspekt | Zdůvodnění |
|--------|-----------|
| **Proč 2.5× a ne 2×** | 2× je příliš citlivé — TD loss normálně kolísá 30-50 % v raných fázích |
| **Proč ne absolutní threshold** | Absolutní cutoff (např. loss > 50) by zablokoval učení v pozdějších fázích, kdy loss může přirozeně vzrůst při změně distribuce |
| **Proč ne incrementovat `training_steps_completed`** | Spike neznamená platný update — nechceme falešně signálovat pokrok |
| **Proč return a ne continue** | `return` přeskočí append do history → příští spike se porovnává s platným předchozím lossem |

### 4.3 Test Coverage

Test v `tests/test_sprint_policy_manager.py` (nebo `tests/probe_f263qcb_*.py`):
- injektuj fake trainer který vrátí loss 30.0 pak 5.0 → guard aktivuje
- ověř že `training_steps_completed` se NEzvyšuje
- ověř že `loss_history` zůstává beze změny

---

## 5. Dryrun Caveats — Co dryrun NESIMULUJE

| Reálný sprint | Dryrun | Důsledek |
|---------------|--------|----------|
| ~3 min reálného času | 9 ms | Cooldown 1 s vypnut (`pm._last_train_at = 0`) — jinak by vše kromě 1. stepu zablokoval |
| Reálná variabilita odměn (abort, memory emergency) | Rovnoměrné rozdělení 10-50 findings | Reálné sprintové odměny mají vyšší variance → spike guard je důležitější v produkci |
| MLX inference na M1 Metal | Inicializuje se, ale inference se nevolá | Trainer se inicializuje (qmix_weights existují), ale inference load = 0 |
| Quota (DuckDB write, fetch budget) | None | Dryrun nemá side-effecty na production state |

Dryrun je **lower bound** na chování. Produkční QMIX bude mít pravděpodobně vyšší loss volatilitu.

---

## 6. A2 Threshold Calibration

`tools/rl_health_report.py::Q_EXPLOSION_THRESHOLD`:

| Suchý běh mean_q range | Doporučený A2 threshold | Důvod |
|------------------------|------------------------|-------|
| 0.0 — 1.0 | 5.0 | Q-hodnoty jsou male, ale 5× outlier je jasná anomálie |
| 0.0 — 10.0 | **50.0** ← naše pozorování | Mean Q 0.96-1.59, threshold 50 = 30× bezpečnostní margin |
| 10.0 — 100.0 | 100.0 (default) | Už pokrývá očekávaný rozsah |

**Akce:** změnit `Q_EXPLOSION_THRESHOLD = 100.0` → **`Q_EXPLOSION_THRESHOLD = 50.0`** v `tools/rl_health_report.py`.

Pokud se v produkci ukáže, že Q roste nad 50, kalibrace se aktualizuje znovu z reálných dat.

---

## 7. Reproducibility

```bash
cd ~/PycharmProjects/Hledac/hledac/universal
HLEDAC_RL_SKIP_RAM_GATE=1 uv run python tools/rl_training_dryrun.py
```

Výstup: 3 train_stepy v 0.81 s, produkční state nedotčen (overeno hash checkem).
