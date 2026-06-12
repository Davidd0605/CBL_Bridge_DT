"""Per-gauge empirical calibration: map raw resistance/DAQ readings to strain-equivalent values."""

from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bridge_model import BridgeModel


def load_gauge_calibration_config(bridge: dict) -> dict:
    settings = dict(bridge.get("gauge_calibration", {}))
    settings.setdefault("enabled", False)
    settings.setdefault("path", "gauge_calibration.json")
    settings.setdefault("min_load_steps", 2)
    return settings


@dataclasses.dataclass
class GaugeCalPoint:
    load_n: float
    raw_readings: dict[str, float]
    model_strains: dict[str, float]
    timestamp: float = dataclasses.field(default_factory=time.time)


@dataclasses.dataclass
class GaugeCalFitResult:
    scales: dict[str, dict[str, float]]
    n_points: int
    success: bool
    timestamp: float = dataclasses.field(default_factory=time.time)


class GaugeCalibrator:
    def __init__(self, model: "BridgeModel") -> None:
        self.model = model
        self.config = load_gauge_calibration_config(model.bridge)
        self.enabled = bool(self.config.get("enabled", False))
        self.path = model.bridge_path.with_name(str(self.config.get("path", "gauge_calibration.json")))
        self.min_load_steps = int(self.config.get("min_load_steps", 2))

        self.active = False
        self.raw_tare: dict[str, float] = {}
        self.points: list[GaugeCalPoint] = []
        self.scales: dict[str, dict[str, float]] = {}
        self.last_summary = "Gauge calibration idle."

    def _gauge_definitions(self) -> list[dict]:
        return self.model.bridge.get("strain_gauges", [])

    def _parse_raw(self, readings=None) -> dict[str, float]:
        if readings is not None:
            if isinstance(readings, dict):
                if any(
                    k in readings
                    for k in ("strain_readings", "physical_strains", "element_ids")
                ):
                    parsed = self.model.comparison._parse_strains_from_state(readings)
                else:
                    parsed = {str(k): float(v) for k, v in readings.items()}
            else:
                parsed = {}
        elif isinstance(self.model.latest_real_state, dict):
            parsed = self.model.comparison._parse_strains_from_state(
                self.model.latest_real_state
            )
        else:
            parsed = {}
        return {str(k): float(v) for k, v in parsed.items()} if parsed else {}

    def _model_strains_for_gauges(self) -> dict[str, float]:
        strains = {}
        for gauge in self._gauge_definitions():
            gauge_id = str(gauge["gauge_id"])
            ele_id = int(gauge["ele_id"])
            value = float(
                self.model.element_results.get(ele_id, {}).get("combined_strain", 0.0)
            )
            strains[gauge_id] = value
        return strains

    def set_raw_tare(self, raw: dict | None = None) -> bool:
        parsed = self._parse_raw(raw)
        if not parsed:
            return False
        self.raw_tare = dict(parsed)
        self.last_summary = f"Gauge raw tare set ({len(self.raw_tare)} gauge(s))."
        return True

    def capture_point(self, raw_readings=None) -> bool:
        if not getattr(self.model, "analysis_completed", False):
            self.last_summary = "Gauge cal capture failed: analysis not completed."
            return False
        parsed = self._parse_raw(raw_readings)
        if not parsed:
            self.last_summary = "Gauge cal capture failed: no raw readings."
            return False
        model_strains = self._model_strains_for_gauges()
        point = GaugeCalPoint(
            load_n=float(self.model._total_applied_load()),
            raw_readings=parsed,
            model_strains=model_strains,
        )
        self.points.append(point)
        self.last_summary = (
            f"Gauge cal point {len(self.points)} captured @ {point.load_n:.1f} N."
        )
        return True

    def fit(self, save: bool = True) -> GaugeCalFitResult:
        if not self.raw_tare:
            result = GaugeCalFitResult(scales={}, n_points=len(self.points), success=False)
            self.last_summary = "Gauge cal fit failed: raw tare not set."
            return result
        if len(self.points) < self.min_load_steps:
            result = GaugeCalFitResult(
                scales={}, n_points=len(self.points), success=False
            )
            self.last_summary = (
                f"Gauge cal fit failed: need {self.min_load_steps} load step(s), "
                f"have {len(self.points)}."
            )
            return result

        fitted: dict[str, dict[str, float]] = {}
        for gauge in self._gauge_definitions():
            gauge_id = str(gauge["gauge_id"])
            xs = []
            ys = []
            for point in self.points:
                if gauge_id not in point.raw_readings:
                    continue
                if gauge_id not in point.model_strains:
                    continue
                raw_val = float(point.raw_readings[gauge_id])
                tare = float(self.raw_tare.get(gauge_id, 0.0))
                x = raw_val - tare
                y = float(point.model_strains[gauge_id])
                xs.append(x)
                ys.append(y)
            if len(xs) < 1:
                continue
            scale, r2 = _through_origin_ls(xs, ys)
            fitted[gauge_id] = {"scale": scale, "offset": 0.0, "r2": r2}

        if not fitted:
            result = GaugeCalFitResult(scales={}, n_points=len(self.points), success=False)
            self.last_summary = "Gauge cal fit failed: no gauge scales computed."
            return result

        self.scales = fitted
        self.active = True
        result = GaugeCalFitResult(
            scales=dict(fitted), n_points=len(self.points), success=True
        )
        if save:
            self.save()
        n_bad = sum(1 for s in fitted.values() if s["r2"] < 0.5 or s["scale"] <= 0.0)
        warn = f" ({n_bad} poor fit)" if n_bad else ""
        self.last_summary = (
            f"Gauge calibration active: {len(fitted)} gauge(s), "
            f"{len(self.points)} point(s){warn}."
        )
        return result

    def convert(self, raw_dict: dict[str, float]) -> dict[str, float]:
        if not self.active or not self.scales:
            return {str(k): float(v) for k, v in raw_dict.items()}
        converted = {}
        for gauge_id, raw_val in raw_dict.items():
            gid = str(gauge_id)
            if gid not in self.scales:
                converted[gid] = float(raw_val)
                continue
            scale = float(self.scales[gid]["scale"])
            tare = float(self.raw_tare.get(gid, 0.0))
            offset = float(self.scales[gid].get("offset", 0.0))
            converted[gid] = scale * (float(raw_val) - tare) + offset
        return converted

    def clear(self) -> None:
        self.active = False
        self.raw_tare = {}
        self.points = []
        self.scales = {}
        self.last_summary = "Gauge calibration cleared."

    def save(self, path: str | Path | None = None) -> None:
        out = Path(path) if path is not None else self.path
        data = {
            "active": self.active,
            "raw_tare": self.raw_tare,
            "scales": self.scales,
            "n_points": len(self.points),
            "timestamp": time.time(),
        }
        out.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def load(self, path: str | Path | None = None) -> bool:
        src = Path(path) if path is not None else self.path
        if not src.exists():
            return False
        try:
            data = json.loads(src.read_text(encoding="utf-8"))
            self.raw_tare = {str(k): float(v) for k, v in data.get("raw_tare", {}).items()}
            self.scales = {
                str(gid): {
                    "scale": float(params.get("scale", 1.0)),
                    "offset": float(params.get("offset", 0.0)),
                    "r2": float(params.get("r2", 0.0)),
                }
                for gid, params in data.get("scales", {}).items()
            }
            self.active = bool(data.get("active", False)) and bool(self.scales)
            if self.active:
                self.last_summary = (
                    f"Gauge calibration loaded: {len(self.scales)} gauge(s)."
                )
            return self.active
        except Exception:
            return False


def _through_origin_ls(xs: list[float], ys: list[float]) -> tuple[float, float]:
    denom = sum(x * x for x in xs)
    if denom < 1e-30:
        return 0.0, 0.0
    scale = sum(x * y for x, y in zip(xs, ys)) / denom
    ss_res = sum((y - scale * x) ** 2 for x, y in zip(xs, ys))
    ss_tot = sum(y * y for y in ys)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-30 else 0.0
    return scale, r2
