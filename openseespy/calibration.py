"""Bridge model calibration: tune E and cross-section area scale factors to match
physical strain gauge readings at multiple known load states.

Workflow
--------
1. Apply a known load to the real bridge and record physical strain readings.
2. Call ``add_measurement(node_loads, gauge_readings)`` for each load state.
3. Call ``run()`` to find scale factors that minimise NRMSE across all states.
4. Call ``apply(result)`` to commit the calibration to the live model.
5. Optionally ``save(path, result)`` / ``load(path)`` for persistence.

The calibrator is independent of the session tare: tare removes per-gauge DC
offsets; calibration corrects the model's global structural stiffness.
"""

from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from bridge_model import BridgeModel


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class CalibrationResult:
    """Outcome of a calibration optimisation run.

    Attributes:
        E_scale: Global Young's modulus multiplier applied to all elements.
        angle_A_scale: Cross-section area multiplier for ``angle_15x15x3`` members.
        flat_A_scale: Cross-section area multiplier for ``flat_bar_15x3`` members.
        nrmse: Mean normalised RMSE across all measurement states at convergence.
        iterations: Number of optimiser iterations performed.
        success: Whether the optimiser reported convergence.
        timestamp: Unix time when the result was produced.
    """

    E_scale: float
    angle_A_scale: float
    flat_A_scale: float
    nrmse: float
    iterations: int
    success: bool
    timestamp: float = dataclasses.field(default_factory=time.time)

    def to_params(self) -> dict:
        """Return the three scale factors as a plain dict for ``apply_calibration``."""
        return {
            "E_scale": self.E_scale,
            "angle_A_scale": self.angle_A_scale,
            "flat_A_scale": self.flat_A_scale,
        }

    def __str__(self) -> str:  # noqa: D105
        return (
            f"CalibrationResult(E_scale={self.E_scale:.4f}, "
            f"angle_A_scale={self.angle_A_scale:.4f}, "
            f"flat_A_scale={self.flat_A_scale:.4f}, "
            f"nrmse={self.nrmse:.6f}, "
            f"iterations={self.iterations}, success={self.success})"
        )


# ---------------------------------------------------------------------------
# Calibrator
# ---------------------------------------------------------------------------

class BridgeCalibrator:
    """Calibrates bridge model material/geometry parameters against real sensor data.

    The optimiser searches for ``(E_scale, angle_A_scale, flat_A_scale)`` that
    minimises the mean NRMSE between simulated and measured strain gauge
    readings across all stored measurement states.

    Parameters
    ----------
    model:
        Live ``BridgeModel`` instance whose ``_calibration_scales`` will be
        adjusted during optimisation and restored afterwards.
    """

    PARAM_NAMES = ("E_scale", "angle_A_scale", "flat_A_scale")
    DEFAULT_BOUNDS: list[tuple[float, float]] = [(0.5, 2.0), (0.5, 2.0), (0.5, 2.0)]
    DEFAULT_X0: list[float] = [1.0, 1.0, 1.0]

    def __init__(self, model: "BridgeModel") -> None:
        self.model = model
        self.measurements: list[tuple[dict, dict]] = []
        self._gauge_definitions: list[dict] | None = None

    # ------------------------------------------------------------------
    # Measurement management
    # ------------------------------------------------------------------

    def add_measurement(self, node_loads: dict, gauge_readings: dict | list) -> None:
        """Store a (load-state, physical-readings) pair for calibration.

        Parameters
        ----------
        node_loads:
            Dict mapping node ID (int or str) to applied load in Newtons,
            same format as ``model.node_loads``.
        gauge_readings:
            Either a ``{gauge_id: strain_value}`` dict, a list of
            ``{"gauge_id": ..., "value": ...}`` dicts, or a full
            ``cbl/bridge/real/state`` MQTT payload.
        """
        parsed = self._parse_gauge_readings(gauge_readings)
        if not parsed:
            raise ValueError(
                "gauge_readings produced no usable strain values. "
                "Pass a {gauge_id: value} dict or a real/state MQTT payload."
            )
        self.measurements.append(({int(k): float(v) for k, v in node_loads.items()}, parsed))

    def clear_measurements(self) -> None:
        """Remove all stored measurement pairs."""
        self.measurements.clear()

    # ------------------------------------------------------------------
    # Optimisation
    # ------------------------------------------------------------------

    def run(
        self,
        method: str = "L-BFGS-B",
        max_iter: int = 300,
        bounds: list[tuple[float, float]] | None = None,
        x0: list[float] | None = None,
    ) -> CalibrationResult:
        """Run the bounded optimiser and return the best calibration result.

        The model is fully restored to its state before the call (original
        calibration scales and node loads) after the optimiser finishes,
        regardless of success or failure.

        Parameters
        ----------
        method:
            ``scipy.optimize.minimize`` method name, e.g. ``"L-BFGS-B"``
            (default, fast bounded gradient descent) or
            ``"differential_evolution"`` (global search, slower).
        max_iter:
            Maximum number of optimiser iterations.
        bounds:
            List of ``(min, max)`` pairs for
            ``[E_scale, angle_A_scale, flat_A_scale]``.
            Defaults to ``[(0.5, 2.0), (0.5, 2.0), (0.5, 2.0)]``.
        x0:
            Initial parameter guess. Defaults to ``[1.0, 1.0, 1.0]``.
            Ignored when ``method="differential_evolution"``.

        Returns
        -------
        CalibrationResult
            Best parameters found, together with diagnostics.
        """
        if not self.measurements:
            raise ValueError("No measurements added. Call add_measurement() first.")

        gauge_defs = self._get_gauge_definitions()
        if not gauge_defs:
            raise ValueError(
                "No strain gauges defined in bridge JSON "
                "(expected 'strain_gauges' list with 'gauge_id'/'ele_id' entries)."
            )

        _bounds = bounds or self.DEFAULT_BOUNDS
        _x0 = x0 or self.DEFAULT_X0

        original_scales = dict(self.model._calibration_scales)
        original_node_loads = dict(self.model.node_loads)

        with self.model._opensees_lock:
            self.model._calibration_in_progress = True
            try:
                x_opt, nrmse, iterations, success = self._run_optimizer(
                    method, max_iter, _bounds, _x0, gauge_defs
                )
            finally:
                self.model._calibration_in_progress = False
                self.model._calibration_scales = original_scales
                self.model.node_loads = original_node_loads
                self.model._solve_current_loads_unlocked()

        return CalibrationResult(
            E_scale=float(x_opt[0]),
            angle_A_scale=float(x_opt[1]),
            flat_A_scale=float(x_opt[2]),
            nrmse=nrmse,
            iterations=iterations,
            success=success,
        )

    def _run_optimizer(
        self,
        method: str,
        max_iter: int,
        bounds: list[tuple[float, float]],
        x0: list[float],
        gauge_defs: list[dict],
    ) -> tuple[np.ndarray, float, int, bool]:
        """Internal: run scipy optimizer; returns (x_opt, nrmse, iterations, success)."""
        try:
            from scipy.optimize import differential_evolution, minimize
        except ImportError as exc:
            raise ImportError(
                "scipy is required for calibration. Install with: pip install scipy"
            ) from exc

        objective = lambda x: self._objective(x, gauge_defs)  # noqa: E731

        if method.lower() == "differential_evolution":
            result = differential_evolution(
                objective,
                bounds=bounds,
                maxiter=max_iter,
                seed=42,
                tol=1e-6,
                workers=1,
                polish=True,
            )
        else:
            result = minimize(
                objective,
                x0=x0,
                method=method,
                bounds=bounds,
                options={"maxiter": max_iter, "ftol": 1e-9},
            )

        return result.x, float(result.fun), int(result.nit), bool(result.success)

    def _objective(self, x: np.ndarray, gauge_defs: list[dict]) -> float:
        """Compute mean NRMSE across all measurement states for parameter vector ``x``."""
        self.model._calibration_scales = {
            "E_scale": float(x[0]),
            "angle_A_scale": float(x[1]),
            "flat_A_scale": float(x[2]),
        }

        total_nrmse = 0.0
        for node_loads, readings in self.measurements:
            self.model.node_loads = dict(node_loads)
            ok = self.model._solve_current_loads_unlocked()
            if ok != 0:
                return 1e6
            simulated = self._read_model_strains(gauge_defs)
            measured = self._measured_strain_vector(gauge_defs, readings)
            total_nrmse += _nrmse(simulated, measured)

        return total_nrmse / len(self.measurements)

    # ------------------------------------------------------------------
    # Apply / persist
    # ------------------------------------------------------------------

    def apply(self, result: CalibrationResult) -> None:
        """Apply a ``CalibrationResult`` to the live model."""
        self.model.apply_calibration(result.to_params())

    def save(self, path: str | Path, result: CalibrationResult | None = None) -> None:
        """Serialise calibration parameters to a JSON file.

        If *result* is provided its full diagnostics are saved. Otherwise only
        the model's current ``_calibration_scales`` are written (useful for
        persisting manually applied params).
        """
        if result is not None:
            data = dataclasses.asdict(result)
        else:
            data = {
                "E_scale": self.model._calibration_scales.get("E_scale", 1.0),
                "angle_A_scale": self.model._calibration_scales.get("angle_A_scale", 1.0),
                "flat_A_scale": self.model._calibration_scales.get("flat_A_scale", 1.0),
                "nrmse": None,
                "iterations": None,
                "success": None,
                "timestamp": time.time(),
            }
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")

    def load(self, path: str | Path) -> CalibrationResult:
        """Load calibration parameters from a JSON file and apply them to the model.

        Returns the ``CalibrationResult`` that was loaded and applied.
        """
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        result = CalibrationResult(
            E_scale=float(raw.get("E_scale", 1.0)),
            angle_A_scale=float(raw.get("angle_A_scale", 1.0)),
            flat_A_scale=float(raw.get("flat_A_scale", 1.0)),
            nrmse=float(raw["nrmse"]) if raw.get("nrmse") is not None else 0.0,
            iterations=int(raw["iterations"]) if raw.get("iterations") is not None else 0,
            success=bool(raw.get("success", True)),
            timestamp=float(raw.get("timestamp", 0.0)),
        )
        self.apply(result)
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_gauge_definitions(self) -> list[dict]:
        if self._gauge_definitions is None:
            self._gauge_definitions = self.model.bridge.get("strain_gauges", [])
        return self._gauge_definitions

    def _read_model_strains(self, gauge_defs: list[dict]) -> np.ndarray:
        """Read absolute ``combined_strain`` from the current model solve for each gauge."""
        return np.array([
            float(self.model.element_results.get(int(g["ele_id"]), {}).get("combined_strain", 0.0))
            for g in gauge_defs
        ])

    def _measured_strain_vector(self, gauge_defs: list[dict], readings: dict) -> np.ndarray:
        """Build a strain vector aligned with ``gauge_defs`` from a readings dict."""
        return np.array([
            float(readings.get(str(g["gauge_id"]), 0.0))
            for g in gauge_defs
        ])

    def _parse_gauge_readings(self, readings: dict | list) -> dict:
        """Parse gauge readings from multiple formats into a ``{gauge_id: value}`` dict."""
        if isinstance(readings, dict):
            # Full real/state payload: delegate to comparison baseline parser
            if any(k in readings for k in ("strain_readings", "physical_strains", "element_ids")):
                parsed = self.model.comparison._parse_strains_from_state(readings)
                return {str(k): float(v) for k, v in parsed.items()} if parsed else {}
            # Plain {gauge_id: value} dict
            return {str(k): float(v) for k, v in readings.items()}
        if isinstance(readings, list):
            # List of {"gauge_id": ..., "value": ...} objects
            result = {}
            for item in readings:
                if isinstance(item, dict) and "gauge_id" in item:
                    val = item.get("value", item.get("strain", item.get("combined_strain")))
                    if val is not None:
                        result[str(item["gauge_id"])] = float(val)
            return result
        raise ValueError(f"Unsupported gauge_readings type: {type(readings).__name__}")


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _nrmse(simulated: np.ndarray, measured: np.ndarray) -> float:
    """Normalised RMSE: RMSE divided by the max absolute measured value."""
    diff = simulated - measured
    rmse = float(np.sqrt(np.mean(diff ** 2)))
    scale = float(np.max(np.abs(measured))) if np.any(measured != 0.0) else 1.0
    return rmse / max(scale, 1e-12)
