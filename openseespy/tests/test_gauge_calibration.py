from __future__ import annotations

from gauge_calibration import GaugeCalibrator, GaugeCalPoint


class StubModel:
    def __init__(self):
        self.bridge = {
            "strain_gauges": [
                {"gauge_id": "G1", "ele_id": 1},
                {"gauge_id": "G2", "ele_id": 2},
            ],
            "gauge_calibration": {
                "enabled": True,
                "path": "gauge_calibration.json",
                "min_load_steps": 2,
            },
        }
        self.bridge_path = __import__("pathlib").Path("bridge_3d_pratt.json")
        self.latest_real_state = None
        self.analysis_completed = True
        self.node_loads = {}
        self.element_results = {
            1: {"combined_strain": 1.0e-4},
            2: {"combined_strain": 2.0e-4},
        }
        self.comparison = self

    def _parse_strains_from_state(self, payload):
        return payload.get("physical_strains", {})

    def _total_applied_load(self):
        return float(sum(self.node_loads.values()))


def test_fit_and_convert_round_trip():
    model = StubModel()
    cal = GaugeCalibrator(model)
    cal.raw_tare = {"G1": 100.0, "G2": 200.0}
    known_scales = {"G1": 1.0e-6, "G2": 2.0e-6}

    model.element_results = {
        1: {"combined_strain": 1.0e-4},
        2: {"combined_strain": 2.0e-4},
    }
    cal.points.append(
        GaugeCalPoint(
            load_n=500.0,
            raw_readings={
                "G1": 100.0 + 1.0e-4 / known_scales["G1"],
                "G2": 200.0 + 2.0e-4 / known_scales["G2"],
            },
            model_strains={"G1": 1.0e-4, "G2": 2.0e-4},
        )
    )
    model.element_results = {
        1: {"combined_strain": 2.0e-4},
        2: {"combined_strain": 4.0e-4},
    }
    cal.points.append(
        GaugeCalPoint(
            load_n=1000.0,
            raw_readings={
                "G1": 100.0 + 2.0e-4 / known_scales["G1"],
                "G2": 200.0 + 4.0e-4 / known_scales["G2"],
            },
            model_strains={"G1": 2.0e-4, "G2": 4.0e-4},
        )
    )

    result = cal.fit(save=False)
    assert result.success
    assert cal.active

    raw = {
        "G1": 100.0 + 1.5e-4 / known_scales["G1"],
        "G2": 200.0 + 3.0e-4 / known_scales["G2"],
    }
    converted = cal.convert(raw)
    assert abs(converted["G1"] - 1.5e-4) < 1e-10
    assert abs(converted["G2"] - 3.0e-4) < 1e-10


def test_prerequisites_skip_without_gauge_cal():
    from damage_detection import DamageDetectionService

    class FakeComparison:
        active = True

        def _parse_strains_from_state(self, payload):
            return payload.get("physical_strains", {})

    class FakeGaugeCal:
        enabled = True
        active = False
        scales = {}

        class path:
            @staticmethod
            def exists():
                return True

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
            self.gauge_calibration = FakeGaugeCal()

    model = FakeModel()
    service = DamageDetectionService(model)
    ok, reason = service._prerequisites_ok()
    assert ok is False
    assert reason == "gauge calibration not active"
