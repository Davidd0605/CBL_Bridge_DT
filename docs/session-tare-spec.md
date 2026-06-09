# Specification: session tare and comparison behaviour

**Status:** draft  
**Relates to:** SSA6 intended workflow, `comparison_baseline.py`, `SensitivityMatrix.py`, `damage_detection.py`, `bridge_model.py`, `bridge_mqtt.py`  
**Repository:** `digital-twin/MQTT_Unity_Python`

---

## 1. Problem

The SSA6 report describes a **session tare**: one calibration at the start of monitoring, then load is varied for the remainder of the session while damage detection continues.

The current implementation treats tare as a **fixed load-state reference**. When `node_loads` after tare differ from the stored snapshot, `load_mismatch()` returns true, damage cycles are skipped, and the UI suggests re-tare. That conflicts with the intended use case, where changing load is the experiment, not a reason to recalibrate.

| Aspect | Intended (SSA6) | Current code |
|--------|-----------------|--------------|
| When to tare | Once per session (healthy state, often zero/nominal load) | Same command, but validity tied to unchanged `node_loads` |
| Role of tare | Remove gauge offsets; optional healthy snapshot at session start | Full structural + load snapshot; blocks if load changes |
| Load during session | Varies via `cbl/bridge/load` | Triggers `comparison_tare_load_mismatch` and skips detection |
| Re-tare | New session or deliberate reset only | Required after every load change (de facto) |

---

## 2. Target behaviour

### 2.1 Session tare (operator)

1. Start monitoring session.
2. With bridge healthy and at known load (typically 0 N or nominal), publish strains on `cbl/bridge/real/state`.
3. Send `{"action":"tare"}` on `cbl/bridge/command` **once**.
4. For the rest of the session:
   - Apply and change load via `cbl/bridge/load`.
   - Publish strains on `cbl/bridge/real/state`.
   - Consume `cbl/bridge/sim/damage` without sending tare again.

`clear_tare` remains for explicitly ending a session baseline.

### 2.2 Semantics of comparison modes

**Delta mode (default)** — asymmetric session offset:

| Side | Meaning |
|------|---------|
| Physical | `current_reading - tare_physical[gauge]` (sensor zero / session offset only) |
| Model (healthy check and sensitivity) | Strains from **current** OpenSees solve at **current** live load. Comparison uses the same convention as today for model deltas **or** switches to absolute model strains at current load while physical stays offset-corrected (see §3.2). |

Recommended interpretation for varying load (simplest correct physics):

- **Physical vector:** offset-corrected absolute readings  
  `ε_phys,corr(g) = ε_phys,raw(g) - ε_phys,tare(g)`
- **Model vector:** absolute strains at current load from healthy solve  
  `ε_model(g) = ε_model,combined(ele(g))` after `_solve_current_loads()`
- **Compare** `ε_phys,corr` to `ε_model` (not both as “delta since tare load state” unless tare was at the same load as now).

If tare is always at 0 N and load ramps monotonically, “delta since tare” on **both** sides is equivalent to the increment from session start; that breaks when load steps are non-monotonic or tare is not at zero. Prefer explicit **physical offset + model absolute at current load** for robustness.

**Absolute mode** — unchanged: raw physical vs raw model at current load; tare optional for metadata only.

### 2.3 Damage detection

- Must **not** skip cycles solely because live load changed since tare.
- Prerequisites: session tare active (delta mode), analysis completed, complete gauge readings in `real/state`, `strain_gauges` configured.
- Healthy gate and 86-scenario sweep unchanged in structure; only the strain vectors fed into `is_healthy` / `build_sensitivity` follow §3.2.

### 2.4 MQTT / published state

| Field | Change |
|-------|--------|
| `comparison_tare_load_mismatch` | Remove **blocking** meaning. Either remove field, or repurpose as informational `comparison_tare_load_n` vs `live_load_n` (no effect on detection). |
| Status text `(re-tare recommended)` | Remove or replace with e.g. `session tare @ X N, live Y N`. |

---

## 3. Required code changes

### 3.1 `comparison_baseline.py`

1. **Document** in class docstring: session tare stores per-gauge physical offsets (and optional deflection offsets); not a per-load-step calibration.
2. **`load_mismatch()`**  
   - **Option A (minimal):** Stop using for gating; keep method for optional telemetry only.  
   - **Option B:** Remove method; store `tare_load_n` for display only, never compare to current `node_loads` for logic.
3. **`tare()`**  
   - Continue storing `physical_strains` at session start.  
   - **Stop requiring** `model_strains` / `node_loads` for detection gating, **or** keep storing them only for diagnostics.  
   - If adopting §2.2 recommended vectors: do **not** use `model_strains` in the primary comparison path (only physical offsets from tare).
4. **`model_strain_delta` / `model_strain_deltas_for_gauges`**  
   - Deprecate for detection, or restrict to legacy absolute-delta path behind a flag.

### 3.2 `SensitivityMatrix.py`

1. **`run_healthy(mode="delta")`**  
   - Return **absolute** gauge strains at current load: `_read_all_gauges_absolute()`, not `_read_all_gauges_delta()`.
2. **`_physical_delta_strain` / measured vector in `damage_detection`**  
   - Keep physical offset correction (session tare).
3. **`build_sensitivity` / `_run_damaged`**  
   - Damaged scenario columns: absolute strains at current load (same as healthy path).  
   - Measured column: physical offset-corrected absolutes.  
   - Ensure `mode=="delta"` means “physical session offset” not “model delta since tare”.
4. **`build_sensitivity` delta branch**  
   - Remove requirement `baseline.active` tied to unchanged load; require only `baseline.active` for physical offset.
5. Update docstrings for `build_sensitivity(..., mode=)` to describe new semantics.

### 3.3 `damage_detection.py`

1. **`_prerequisites_ok`** — delete block:
   ```python
   if self.model.comparison.load_mismatch():
       return False, "load mismatch — re-tare recommended"
   ```
2. Keep `comparison tare not active` check for delta mode only.
3. No other behavioural change to debounce, lock, or publish logic.

### 3.4 `bridge_model.py`

1. **`_update_status` / tare_status** — remove `(re-tare recommended)` tied to `load_mismatch()`.
2. Optional: show `Session tare active | tare at {load_n} N | live {live_load} N`.

### 3.5 `bridge_mqtt.py`

1. Adjust or remove `comparison_tare_load_mismatch` in `sim/state` payload per §2.4.
2. Update README section 5 to match topic names and session-tare workflow.

### 3.6 `README.md`

1. Document session tare workflow: tare once → vary load → monitor `sim/damage`.
2. Remove implication that load change requires re-tare.
3. Fix geometry topic to `cbl/bridge/sim/geometry` if still wrong.

### 3.7 Tests (when added)

| Case | Expected |
|------|----------|
| Tare at 0 N, load 100 N, healthy prototype | `healthy=true`, detection runs |
| Tare at 0 N, load 200 N without re-tare | Detection still runs (not skipped) |
| No tare, delta mode | Detection skipped with clear reason |
| Load change + damage on one gauge | Flags correct element |

---

## 4. Non-goals (this change)

- Unity visualization of `sim/damage`.
- Automated model calibrator (separate initiative; see §5).
- Changing debounce intervals, \(\alpha\), or number of scenarios.
- Deriving `strain_gauges` from `load_cells` in JSON.

---

## 5. Follow-on: automated model calibrator (SSA next step)

Not part of the session-tare fix, but required for accurate comparison under load.

**Goal:** Adjust OpenSees parameters so simulated strains match prototype measurements before damage logic is trusted.

**Inputs:**

- MQTT history or scripted sweeps: `cbl/bridge/load`, `cbl/bridge/real/state`.
- Config: tunable parameters in `bridge_3d_pratt.json` (E, A, support fixities, joint stiffness placeholders).

**Outputs:**

- Updated JSON / coefficient set minimizing error (e.g. NRMSE across gauges per load step).
- Optional `cbl/bridge/command` e.g. `{"action":"apply_calibration","params":{...}}` or file write + reload.

**Suggested modules:**

- `openseespy/calibration.py` — objective function, parameter bounds, optimizer loop.
- CLI or MQTT-triggered batch mode using stored broker logs.

**Acceptance:** After calibration, healthy gate passes for physical prototype at multiple load levels with single session tare.

---

## 6. Implementation order

1. Remove `load_mismatch` gating and status/MQTT “re-tare” messaging (quick, unblocks intended workflow partially).
2. Implement §3.2 comparison vectors (physical offset + model absolute at current load).
3. Update README and inline docstrings.
4. Add tests per §3.7.
5. Plan calibrator per §5.

---

## 7. Acceptance criteria (session tare change)

- [ ] One tare at session start; load varied via MQTT without further tare commands.
- [ ] Damage detection runs after load changes (not skipped for load mismatch).
- [ ] `comparison_tare_load_mismatch` does not block detection.
- [ ] Healthy structure at multiple loads classifies healthy with single session tare (within threshold tuning).
- [ ] Injected gauge fault still flags correct element at fixed load (regression).
- [ ] SSA6 description and README agree with behaviour.
