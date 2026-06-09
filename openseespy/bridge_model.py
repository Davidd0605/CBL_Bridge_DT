"""Bridge domain model and MQTT-command business logic."""

from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path

import openseespy.opensees as ops

from bridge_mqtt import BridgeMQTTPublisher
from comparison_baseline import ComparisonBaseline
from damage_detection import DamageDetectionService


class BridgeModel:
    """Non-UI bridge state, analysis, comparison mode, and MQTT handling."""

    def __init__(
        self,
        *,
        dispatch_to_main=None,
        info_callback=None,
        status_callback=None,
        sensor_callback=None,
        redraw_callback=None,
    ):
        self._dispatch = dispatch_to_main or (lambda callback, *args: callback(*args))
        self._info_callback = info_callback
        self._status_callback = status_callback
        self._sensor_callback = sensor_callback
        self._redraw_callback = redraw_callback

        self.bridge_path = Path(__file__).with_name("bridge_3d_pratt.json")
        self.bridge = self._load_bridge(self.bridge_path)
        self.node_coords = {
            int(node["id"]): (float(node["x"]), float(node["y"]), float(node["z"]))
            for node in self.bridge["nodes"]
        }
        self.node_labels = {
            int(node["id"]): node.get("label", str(node["id"]))
            for node in self.bridge["nodes"]
        }
        self.elements = []
        for element in self.bridge["elements"]:
            parsed = dict(element)
            parsed["id"] = int(element["id"])
            parsed["type"] = element.get("type", "member")
            parsed["i"] = int(element["i"])
            parsed["j"] = int(element["j"])
            self.elements.append(parsed)

        self.profiles = self.bridge.get("steel_profiles", {})
        self.profile_rules = self.bridge.get("member_profile_rules", [])
        self.supports = self.bridge.get("supports", [])
        self.sensor_points = self.bridge.get("deflection_sensor_points", [])
        self.load_cells = self.bridge.get("load_cells", [])
        self.load_points = self.bridge.get("recommended_load_application_nodes", [])

        self.gravity = 9.80665
        self.poisson_ratio = 0.30
        self.include_self_weight = True
        self.reference_load_n = 100.0
        self.load_step = 0.1
        self.node_loads = {}

        self.element_results = {}
        self.support_reactions = {}
        self.dead_load_displacements = {}
        self.dead_load_strains = {}
        self.comparison = ComparisonBaseline(self)
        self.comparison_mode = "delta"
        self.latest_real_state = None
        self.total_self_weight_n = 0.0
        self.analysis_completed = False
        self.model_errors = []
        self.model_warnings = []
        self.mqtt = BridgeMQTTPublisher(
            load_callback=self._on_mqtt_load,
            command_callback=self._on_mqtt_command,
            real_state_callback=self._on_mqtt_real_state,
        )
        self._geometry_published = False
        self.damage_overrides = {}
        self._calibration_scales = {"E_scale": 1.0, "angle_A_scale": 1.0, "flat_A_scale": 1.0}
        self._calibration_in_progress = False
        self._calibration_path = self.bridge_path.with_name("calibration.json")
        self._auto_load_calibration()
        self._opensees_lock = threading.RLock()
        self.detection = DamageDetectionService(self)

        self.defo_scale = 250.0
        self.visual_defo_scale = self.defo_scale
        self.auto_defo_scale = True
        self.min_deformed_offset_px = 30.0

        self.canvas_width = 980
        self.canvas_height = 620
        self.margin = 80
        self._set_view_transform()

        self.default_load_node = int(
            self.bridge.get("midspan_sensor_node")
            or self.load_points[len(self.load_points) // 2]["node"]
        )
        self.selected_load_node = self.default_load_node
        self._load_node_text = f"node {self.selected_load_node}"
        self._load_force_text = f"{self.reference_load_n:.1f}"

        self._last_info_text = ""
        self._last_status_text = ""
        self._last_sensor_text = ""

        self._solve_current_loads()
        self._set_info_message(self._info_message("3D model ready."))
        self._update_status()
        self._publish_mqtt()
        self.detection.start()

    def _load_bridge(self, path):
        with path.open("r", encoding="utf-8") as bridge_file:
            return json.load(bridge_file)

    def _auto_load_calibration(self) -> None:
        """Apply saved calibration.json scales if the file exists next to the bridge JSON."""
        if not self._calibration_path.exists():
            return
        try:
            data = json.loads(self._calibration_path.read_text(encoding="utf-8"))
            for key in ("E_scale", "angle_A_scale", "flat_A_scale"):
                if key in data and data[key] is not None:
                    self._calibration_scales[key] = float(data[key])
            self.model_warnings.append(
                f"Calibration loaded from {self._calibration_path.name}: "
                f"E\u00d7{self._calibration_scales['E_scale']:.4f}, "
                f"angle_A\u00d7{self._calibration_scales['angle_A_scale']:.4f}, "
                f"flat_A\u00d7{self._calibration_scales['flat_A_scale']:.4f}"
            )
        except Exception as exc:
            self.model_warnings.append(
                f"Could not load calibration from {self._calibration_path.name}: {exc}"
            )

    def _set_info_message(self, text):
        self._last_info_text = text
        if self._info_callback is not None:
            self._info_callback(text)

    def _set_status_message(self, text):
        self._last_status_text = text
        if self._status_callback is not None:
            self._status_callback(text)

    def _set_sensor_summary(self, text):
        self._last_sensor_text = text
        if self._sensor_callback is not None:
            self._sensor_callback(text)

    def _trigger_redraw(self):
        if self._redraw_callback is not None:
            self._redraw_callback()

    def _publish_mqtt(self):
        if not self.mqtt.enabled:
            return
        if not self._geometry_published:
            if self.mqtt.publish_geometry(self):
                self._geometry_published = True
        if self._calibration_in_progress:
            return
        if self.detection.detection_in_progress:
            return
        self.mqtt.publish_state(self)

    def _on_mqtt_load(self, payload):
        self._dispatch(self._apply_mqtt_load_payload, payload)

    def _on_mqtt_command(self, payload):
        self._dispatch(self._apply_mqtt_command_payload, payload)

    def _on_mqtt_real_state(self, payload):
        self._dispatch(self._store_real_state_payload, payload)

    def _store_real_state_payload(self, payload):
        if isinstance(payload, dict):
            self.latest_real_state = payload
            self.detection.schedule()

    def _apply_mqtt_command_payload(self, payload):
        if not isinstance(payload, dict):
            self.mqtt._last_error = "command payload must be a JSON object"
            self._update_status()
            return

        result = self._handle_command(payload)
        if result["ok"]:
            self._set_info_message(self._info_message(result["message"]))
        else:
            self.mqtt._last_error = result["error"]
            if result.get("message"):
                self._set_info_message(result["message"])
        self._update_status()
        self._publish_mqtt()
        self._trigger_redraw()

    def _handle_command(self, payload):
        action = payload.get("action")
        if action == "tare":
            readings = payload.get("readings", payload.get("strains"))
            if self.tare(strain_readings=readings):
                self.detection.schedule()
                return {"ok": True, "message": self._tare_success_message()}
            return {
                "ok": False,
                "error": "tare failed: missing physical readings",
                "message": (
                    "Tare failed: need physical sensor readings from "
                    "cbl/bridge/real/state or a 'readings' field in the command."
                ),
            }

        if action == "clear_tare":
            self.clear_tare()
            return {
                "ok": True,
                "message": "Sensor tare cleared for error comparison.",
            }

        if action == "tare_physical_strains":
            readings = payload.get("readings", payload.get("strains"))
            if readings is None:
                return {
                    "ok": False,
                    "error": "tare_physical_strains requires 'readings'",
                    "message": "Physical strain tare failed.",
                }
            if self.tare(strain_readings=readings):
                return {"ok": True, "message": self._tare_success_message()}
            return {
                "ok": False,
                "error": "physical strain tare failed",
                "message": "Physical strain tare failed.",
            }

        if action in ("set_comparison_mode", "comparison_mode"):
            mode = payload.get("mode")
            try:
                mode = self.set_comparison_mode(mode)
            except ValueError as exc:
                return {"ok": False, "error": str(exc), "message": str(exc)}
            return {"ok": True, "message": f"Comparison mode set to '{mode}'."}

        if action == "apply_calibration":
            params = payload.get("params") or payload.get("scales") or {}
            if not isinstance(params, dict) or not params:
                return {
                    "ok": False,
                    "error": "apply_calibration requires a 'params' object with scale factors",
                    "message": "apply_calibration requires 'params': {E_scale, angle_A_scale, flat_A_scale}",
                }
            self.apply_calibration(params)
            scales = self._calibration_scales
            return {
                "ok": True,
                "message": (
                    f"Calibration applied: E\u00d7{scales['E_scale']:.4f}, "
                    f"angle_A\u00d7{scales['angle_A_scale']:.4f}, "
                    f"flat_A\u00d7{scales['flat_A_scale']:.4f}."
                ),
            }

        if action == "reset_calibration":
            self.reset_calibration()
            return {"ok": True, "message": "Calibration reset to nominal values."}

        return {
            "ok": False,
            "error": f"unknown command action: {action!r}",
            "message": "",
        }

    def _apply_mqtt_load_payload(self, payload):
        try:
            node_loads, selected_node = self._parse_mqtt_load_payload(payload)
        except ValueError as exc:
            self.mqtt._last_error = f"load message error: {exc}"
            self._update_status()
            return

        previous_loads = dict(self.node_loads)
        previous_selected_node = self.selected_load_node
        self.node_loads = node_loads
        if selected_node is not None:
            self.selected_load_node = selected_node

        ok = self._solve_current_loads()
        if ok != 0:
            self.node_loads = previous_loads
            self.selected_load_node = previous_selected_node
            self._solve_current_loads()
            self._set_info_message("MQTT load rejected: analysis failed.")
            return

        self._set_info_message(
            self._info_message(
                f"MQTT load applied: {self._total_applied_load():.1f} N total."
            )
        )
        self._update_status()
        self._trigger_redraw()

    def _parse_mqtt_load_payload(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")
        if "node_loads" in payload:
            node_loads = self._parse_mqtt_node_loads(payload["node_loads"])
            return node_loads, self._mqtt_selected_node(payload, node_loads)
        if "loads" in payload:
            node_loads = self._parse_mqtt_node_loads(payload["loads"])
            return node_loads, self._mqtt_selected_node(payload, node_loads)
        if "node" not in payload:
            raise ValueError("expected 'node' with 'load_n', or 'node_loads'")
        node = self._validated_load_node(payload["node"])
        load = self._mqtt_load_value(payload)
        return ({node: load} if load > 0.0 else {}, node)

    def _parse_mqtt_node_loads(self, raw_loads):
        parsed = {}
        if isinstance(raw_loads, dict):
            iterable = raw_loads.items()
        elif isinstance(raw_loads, list):
            iterable = (
                (item.get("node"), self._mqtt_load_value(item))
                for item in raw_loads
                if isinstance(item, dict)
            )
        else:
            raise ValueError("'node_loads' must be an object or list")

        for raw_node, raw_load in iterable:
            node = self._validated_load_node(raw_node)
            load = self._validated_mqtt_load(raw_load)
            if load > 0.0:
                parsed[node] = load
        return parsed

    def _mqtt_selected_node(self, payload, node_loads):
        if "selected_load_node" in payload:
            return self._validated_load_node(payload["selected_load_node"])
        if "node" in payload:
            return self._validated_load_node(payload["node"])
        if node_loads:
            return next(iter(node_loads))
        return None

    def _mqtt_load_value(self, payload):
        for key in ("load_n", "force_n", "weight_n", "load"):
            if key in payload:
                return self._validated_mqtt_load(payload[key])
        raise ValueError("expected load value key 'load_n'")

    def _validated_load_node(self, raw_node):
        try:
            node = int(raw_node)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid load node {raw_node!r}") from exc
        if node not in self.node_coords:
            raise ValueError(f"unknown load node {node}")
        return node

    def _validated_mqtt_load(self, raw_load):
        try:
            load = float(raw_load)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid load value {raw_load!r}") from exc
        if load < 0.0:
            raise ValueError("load must be zero or positive")
        return load

    def _project(self, x, y, z):
        return x + 0.45 * z, y + 0.28 * z

    def _set_view_transform(self):
        projected = [self._project(*coord) for coord in self.node_coords.values()]
        xs = [coord[0] for coord in projected]
        ys = [coord[1] for coord in projected]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        width = max(max_x - min_x, 1e-9)
        height = max(max_y - min_y, 1e-9)
        usable_width = self.canvas_width - 2 * self.margin
        usable_height = self.canvas_height - 2 * self.margin
        self.scale = min(usable_width / width, usable_height / height)
        self.origin_x = self.margin - min_x * self.scale
        self.origin_y = self.canvas_height - self.margin + min_y * self.scale

    def world_to_canvas(self, x, y, z):
        px, py = self._project(x, y, z)
        cx = self.origin_x + px * self.scale
        cy = self.origin_y - py * self.scale
        return cx, cy

    def canvas_to_projected_world(self, cx, cy):
        x = (cx - self.origin_x) / self.scale
        y = (self.origin_y - cy) / self.scale
        return x, y

    def _normalize_support_fixity(self, support):
        fixity = support.get("fixity", [])
        if len(fixity) == 3:
            return [int(fixity[0]), int(fixity[1]), int(fixity[2]), 0, 0, 0]
        if len(fixity) == 6:
            return [int(value) for value in fixity]
        raise ValueError(
            f"Support at node {support.get('node')} must have 3 or 6 fixity values."
        )

    def _profile_for_element(self, element):
        element_type = element.get("type", "").lower()
        explicit = element.get("profile")
        if explicit:
            return explicit
        for rule in self.profile_rules:
            markers = [marker.lower() for marker in rule.get("match_type_contains", [])]
            if any(marker in element_type for marker in markers):
                return rule.get("profile")
        if "brace" in element_type or "diagonal" in element_type:
            return "flat_bar_15x3"
        return "angle_15x15x3"

    def _section_for_element(self, element):
        profile_name = self._profile_for_element(element)
        profile = self.profiles.get(profile_name, {})
        area = float(element.get("A", profile.get("A", 8.1e-5)))
        elastic_modulus = float(element.get("E", profile.get("E", 200e9)))
        e_scale = self._calibration_scales.get("E_scale", 1.0)
        a_key = "flat_A_scale" if profile_name == "flat_bar_15x3" else "angle_A_scale"
        a_scale = self._calibration_scales.get(a_key, 1.0)
        area = area * a_scale
        elastic_modulus = elastic_modulus * e_scale
        density = float(element.get("density", profile.get("density", 7850.0)))
        yield_stress = float(
            element.get("yield_stress", profile.get("yield_stress", 250e6))
        )
        shear_modulus = elastic_modulus / (2.0 * (1.0 + self.poisson_ratio))
        if profile_name == "flat_bar_15x3":
            width = float(profile.get("width_m", 0.015))
            thickness = float(profile.get("thickness_m", 0.003))
            iy = thickness * width**3 / 12.0
            iz = width * thickness**3 / 12.0
            torsion = width * thickness**3 / 3.0
            depth = width
        else:
            leg = float(profile.get("leg_length_m", 0.015))
            thickness = float(profile.get("thickness_m", 0.003))
            iy = 1.5e-9
            iz = 1.5e-9
            torsion = (2.0 * leg - thickness) * thickness**3 / 3.0
            depth = leg
        iy = float(element.get("Iy", iy))
        iz = float(element.get("Iz", iz))
        torsion = float(element.get("J", torsion))
        depth = float(element.get("depth", depth))
        section_modulus = max(iy, iz) / max(depth / 2.0, 1e-9)
        if area <= 0.0 or elastic_modulus <= 0.0 or iy <= 0.0 or iz <= 0.0:
            raise ValueError(f"Element {element['id']} has invalid section properties.")
        if density < 0.0 or yield_stress <= 0.0:
            raise ValueError(f"Element {element['id']} has invalid material properties.")
        section = {
            "profile": profile_name,
            "A": area,
            "E": elastic_modulus,
            "G": shear_modulus,
            "J": torsion,
            "Iy": iy,
            "Iz": iz,
            "S": section_modulus,
            "density": density,
            "yield_stress": yield_stress,
        }
        alpha = self.damage_overrides.get(element["id"])
        if alpha is not None:
            section["E"] = section["E"] * alpha
            section["G"] = section["G"] * alpha
        return section

    def set_damage(self, element_ids, alpha=0.80):
        with self._opensees_lock:
            self.damage_overrides = {ele_id: alpha for ele_id in element_ids}

    def reset_damage(self):
        with self._opensees_lock:
            self.damage_overrides = {}

    def apply_calibration(self, params: dict) -> None:
        """Apply material/geometry scale factors and rebuild the model.

        Args:
            params: dict with any of: E_scale, angle_A_scale, flat_A_scale.
                    Values are multiplied against nominal profile properties.
        """
        with self._opensees_lock:
            for key in ("E_scale", "angle_A_scale", "flat_A_scale"):
                if key in params:
                    self._calibration_scales[key] = float(params[key])
            self._solve_current_loads_unlocked()
        self._set_info_message(
            self._info_message(
                f"Calibration applied: E\u00d7{self._calibration_scales['E_scale']:.4f}, "
                f"angle_A\u00d7{self._calibration_scales['angle_A_scale']:.4f}, "
                f"flat_A\u00d7{self._calibration_scales['flat_A_scale']:.4f}."
            )
        )
        self._update_status()
        self.mqtt.publish_calibration(self, {"calibration_scales": dict(self._calibration_scales)})
        self._trigger_redraw()

    def reset_calibration(self) -> None:
        """Reset all calibration scale factors to 1.0 and rebuild the model."""
        self.apply_calibration({"E_scale": 1.0, "angle_A_scale": 1.0, "flat_A_scale": 1.0})
        self._set_info_message(self._info_message("Calibration reset to nominal values."))

    def _element_length(self, element):
        return self._distance(
            self.node_coords[element["i"]], self.node_coords[element["j"]]
        )

    def _distance(self, p1, p2):
        return math.sqrt(
            (p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2 + (p1[2] - p2[2]) ** 2
        )

    def _validate_bridge_data(self):
        errors = []
        node_ids = set(self.node_coords)
        if not self.supports:
            errors.append("At least one support is required.")
        if not self.load_points:
            errors.append("At least one load point is required.")
        for support in self.supports:
            node = int(support.get("node", -1))
            if node not in node_ids:
                errors.append(f"Support node {node} does not exist.")
            try:
                self._normalize_support_fixity(support)
            except ValueError as exc:
                errors.append(str(exc))
        for element in self.elements:
            if element["i"] not in node_ids or element["j"] not in node_ids:
                errors.append(f"Element {element['id']} references a missing node.")
            if element["i"] == element["j"]:
                errors.append(f"Element {element['id']} has identical end nodes.")
            try:
                self._section_for_element(element)
            except ValueError as exc:
                errors.append(str(exc))
        self.model_errors = errors
        return errors

    def _geom_transf_vector(self, element):
        xi = self.node_coords[element["i"]]
        xj = self.node_coords[element["j"]]
        dx = xj[0] - xi[0]
        dy = xj[1] - xi[1]
        dz = xj[2] - xi[2]
        length = max(math.sqrt(dx * dx + dy * dy + dz * dz), 1e-12)
        local_x = (dx / length, dy / length, dz / length)
        up = (0.0, 1.0, 0.0)
        dot = abs(local_x[0] * up[0] + local_x[1] * up[1] + local_x[2] * up[2])
        if dot > 0.92:
            return (0.0, 0.0, 1.0)
        return up

    def _compute_self_weight_nodal_loads(self):
        loads = {}
        self.total_self_weight_n = 0.0
        if not self.include_self_weight:
            return loads
        for element in self.elements:
            section = self._section_for_element(element)
            weight = (
                section["density"]
                * section["A"]
                * self._element_length(element)
                * self.gravity
            )
            self.total_self_weight_n += weight
            loads[element["i"]] = loads.get(element["i"], 0.0) + weight / 2.0
            loads[element["j"]] = loads.get(element["j"], 0.0) + weight / 2.0
        return loads

    def _distributed_live_loads(self):
        return {
            int(node): float(load)
            for node, load in self.node_loads.items()
            if load > 0.0
        }

    def _build_model(self):
        ops.wipe()
        self.element_results = {}
        self.support_reactions = {}
        errors = self._validate_bridge_data()
        if errors:
            raise ValueError(" ".join(errors))
        ops.model("basic", "-ndm", 3, "-ndf", 6)
        for tag, (x, y, z) in self.node_coords.items():
            ops.node(tag, x, y, z)
        for support in self.supports:
            ops.fix(int(support["node"]), *self._normalize_support_fixity(support))
        for element in self.elements:
            section = self._section_for_element(element)
            vx, vy, vz = self._geom_transf_vector(element)
            transf_tag = 10000 + element["id"]
            ops.geomTransf("Linear", transf_tag, vx, vy, vz)
            ops.element(
                "elasticBeamColumn",
                element["id"],
                element["i"],
                element["j"],
                section["A"],
                section["E"],
                section["G"],
                section["J"],
                section["Iy"],
                section["Iz"],
                transf_tag,
            )
        ops.timeSeries("Linear", 1)
        ops.pattern("Plain", 1, 1)
        combined_loads = self._compute_self_weight_nodal_loads()
        for node, load in self._distributed_live_loads().items():
            combined_loads[node] = combined_loads.get(node, 0.0) + load
        for node, load in combined_loads.items():
            if load > 0.0:
                ops.load(int(node), 0.0, -load, 0.0, 0.0, 0.0, 0.0)
        ops.constraints("Transformation")
        ops.numberer("RCM")
        ops.system("BandGeneral")
        ops.test("NormDispIncr", 1e-10, 80, 0)
        ops.algorithm("Newton")
        ops.integrator("LoadControl", 1.0)
        ops.analysis("Static")

    def _display_node_disp(self, node):
        if not self._has_analysis_results() or not self._has_live_load():
            return 0.0, 0.0, 0.0
        base = self.dead_load_displacements.get(node, (0.0, 0.0, 0.0))
        return (
            ops.nodeDisp(node, 1) - base[0],
            ops.nodeDisp(node, 2) - base[1],
            ops.nodeDisp(node, 3) - base[2],
        )

    def _mqtt_node_disp(self, node):
        if not self._has_analysis_results():
            return 0.0, 0.0, 0.0
        if self._has_live_load():
            return self._display_node_disp(node)
        return (ops.nodeDisp(node, 1), ops.nodeDisp(node, 2), ops.nodeDisp(node, 3))

    def _element_strain_value(self, element_id, key, live_only=False):
        if not self._has_analysis_results():
            return 0.0
        total = self.element_results.get(element_id, {}).get(key, 0.0)
        if live_only and self._has_live_load():
            dead = self.dead_load_strains.get(element_id, {}).get(key, 0.0)
            return total - dead
        if live_only:
            return 0.0
        return total

    def _mqtt_element_strain_value(self, element_id, key):
        if self._has_live_load():
            return self._element_strain_value(element_id, key, live_only=True)
        return self._element_strain_value(element_id, key, live_only=False)

    def _mqtt_element_strain(self, element_id):
        return self._mqtt_element_strain_value(element_id, "combined_strain")

    def _update_status(self):
        total_uy = (
            ops.nodeDisp(self.selected_load_node, 2)
            if self._has_analysis_results()
            else 0.0
        )
        live_uy = self._display_node_disp(self.selected_load_node)[1]
        active_load = self._selected_node_load()
        live_load = self._total_applied_load()
        total_load = live_load + self.total_self_weight_n
        if self.comparison_mode == "delta" and self.comparison.active:
            n_strain = len(self.comparison.physical_strains)
            n_defl = len(self.comparison.physical_deflections)
            tare_status = (
                f" | Comparison mode: {self.comparison_mode}"
                f" | Session tare @ {self.comparison.load_n:.1f} N"
                f", live {live_load:.1f} N"
                f" ({n_strain} strain, {n_defl} deflection sensor(s))"
            )
        else:
            tare_status = f" | Comparison mode: {self.comparison_mode}"
        detection_status = ""
        if hasattr(self, "detection") and self.detection.last_summary:
            detection_status = f" | {self.detection.last_summary}"
        self._set_status_message(
            (
                f"Active node {self.selected_load_node}: applied {active_load:.1f} N | "
                f"Live: {live_load:.1f} N | Self-weight: {self.total_self_weight_n:.1f} N | "
                f"Total vertical: {total_load:.1f} N | Uy live: {live_uy:.6e} m, "
                f"total: {total_uy:.6e} m{tare_status}{detection_status}"
            )
        )
        self._set_sensor_summary(
            self._sensor_summary()
            + " | "
            + self._member_strain_summary()
            + " | "
            + self._engineering_summary()
        )

    def _sensor_summary(self):
        readings = []
        for sensor in self.sensor_points:
            node = int(sensor["node"])
            total_uy = ops.nodeDisp(node, 2) if self._has_analysis_results() else 0.0
            live_uy = self._display_node_disp(node)[1]
            readings.append(
                f"{sensor['sensor_id']} n{node}: {live_uy * 1000.0:.3f} mm live "
                f"({total_uy * 1000.0:.3f} mm total)"
            )
        return "Deflection: " + "; ".join(readings)

    def _member_strain_summary(self):
        if not self._has_analysis_results() or not self.element_results:
            return "Member strain: n/a"
        critical = max(
            self.element_results,
            key=lambda element_id: abs(self._mqtt_element_strain(element_id)),
        )
        live_axial = self._mqtt_element_strain_value(critical, "axial_strain")
        live_bending = self._mqtt_element_strain_value(critical, "bending_strain")
        live_combined = self._mqtt_element_strain(critical)
        return (
            "Member strain: "
            f"max |ε| {abs(live_combined) * 1e6:.1f} µε at element {critical} "
            f"(axial {live_axial * 1e6:.1f}, bending {live_bending * 1e6:.1f} µε live)"
        )

    def _collect_element_results(self):
        self.element_results = {}
        for element in self.elements:
            section = self._section_for_element(element)
            axial_force = 0.0
            moment = 0.0
            try:
                local_forces = ops.eleResponse(element["id"], "localForces")
                if len(local_forces) >= 12:
                    axial_force = max(abs(local_forces[0]), abs(local_forces[6]))
                    moment = max(
                        abs(local_forces[4]),
                        abs(local_forces[5]),
                        abs(local_forces[10]),
                        abs(local_forces[11]),
                    )
            except Exception:
                pass
            elastic_modulus = section["E"]
            axial_stress = axial_force / section["A"]
            bending_stress = moment / section["S"] if section["S"] > 0.0 else 0.0
            combined_stress = abs(axial_stress) + abs(bending_stress)
            self.element_results[element["id"]] = {
                "profile": section["profile"],
                "axial_force": axial_force,
                "moment": moment,
                "axial_stress": axial_stress,
                "bending_stress": bending_stress,
                "combined_stress": combined_stress,
                "axial_strain": axial_stress / elastic_modulus,
                "bending_strain": bending_stress / elastic_modulus,
                "combined_strain": combined_stress / elastic_modulus,
                "utilization": combined_stress / section["yield_stress"],
            }

    def _collect_support_reactions(self):
        self.support_reactions = {}
        try:
            ops.reactions()
            for support in self.supports:
                node = int(support["node"])
                self.support_reactions[node] = tuple(
                    ops.nodeReaction(node, dof) for dof in range(1, 7)
                )
        except Exception:
            self.support_reactions = {}

    def _max_deflection_summary(self):
        if not self._has_analysis_results():
            return 0.0, None
        max_node = None
        max_uy = 0.0
        for node in self.node_coords:
            uy = self._display_node_disp(node)[1] if self._has_live_load() else 0.0
            if max_node is None or abs(uy) > abs(max_uy):
                max_node = node
                max_uy = uy
        return max_uy, max_node

    def _controlling_element_id(self):
        if not self.element_results:
            return None
        return max(
            self.element_results,
            key=lambda element_id: self.element_results[element_id]["utilization"],
        )

    def _engineering_summary(self):
        if not self._has_analysis_results():
            return "Engineering summary unavailable."
        max_uy, max_node = self._max_deflection_summary()
        controlling = self._controlling_element_id()
        if controlling is None:
            return "Engineering summary unavailable."
        result = self.element_results[controlling]
        vertical_reactions = sum(
            reaction[1] for reaction in self.support_reactions.values()
        )
        live_strain = self._mqtt_element_strain(controlling)
        return (
            "Engineering: "
            f"max Uy {max_uy * 1000.0:.3f} mm at node {max_node}; "
            f"max stress {result['combined_stress'] / 1e6:.2f} MPa; "
            f"max strain {live_strain * 1e6:.1f} µε live at element {controlling}; "
            f"util {result['utilization']:.3f}; "
            f"vertical reactions sum {vertical_reactions:.1f} N."
        )

    def _info_message(self, prefix):
        message = prefix
        if self.model_warnings:
            message = f"{message} {'; '.join(self.model_warnings[:2])}"
        if self.mqtt.enabled or self.mqtt._last_error:
            message = f"{message} | {self.mqtt.status_message}"
        return message

    def apply_load_step(self):
        increment = self.reference_load_n * self.load_step
        previous_load = self._selected_node_load()
        self.node_loads[self.selected_load_node] = previous_load + increment
        ok = self._solve_current_loads()
        if ok != 0:
            self.node_loads[self.selected_load_node] = previous_load
            self._solve_current_loads()
            self._set_info_message("Analysis failed to converge. Try Reset Model.")
            return
        self._set_info_message(
            self._info_message(
                f"Added {increment:.1f} N at node {self.selected_load_node}."
            )
        )
        self._update_status()
        self._trigger_redraw()

    def apply_full_load(self):
        previous_load = self._selected_node_load()
        added_load = max(0.0, self.reference_load_n - previous_load)
        self.node_loads[self.selected_load_node] = self.reference_load_n
        ok = self._solve_current_loads()
        if ok != 0:
            self.node_loads[self.selected_load_node] = previous_load
            self._solve_current_loads()
            self._set_info_message("Analysis failed to converge. Try Reset Model.")
            return
        self._set_info_message(
            self._info_message(
                f"Added {added_load:.1f} N at node {self.selected_load_node}."
            )
        )
        self._update_status()
        self._trigger_redraw()

    def move_load_sensor(self, node):
        if node == self.selected_load_node:
            return
        self.selected_load_node = node
        self._set_info_message(
            (
                f"Load sensor moved to node {node}. Existing loads stay in place; "
                "new load steps apply here."
            )
        )
        self._update_status()
        self._trigger_redraw()

    def _projected_distance(self, point, projected_point):
        px, py = self._project(*point)
        return math.hypot(px - projected_point[0], py - projected_point[1])

    def _solve_current_loads(self):
        with self._opensees_lock:
            return self._solve_current_loads_unlocked()

    def _solve_current_loads_unlocked(self):
        self.analysis_completed = False
        try:
            self._build_model()
        except ValueError as exc:
            self._set_info_message(f"Model assembly failed: {exc}")
            return -1
        try:
            ok = ops.analyze(1)
        except Exception as exc:
            self._set_info_message(f"Analysis failed: {exc}")
            return -1
        if ok == 0:
            self.analysis_completed = True
            self._collect_element_results()
            self._collect_support_reactions()
            if not self._has_live_load():
                self._capture_dead_load_baseline()
            self._publish_mqtt()
        return ok

    def _capture_dead_load_baseline(self):
        self.dead_load_displacements = {
            node: (ops.nodeDisp(node, 1), ops.nodeDisp(node, 2), ops.nodeDisp(node, 3))
            for node in self.node_coords
        }
        self.dead_load_strains = {
            element_id: {
                "axial_strain": result["axial_strain"],
                "bending_strain": result["bending_strain"],
                "combined_strain": result["combined_strain"],
            }
            for element_id, result in self.element_results.items()
        }

    def _has_analysis_results(self):
        return self.analysis_completed

    def _has_live_load(self):
        return self._total_applied_load() > 0.0

    def tare(self, strain_readings=None):
        return self.comparison.tare(
            real_state=self.latest_real_state,
            strain_readings=strain_readings,
        )

    def clear_tare(self):
        self.comparison.clear()

    def set_comparison_mode(self, mode):
        if not isinstance(mode, str):
            raise ValueError("comparison mode must be 'delta' or 'absolute'")
        normalized = mode.strip().lower()
        if normalized not in ("delta", "absolute"):
            raise ValueError(f"invalid comparison mode: {mode!r}")
        self.comparison_mode = normalized
        self._update_status()
        self._publish_mqtt()
        return normalized

    def _tare_success_message(self):
        n_strain = len(self.comparison.physical_strains)
        n_defl = len(self.comparison.physical_deflections)
        return (
            f"Comparison tare set at {self.comparison.load_n:.1f} N "
            f"({n_strain} strain, {n_defl} deflection sensor(s))."
        )

    def _selected_node_load(self):
        return self.node_loads.get(self.selected_load_node, 0.0)

    def _total_applied_load(self):
        return sum(self.node_loads.values())

    def _element_by_id(self, element_id):
        for element in self.elements:
            if element["id"] == element_id:
                return element
        return None

    def reset_model(self):
        self.node_loads.clear()
        if self._solve_current_loads() == 0:
            self._set_info_message(
                self._info_message(
                    "Model reset. Press Space or click Apply Load Step to load the point."
                )
            )
        self._update_status()
        self._publish_mqtt()
        self._trigger_redraw()

    def state_snapshot(self):
        return {
            "comparison_mode": self.comparison_mode,
            "comparison_tare_active": self.comparison.active,
            "comparison_tare_load_n": self.comparison.load_n,
            "flagged_element_ids": list(self.detection.flagged_element_ids),
            "damage_detection_summary": self.detection.last_summary,
            "analysis_completed": self.analysis_completed,
            "selected_load_node": self.selected_load_node,
            "live_load_n": self._total_applied_load(),
            "node_loads": dict(self.node_loads),
            "calibration_scales": dict(self._calibration_scales),
            "last_info_text": self._last_info_text,
            "last_status_text": self._last_status_text,
            "last_sensor_text": self._last_sensor_text,
            "mqtt_status": self.mqtt.status_message,
        }

    def close(self):
        if hasattr(self, "detection"):
            self.detection.stop()
        self.mqtt.disconnect()

    def run_forever(self, poll_seconds: float = 0.5):
        try:
            while True:
                time.sleep(poll_seconds)
        except KeyboardInterrupt:
            pass
        finally:
            self.close()


def main():
    model = BridgeModel()
    model.run_forever()


if __name__ == "__main__":
    main()
