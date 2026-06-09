from __future__ import annotations

import numpy as np

from damage_detection import DamageDetectionService
from SensitivityMatrix import SensitivityMatrix


class FakeComparison:
    def __init__(self):
        self.active = True
        self.physical_strains = {"S1": 100.0}
        self.model_strains = {1: 5.0}

    def load_mismatch(self):
        return True

    def _parse_strains_from_state(self, payload):
        return payload.get("physical_strains", {})

    def physical_strain_deltas_for_gauges(self, gauge_definitions, measured_strain):
        return [
            float(measured_strain[index])
            - self.physical_strains.get(str(gauge["gauge_id"]), 0.0)
            for index, gauge in enumerate(gauge_definitions)
        ]

    def model_strain_deltas_for_gauges(self, gauge_definitions):
        return [
            float(self.app.element_results[gauge["ele_id"]]["combined_strain"])
            - self.model_strains.get(gauge["ele_id"], 0.0)
            for gauge in gauge_definitions
        ]


class FakeModel:
    def __init__(self):
        self.bridge = {
            "strain_gauges": [{"gauge_id": "S1", "ele_id": 1}],
            "damage_detection": {"min_interval_seconds": 0.0},
        }
        self.elements = [{"id": 1}]
        self.comparison = FakeComparison()
        self.comparison.app = self
        self.comparison_mode = "delta"
        self.analysis_completed = True
        self.latest_real_state = {"physical_strains": {"S1": 110.0}}
        self.element_results = {1: {"combined_strain": 20.0}}
        self.damage_overrides = {}

    def set_damage(self, element_ids, alpha=0.8):
        self.damage_overrides = {element_id: alpha for element_id in element_ids}
        self.element_results = {1: {"combined_strain": 18.0}}

    def reset_damage(self):
        self.damage_overrides = {}
        self.element_results = {1: {"combined_strain": 20.0}}

    def _solve_current_loads(self):
        return 0


def test_prerequisites_allow_load_change_after_session_tare():
    model = FakeModel()
    service = DamageDetectionService(model)

    ok, reason = service._prerequisites_ok()

    assert ok is True
    assert reason == ""


def test_delta_mode_compares_physical_offset_to_current_model_absolute():
    model = FakeModel()
    sensitivity = SensitivityMatrix(model)
    sensitivity.define_gauges([{"gauge_id": "S1", "ele_id": 1}])
    sensitivity.define_damage_scenarios(
        [{"scenario_id": "ele_1", "element_ids": [1], "alpha": 0.8}]
    )

    healthy = sensitivity.run_healthy("delta")
    S, Error, _MAC, _OrthoError = sensitivity.build_sensitivity(
        [110.0],
        verbose=False,
        mode="delta",
    )

    np.testing.assert_allclose(healthy, [20.0])
    np.testing.assert_allclose(sensitivity.measured_strain, [10.0])
    np.testing.assert_allclose(S[:, 0], [18.0])
    np.testing.assert_allclose(Error[:, 0], [8.0])
