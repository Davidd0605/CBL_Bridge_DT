"""Background damage detection using SensitivityMatrix and MQTT publishing."""

from __future__ import annotations

import os
import threading
import time
from typing import Any

import numpy as np

from SensitivityMatrix import SensitivityMatrix


def _nearest_gauge_index(element_id: int, gauge_definitions: list[dict]) -> int:
    if not gauge_definitions:
        return 0
    gauge_elements = [int(g["ele_id"]) for g in gauge_definitions]
    nearest = min(
        range(len(gauge_elements)),
        key=lambda index: abs(gauge_elements[index] - element_id),
    )
    return int(nearest)


def load_detection_config(bridge: dict, element_ids: list[int] | None = None) -> tuple[list[dict], list[dict], dict]:
    """
    Load strain gauges and auto-generate one damage scenario per element.

    Returns (gauge_definitions, damage_scenarios, settings).
    """
    settings = dict(bridge.get("damage_detection", {}))
    settings.setdefault("alpha", 0.8)
    settings.setdefault("mac_weight", 10.0)
    settings.setdefault("healthy_mac_threshold", 0.95)
    settings.setdefault("healthy_ortho_threshold", 0.10)
    settings.setdefault("healthy_nrmse_threshold", 0.10)
    settings.setdefault("require_detector_agreement", False)
    settings.setdefault("debounce_seconds", 1.5)
    settings.setdefault("min_interval_seconds", 5.0)

    gauge_definitions = [
        {"gauge_id": str(item["gauge_id"]), "ele_id": int(item["ele_id"])}
        for item in bridge.get("strain_gauges", [])
        if item.get("gauge_id") is not None and item.get("ele_id") is not None
    ]

    if element_ids is None:
        element_ids = sorted(int(element["id"]) for element in bridge.get("elements", []))

    alpha = float(settings["alpha"])
    damage_scenarios = []
    for element_id in element_ids:
        damage_scenarios.append(
            {
                "scenario_id": f"ele_{element_id}",
                "element_ids": [element_id],
                "alpha": alpha,
                "gauge_index": _nearest_gauge_index(element_id, gauge_definitions),
            }
        )

    return gauge_definitions, damage_scenarios, settings


def measured_strain_vector(
    gauge_definitions: list[dict],
    real_state: dict | None,
    comparison,
) -> list[float] | None:
    """Build raw physical strain readings in gauge order."""
    if not gauge_definitions:
        return None
    if not isinstance(real_state, dict):
        return None

    strains = comparison._parse_strains_from_state(real_state)
    if not strains:
        return None

    measured = []
    for gauge in gauge_definitions:
        gauge_id = str(gauge["gauge_id"])
        if gauge_id not in strains:
            return None
        measured.append(float(strains[gauge_id]))
    return measured


def scenario_publish_dict(scenario: dict | None, mac: float | None = None, extra: dict | None = None) -> dict | None:
    if scenario is None:
        return None
    payload = {
        "scenario_id": scenario.get("scenario_id"),
        "element_ids": list(scenario.get("element_ids", [])),
    }
    if mac is not None:
        payload["MAC"] = mac
    if extra:
        payload.update(extra)
    return payload


class DamageDetectionService:
    """Debounced background detection cycles tied to real-bridge MQTT state."""

    def __init__(self, model):
        self.model = model
        self.sensitivity = SensitivityMatrix(model)
        gauge_definitions, damage_scenarios, settings = load_detection_config(
            model.bridge,
            [element["id"] for element in model.elements],
        )
        self.gauge_definitions = gauge_definitions
        self.damage_scenarios = damage_scenarios
        self.settings = settings
        self.sensitivity.define_gauges(gauge_definitions)
        self.sensitivity.define_damage_scenarios(damage_scenarios)

        self.flagged_element_ids: list[int] = []
        self.detection_in_progress = False
        self.last_result: dict[str, Any] | None = None
        self.last_summary = "Damage detection idle."
        self.last_error: str | None = None

        self._schedule_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_run_time = 0.0

        env_debounce = os.environ.get("DAMAGE_DETECT_DEBOUNCE_S")
        env_interval = os.environ.get("DAMAGE_DETECT_MIN_INTERVAL_S")
        if env_debounce is not None:
            self.settings["debounce_seconds"] = float(env_debounce)
        if env_interval is not None:
            self.settings["min_interval_seconds"] = float(env_interval)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._worker_loop,
            name="damage-detection",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._schedule_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def schedule(self) -> None:
        self._schedule_event.set()

    def _worker_loop(self) -> None:
        debounce = float(self.settings.get("debounce_seconds", 1.5))
        while not self._stop_event.is_set():
            if not self._schedule_event.wait(timeout=0.25):
                continue
            self._schedule_event.clear()
            deadline = time.time() + debounce
            while time.time() < deadline and not self._stop_event.is_set():
                if self._schedule_event.wait(timeout=0.1):
                    self._schedule_event.clear()
                    deadline = time.time() + debounce
            if self._stop_event.is_set():
                break
            self._run_cycle_if_due()

    def _run_cycle_if_due(self) -> None:
        min_interval = float(self.settings.get("min_interval_seconds", 5.0))
        elapsed = time.time() - self._last_run_time
        if elapsed < min_interval:
            return
        if self.detection_in_progress:
            return
        self._last_run_time = time.time()
        self.run_cycle()

    def _prerequisites_ok(self) -> tuple[bool, str]:
        if not self.gauge_definitions:
            return False, "no strain_gauges configured"
        if not getattr(self.model, "analysis_completed", False):
            return False, "analysis not completed"
        mode = getattr(self.model, "comparison_mode", "delta")
        if mode == "delta" and not self.model.comparison.active:
            return False, "comparison tare not active"
        measured = measured_strain_vector(
            self.gauge_definitions,
            self.model.latest_real_state,
            self.model.comparison,
        )
        if measured is None:
            return False, "missing strain readings for configured gauges"
        return True, ""

    def run_cycle(self) -> dict[str, Any] | None:
        ok, reason = self._prerequisites_ok()
        if not ok:
            self.last_summary = f"Damage detection skipped: {reason}."
            return None

        measured = measured_strain_vector(
            self.gauge_definitions,
            self.model.latest_real_state,
            self.model.comparison,
        )
        if measured is None:
            self.last_summary = "Damage detection skipped: incomplete strain readings."
            return None

        mode = getattr(self.model, "comparison_mode", "delta")
        lock = getattr(self.model, "_opensees_lock", None)
        if lock is None:
            return self._run_cycle_locked(measured, mode)

        with lock:
            return self._run_cycle_locked(measured, mode)

    def _run_cycle_locked(self, measured: list[float], mode: str) -> dict[str, Any] | None:
        self.detection_in_progress = True
        self.last_error = None
        try:
            self.sensitivity.run_healthy(mode)
            if mode == "delta":
                self.sensitivity.measured_strain = self.sensitivity._physical_delta_strain(
                    measured
                )
            else:
                self.sensitivity.measured_strain = np.asarray(measured, dtype=float)
            health = self.sensitivity.is_healthy(
                mac_threshold=float(self.settings["healthy_mac_threshold"]),
                ortho_threshold=float(self.settings["healthy_ortho_threshold"]),
                nrmse_threshold=float(self.settings["healthy_nrmse_threshold"]),
            )

            if health["healthy"]:
                self.flagged_element_ids = []
                result = self._build_result(
                    healthy=True,
                    health_metrics=health,
                    detection=None,
                    flagged_ids=[],
                )
            else:
                self.sensitivity.build_sensitivity(measured, verbose=False, mode=mode)
                detection = self.sensitivity.detect(mac_weight=float(self.settings["mac_weight"]))
                flagged_ids = self._select_flagged_ids(detection)
                self.flagged_element_ids = flagged_ids
                result = self._build_result(
                    healthy=False,
                    health_metrics=health,
                    detection=detection,
                    flagged_ids=flagged_ids,
                )

            self.model.reset_damage()
            self.model._solve_current_loads()
            self.last_result = result
            self.last_summary = self._format_summary(result)
            self._publish_result(result)
            if getattr(self.model, "_dispatch", None):
                self.model._dispatch(self.model._update_status)
            return result
        except Exception as exc:  # noqa: BLE001 - surface to status/MQTT
            self.last_error = str(exc)
            self.last_summary = f"Damage detection failed: {exc}"
            return None
        finally:
            self.detection_in_progress = False

    def _select_flagged_ids(self, detection: dict) -> list[int]:
        require_agreement = bool(self.settings.get("require_detector_agreement", False))
        ortho = detection["best_ortho"]["scenario"]
        nrmse = detection["best_nrmse"]["scenario"]
        if require_agreement and not detection.get("agreement"):
            return []
        scenario = nrmse if nrmse is not None else ortho
        if scenario is None:
            return []
        return [int(element_id) for element_id in scenario.get("element_ids", [])]

    def _build_result(
        self,
        *,
        healthy: bool,
        health_metrics: dict | None,
        detection: dict | None,
        flagged_ids: list[int],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "type": "damage_detection",
            "timestamp": time.time(),
            "healthy": healthy,
            "flagged_element_ids": flagged_ids,
            "comparison_mode": getattr(self.model, "comparison_mode", "delta"),
            "comparison_tare_active": self.model.comparison.active,
            "agreement": None,
            "best_ortho": None,
            "best_nrmse": None,
            "is_healthy_metrics": health_metrics,
        }
        if detection is not None:
            result["agreement"] = detection.get("agreement")
            result["best_ortho"] = scenario_publish_dict(
                detection["best_ortho"]["scenario"],
                mac=detection["best_ortho"].get("MAC"),
                extra={"OrthoError": detection["best_ortho"].get("OrthoError")},
            )
            result["best_nrmse"] = scenario_publish_dict(
                detection["best_nrmse"]["scenario"],
                mac=detection["best_nrmse"].get("MAC"),
                extra={"NRMSE_Error": detection["best_nrmse"].get("NRMSE_Error")},
            )
        return result

    def _format_summary(self, result: dict) -> str:
        if result.get("healthy"):
            metrics = result.get("is_healthy_metrics") or {}
            return (
                "Damage detection: healthy "
                f"(MAC {metrics.get('MAC', 0):.3f}, "
                f"ortho {metrics.get('OrthoError', 0):.3f})."
            )
        flagged = result.get("flagged_element_ids") or []
        best = result.get("best_nrmse") or {}
        scenario_id = best.get("scenario_id", "?")
        return (
            f"Damage detection: flagged element(s) {flagged} "
            f"(best scenario {scenario_id})."
        )

    def _publish_result(self, result: dict) -> None:
        mqtt = getattr(self.model, "mqtt", None)
        if mqtt is not None and hasattr(mqtt, "publish_damage_detection"):
            mqtt.publish_damage_detection(self.model, result)
