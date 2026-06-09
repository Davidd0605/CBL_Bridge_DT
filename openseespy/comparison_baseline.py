"""Physical-sensor session tare and optional model diagnostics."""

from __future__ import annotations

import time


class ComparisonBaseline:
    """
    Stores per-gauge physical offsets for a monitoring session.
    """

    def __init__(self, app):
        self.app = app
        self.active = False
        self.physical_strains: dict[str, float] = {}
        self.physical_deflections: dict[str, float] = {}
        self.model_strains: dict[int, float] = {}
        self.node_loads: dict = {}
        self.load_n = 0.0
        self.timestamp: float | None = None

    def tare(self, real_state: dict | None = None, strain_readings=None) -> bool:
        """
        Capture physical sensor offsets and optional model diagnostics.

        real_state: latest payload from cbl/bridge/real/state (optional if strain_readings given)
        strain_readings: optional explicit dict or list override for physical strains
        """
        payload = real_state if isinstance(real_state, dict) else {}
        strains = self._parse_strain_readings(strain_readings)
        if not strains:
            strains = self._parse_strains_from_state(payload)
        deflections = self._parse_deflections_from_state(payload)

        if not strains and not deflections:
            return False

        if not getattr(self.app, "analysis_completed", False):
            return False

        self.physical_strains = strains
        self.physical_deflections = deflections
        self.model_strains = {
            int(element_id): float(result["combined_strain"])
            for element_id, result in self.app.element_results.items()
        }
        self.node_loads = dict(self.app.node_loads)
        self.load_n = self.app._total_applied_load()
        self.timestamp = time.time()
        self.active = True
        return True

    def clear(self) -> None:
        self.active = False
        self.physical_strains = {}
        self.physical_deflections = {}
        self.model_strains = {}
        self.node_loads = {}
        self.load_n = 0.0
        self.timestamp = None

    def load_mismatch(self) -> bool:
        """Return whether live load differs from tare load; informational only."""
        if not self.active:
            return False
        return dict(self.app.node_loads) != self.node_loads

    def physical_strain_delta(self, gauge_id: str, current: float) -> float:
        if not self.active:
            return float(current)
        return float(current) - self.physical_strains.get(str(gauge_id), 0.0)

    def physical_deflection_delta(self, sensor_id: str, current: float) -> float:
        if not self.active:
            return float(current)
        return float(current) - self.physical_deflections.get(str(sensor_id), 0.0)

    def model_strain_delta(self, element_id: int, current: float | None = None) -> float:
        if current is None:
            result = self.app.element_results.get(int(element_id), {})
            current = float(result.get("combined_strain", 0.0))
        if not self.active:
            return float(current)
        return float(current) - self.model_strains.get(int(element_id), 0.0)

    def physical_strain_deltas_for_gauges(self, gauge_definitions, measured_strain) -> list[float]:
        measured = list(measured_strain)
        if not self.active:
            return [float(value) for value in measured]
        deltas = []
        for index, gauge in enumerate(gauge_definitions):
            gauge_id = str(gauge["gauge_id"])
            baseline = self.physical_strains.get(gauge_id, 0.0)
            deltas.append(float(measured[index]) - baseline)
        return deltas

    def model_strain_deltas_for_gauges(self, gauge_definitions) -> list[float]:
        if not self.active:
            return [
                float(
                    self.app.element_results.get(gauge["ele_id"], {}).get(
                        "combined_strain", 0.0
                    )
                )
                for gauge in gauge_definitions
            ]
        return [
            self.model_strain_delta(gauge["ele_id"])
            for gauge in gauge_definitions
        ]

    def _parse_strain_readings(self, readings):
        if readings is None:
            return {}
        parsed = {}
        if isinstance(readings, dict):
            for gauge_id, strain in readings.items():
                parsed[str(gauge_id)] = float(strain)
        elif isinstance(readings, list):
            for item in readings:
                if not isinstance(item, dict):
                    continue
                gauge_id = item.get("gauge_id")
                strain = item.get("strain", item.get("combined_strain"))
                if gauge_id is not None and strain is not None:
                    parsed[str(gauge_id)] = float(strain)
        return parsed

    def _parse_strains_from_state(self, payload: dict) -> dict[str, float]:
        strains = {}
        for item in payload.get("strain_readings", []):
            if not isinstance(item, dict):
                continue
            gauge_id = item.get("gauge_id")
            strain = item.get("strain", item.get("combined_strain"))
            if gauge_id is not None and strain is not None:
                strains[str(gauge_id)] = float(strain)

        physical = payload.get("physical_strains")
        if isinstance(physical, dict):
            for gauge_id, strain in physical.items():
                strains[str(gauge_id)] = float(strain)

        element_ids = payload.get("element_ids")
        combined = payload.get("combined_strain")
        gauge_ids = payload.get("gauge_ids")
        if element_ids and combined and gauge_ids and len(element_ids) == len(combined):
            for gauge_id, element_id, strain in zip(gauge_ids, element_ids, combined):
                strains[str(gauge_id)] = float(strain)
        elif element_ids and combined and len(element_ids) == len(combined):
            for element_id, strain in zip(element_ids, combined):
                strains[f"element_{element_id}"] = float(strain)

        return strains

    def _parse_deflections_from_state(self, payload: dict) -> dict[str, float]:
        deflections = {}
        for item in payload.get("sensor_readings", []):
            if not isinstance(item, dict):
                continue
            sensor_id = item.get("sensor_id")
            uy = item.get("total_uy_m", item.get("live_uy_m"))
            if sensor_id is not None and uy is not None:
                deflections[str(sensor_id)] = float(uy)
        return deflections
