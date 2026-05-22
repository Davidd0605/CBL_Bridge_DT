import json
import math
import tkinter as tk
from pathlib import Path
from tkinter import ttk

import openseespy.opensees as ops

from bridge_mqtt import BridgeMQTTPublisher


class BridgeLoadApp:
    def __init__(self, root):
        self.root = root
        self.root.title("OpenSeesPy Steel Bridge Load Sensor")

        self.bridge_path = Path(__file__).with_name("bridge_3d_pratt.json")
        self.bridge = self._load_bridge(self.bridge_path)
        self.node_coords = {int(node["id"]): (float(node["x"]), float(node["y"])) for node in self.bridge["nodes"]}
        self.node_labels = {int(node["id"]): node.get("label", str(node["id"])) for node in self.bridge["nodes"]}
        self.elements = []
        for element in self.bridge["elements"]:
            parsed = dict(element)
            parsed["id"] = int(element["id"])
            parsed["type"] = element.get("type", "member")
            parsed["i"] = int(element["i"])
            parsed["j"] = int(element["j"])
            self.elements.append(parsed)
        self.supports = self.bridge.get("supports", [])
        self.sensor_points = self.bridge.get("deflection_sensor_points", [])
        self.load_points = self.bridge.get("recommended_load_application_nodes", [])

        self.default_e_modulus_pa = 200e9
        self.default_area_m2 = 0.0025
        self.default_iz_m4 = 2.0e-6
        self.default_section_depth_m = 0.15
        self.default_density_kg_m3 = 7850.0
        self.default_yield_stress_pa = 250e6
        self.gravity = 9.80665
        self.include_self_weight = True
        self.distribute_live_load = True
        self.force_all_members_as_frame = True
        self.element_results = {}
        self.support_reactions = {}
        self.model_warnings = []
        self.model_errors = []
        self.mqtt = BridgeMQTTPublisher()
        self._geometry_published = False
        self.analysis_completed = False
        self.dead_load_displacements = {}
        self.total_self_weight_n = 0.0
        self._element_sections = {}
        self.defo_scale = 200.0
        self.visual_defo_scale = self.defo_scale
        self.auto_defo_scale = True
        self.min_deformed_offset_px = 36.0
        self.load_step = 0.1
        self.current_lambda = 0.0

        self.default_load_node = int(
            self.bridge.get("midspan_sensor_node") or self.load_points[len(self.load_points) // 2]["node"]
        )
        self.selected_load_node = self.default_load_node
        self.reference_load_n = 100.0
        self.node_loads = {}

        self.canvas_width = 900
        self.canvas_height = 560
        self.margin = 80
        self._set_view_transform()

        self.load_node_var = tk.StringVar()
        self.load_force_var = tk.StringVar(value=f"{self.reference_load_n:.1f}")
        self.sensor_var = tk.StringVar()

        self.canvas = tk.Canvas(root, width=self.canvas_width, height=self.canvas_height, bg="white")
        self.canvas.pack(padx=10, pady=10)
        self.canvas.bind("<Button-1>", self.on_canvas_click)

        self.info_label = tk.Label(
            root,
            text=(
                "Pick a deck point, enter a load, then apply steps. "
                "Self-weight is included; live load is distributed approximately."
            ),
            anchor="w",
        )
        self.info_label.pack(fill="x", padx=10)

        self.status_label = tk.Label(root, text="", anchor="w")
        self.status_label.pack(fill="x", padx=10, pady=(0, 6))

        self.button_frame = tk.Frame(root)
        self.button_frame.pack(fill="x", padx=10, pady=(0, 10))

        tk.Label(self.button_frame, text="Load point").pack(side="left")
        self.load_combo = ttk.Combobox(
            self.button_frame,
            textvariable=self.load_node_var,
            values=self._load_point_choices(),
            state="readonly",
            width=26,
        )
        self.load_combo.pack(side="left", padx=(6, 12))
        self.load_combo.bind("<<ComboboxSelected>>", self.on_load_point_selected)

        tk.Label(self.button_frame, text="Target load (N)").pack(side="left")
        self.force_entry = tk.Entry(self.button_frame, textvariable=self.load_force_var, width=10)
        self.force_entry.pack(side="left", padx=(6, 12))
        self.force_entry.bind("<Return>", self.on_force_changed)
        self.force_entry.bind("<FocusOut>", self.on_force_changed)

        self.step_button = tk.Button(self.button_frame, text="Apply Load Step", command=self.apply_load_step)
        self.step_button.pack(side="left")

        self.full_load_button = tk.Button(self.button_frame, text="Apply Full Load", command=self.apply_full_load)
        self.full_load_button.pack(side="left", padx=(8, 0))

        self.reset_button = tk.Button(self.button_frame, text="Reset Model", command=self.reset_model)
        self.reset_button.pack(side="left", padx=(8, 0))

        self.sensor_label = tk.Label(root, textvariable=self.sensor_var, anchor="w")
        self.sensor_label.pack(fill="x", padx=10, pady=(0, 10))

        self.root.bind("<space>", self.on_space_key)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._sync_load_combo()
        if self._solve_current_loads() == 0:
            self.info_label.config(text=self._info_message("Model ready."))
        self._draw_bridge()
        self._update_status()
        self._publish_mqtt()

    def _load_bridge(self, path):
        with path.open("r", encoding="utf-8") as bridge_file:
            return json.load(bridge_file)

    def _publish_mqtt(self):
        if not self.mqtt.enabled:
            return
        if not self._geometry_published:
            if self.mqtt.publish_geometry(self):
                self._geometry_published = True
        self.mqtt.publish_state(self)

    def _on_close(self):
        self.mqtt.disconnect()
        self.root.destroy()

    def _load_point_choices(self):
        return [f"{item['label']} (node {int(item['node'])})" for item in self.load_points]

    def _sync_load_combo(self):
        for choice in self._load_point_choices():
            if f"node {self.selected_load_node})" in choice:
                self.load_node_var.set(choice)
                return
        self.load_node_var.set(f"node {self.selected_load_node}")

    def _set_view_transform(self):
        xs = [coord[0] for coord in self.node_coords.values()]
        ys = [coord[1] for coord in self.node_coords.values()]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        width = max(max_x - min_x, 1e-9)
        height = max(max_y - min_y, 1e-9)
        usable_width = self.canvas_width - 2 * self.margin
        usable_height = self.canvas_height - 2 * self.margin
        self.scale = min(usable_width / width, usable_height / height)
        self.origin_x = self.margin - min_x * self.scale
        self.origin_y = self.canvas_height - self.margin + min_y * self.scale

    def _normalize_support_fixity(self, support):
        fixity = support.get("fixity", [])
        if len(fixity) == 2:
            return [int(fixity[0]), int(fixity[1]), 0]
        if len(fixity) == 3:
            return [int(fixity[0]), int(fixity[1]), int(fixity[2])]
        raise ValueError(f"Support at node {support.get('node')} must have 2 or 3 fixity values.")

    def _is_frame_element(self, element):
        if self.force_all_members_as_frame:
            return True

        element_type = element.get("type", "member").lower()
        axial_only_markers = ("diagonal", "brace", "tie", "truss")
        force_frame = element.get("frame", False) or element.get("behavior") == "frame"
        force_truss = (
            element.get("truss_only", False)
            or element.get("axial_only", False)
            or element.get("behavior") in {"truss", "axial_only"}
        )
        if force_frame:
            return True
        if force_truss:
            return False
        return not any(marker in element_type for marker in axial_only_markers)

    def _get_element_section(self, element):
        element_id = element["id"]
        if element_id in self._element_sections:
            return self._element_sections[element_id]

        defaults_used = []

        def get_positive_number(key, default, strictly_positive=True):
            value = float(element.get(key, default))
            if (strictly_positive and value <= 0.0) or (not strictly_positive and value < 0.0):
                raise ValueError(f"Element {element_id} has invalid {key}={value}.")
            if key not in element:
                defaults_used.append(key)
            return value

        depth = get_positive_number("depth", self.default_section_depth_m)
        if "S" in element:
            section_modulus = get_positive_number("S", self.default_iz_m4 / (depth / 2.0))
        elif "section_modulus" in element:
            section_modulus = get_positive_number("section_modulus", self.default_iz_m4 / (depth / 2.0))
        else:
            section_modulus = None

        section = {
            "A": get_positive_number("A", self.default_area_m2),
            "Iz": get_positive_number("Iz", self.default_iz_m4),
            "E": get_positive_number("E", self.default_e_modulus_pa),
            "depth": depth,
            "density": get_positive_number("density", self.default_density_kg_m3, strictly_positive=False),
            "yield_stress": get_positive_number("yield_stress", self.default_yield_stress_pa),
        }
        if section_modulus is None:
            section_modulus = section["Iz"] / (section["depth"] / 2.0)
            defaults_used.append("section_modulus")
        section["S"] = section_modulus

        if defaults_used:
            self.model_warnings.append(f"Element {element_id} used default {', '.join(defaults_used)}.")
        self._element_sections[element_id] = section
        return section

    def _element_length(self, element):
        return self._distance(self.node_coords[element["i"]], self.node_coords[element["j"]])

    def _validate_bridge_data(self):
        errors = []
        self.model_warnings = []
        self._element_sections = {}
        node_ids = set(self.node_coords)

        if not self.supports:
            errors.append("At least one support is required.")
        if not self.load_points:
            errors.append("At least one load point is required.")

        constrained_dofs = [0, 0, 0]
        restrained_support_nodes = set()
        support_fixities = []
        for support in self.supports:
            node = int(support.get("node", -1))
            if node not in node_ids:
                errors.append(f"Support node {node} does not exist.")
                continue
            try:
                fixity = self._normalize_support_fixity(support)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            for index, fixed in enumerate(fixity):
                constrained_dofs[index] += int(bool(fixed))
            if any(fixity):
                restrained_support_nodes.add(node)
            support_fixities.append((node, fixity))

        if not any(constrained_dofs):
            errors.append("The model is unconstrained; add support fixities.")
        if constrained_dofs[0] == 0:
            errors.append("At least one support must restrain X translation.")
        if constrained_dofs[1] == 0:
            errors.append("At least one support must restrain Y translation.")
        if len(restrained_support_nodes) < 2 and constrained_dofs[2] == 0:
            errors.append("Use at least two restrained support points or one rotationally fixed support.")
        if support_fixities and all(fixity == [0, 1, 0] for _, fixity in support_fixities):
            self.model_warnings.append(
                "All supports are vertical rollers only; the model may have a rigid-body mechanism."
            )

        element_types_by_node = {node: [] for node in node_ids}

        for element in self.elements:
            nodes_ok = True
            for key in ("i", "j"):
                if element[key] not in node_ids:
                    nodes_ok = False
                    errors.append(f"Element {element['id']} references missing node {element[key]}.")
            if element["i"] == element["j"]:
                errors.append(f"Element {element['id']} has identical end nodes.")
            try:
                section = self._get_element_section(element)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if nodes_ok:
                element_types_by_node[element["i"]].append(self._is_frame_element(element))
                element_types_by_node[element["j"]].append(self._is_frame_element(element))
            if self._is_frame_element(element) and section["Iz"] <= 0.0:
                errors.append(f"Element {element['id']} frame Iz must be positive.")
            if self._is_frame_element(element) and section["S"] <= 0.0:
                errors.append(f"Element {element['id']} frame section modulus must be positive.")
            if nodes_ok and self._element_length(element) <= 0.0:
                errors.append(f"Element {element['id']} has zero length.")

        support_nodes = {int(support.get("node", -1)) for support in self.supports}
        for node, connected_frame_flags in element_types_by_node.items():
            if node not in support_nodes and connected_frame_flags and not any(connected_frame_flags):
                self.model_warnings.append(
                    f"Node {node} is connected only to axial-only elements; Rz may be unstable in a 3-DOF frame model."
                )

        for load_point in self.load_points:
            node = int(load_point["node"])
            if node not in node_ids:
                errors.append(f"Load point node {node} does not exist.")

        for sensor in self.sensor_points:
            node = int(sensor["node"])
            if node not in node_ids:
                errors.append(f"Sensor node {node} does not exist.")

        self.model_errors = errors
        return errors

    def _compute_self_weight_nodal_loads(self):
        loads = {}
        self.total_self_weight_n = 0.0
        if not self.include_self_weight:
            return loads

        for element in self.elements:
            section = self._get_element_section(element)
            weight = section["density"] * section["A"] * self._element_length(element) * self.gravity
            self.total_self_weight_n += weight
            loads[element["i"]] = loads.get(element["i"], 0.0) + weight / 2.0
            loads[element["j"]] = loads.get(element["j"], 0.0) + weight / 2.0
        return loads

    def _distributed_live_loads(self):
        if not self.distribute_live_load:
            return {int(node): float(load) for node, load in self.node_loads.items() if load > 0.0}

        ordered_nodes = sorted(
            {int(point["node"]) for point in self.load_points},
            key=lambda node: self.node_coords[node][0],
        )
        if len(ordered_nodes) <= 1:
            return {int(node): float(load) for node, load in self.node_loads.items() if load > 0.0}

        distributed = {}
        for selected_node, load in self.node_loads.items():
            selected_node = int(selected_node)
            load = float(load)
            if load <= 0.0:
                continue
            if selected_node not in ordered_nodes:
                distributed[selected_node] = distributed.get(selected_node, 0.0) + load
                continue

            index = ordered_nodes.index(selected_node)
            left_node = ordered_nodes[index - 1] if index > 0 else None
            right_node = ordered_nodes[index + 1] if index < len(ordered_nodes) - 1 else None

            if left_node is None or right_node is None:
                neighbour = right_node if left_node is None else left_node
                shares = {selected_node: 0.70, neighbour: 0.30}
            else:
                selected_x = self.node_coords[selected_node][0]
                left_distance = max(selected_x - self.node_coords[left_node][0], 1e-9)
                right_distance = max(self.node_coords[right_node][0] - selected_x, 1e-9)
                adjacent_total = 0.50
                left_share = adjacent_total * right_distance / (left_distance + right_distance)
                right_share = adjacent_total - left_share
                shares = {
                    selected_node: 0.50,
                    left_node: left_share,
                    right_node: right_share,
                }

            for node, share in shares.items():
                distributed[node] = distributed.get(node, 0.0) + load * share
        return distributed

    def _build_model(self):
        ops.wipe()
        self.element_results = {}
        self.support_reactions = {}
        errors = self._validate_bridge_data()
        if errors:
            raise ValueError(" ".join(errors))

        ops.model("basic", "-ndm", 2, "-ndf", 3)

        for tag, (x, y) in self.node_coords.items():
            ops.node(tag, x, y)

        for support in self.supports:
            fix_x, fix_y, fix_rz = self._normalize_support_fixity(support)
            ops.fix(int(support["node"]), fix_x, fix_y, fix_rz)

        # Frame elements include bending stiffness; hybrid mode keeps braces axial-only.
        ops.geomTransf("Linear", 1)
        for element in self.elements:
            section = self._get_element_section(element)
            if self._is_frame_element(element):
                ops.element(
                    "elasticBeamColumn",
                    element["id"],
                    element["i"],
                    element["j"],
                    section["A"],
                    section["E"],
                    section["Iz"],
                    1,
                )
            else:
                material_tag = 1000 + element["id"]
                ops.uniaxialMaterial("Elastic", material_tag, section["E"])
                ops.element(
                    "truss",
                    element["id"],
                    element["i"],
                    element["j"],
                    section["A"],
                    material_tag,
                )

        ops.timeSeries("Linear", 1)
        ops.pattern("Plain", 1, 1)

        # Self-weight is approximated as nodal loads for this educational model.
        combined_loads = self._compute_self_weight_nodal_loads()
        # Live load distribution is simplified; it is not a full deck/contact model.
        for node, load in self._distributed_live_loads().items():
            combined_loads[node] = combined_loads.get(node, 0.0) + load
        for node, load in combined_loads.items():
            if load > 0.0:
                ops.load(int(node), 0.0, -load, 0.0)

        ops.constraints("Plain")
        ops.numberer("RCM")
        ops.system("BandGeneral")
        ops.test("NormDispIncr", 1e-12, 40, 0)
        ops.algorithm("Newton")
        ops.integrator("LoadControl", 1.0)
        ops.analysis("Static")

    def world_to_canvas(self, x, y):
        cx = self.origin_x + x * self.scale
        cy = self.origin_y - y * self.scale
        return cx, cy

    def canvas_to_world(self, cx, cy):
        x = (cx - self.origin_x) / self.scale
        y = (self.origin_y - cy) / self.scale
        return x, y

    def _draw_line(self, p1, p2, color, width=2, dash=None):
        x1, y1 = self.world_to_canvas(*p1)
        x2, y2 = self.world_to_canvas(*p2)
        self.canvas.create_line(x1, y1, x2, y2, fill=color, width=width, dash=dash)

    def _draw_node(self, p, color, radius=4):
        x, y = self.world_to_canvas(*p)
        self.canvas.create_oval(x - radius, y - radius, x + radius, y + radius, fill=color, outline=color)

    def _draw_bridge(self):
        self.canvas.delete("all")
        controlling_element = self._controlling_element_id()

        for element in self.elements:
            result = self.element_results.get(element["id"], {})
            utilization = result.get("utilization", 0.0)
            if utilization > 1.0:
                color = "#dc2626"
            elif utilization > 0.75:
                color = "#f59e0b"
            else:
                color = "#4b5563" if element["type"] != "diagonal" else "#6b7280"
            self._draw_line(
                self.node_coords[element["i"]],
                self.node_coords[element["j"]],
                color=color,
                width=(6 if element["id"] == controlling_element else 4 if "chord" in element["type"] else 3),
            )

        for node_tag in self.node_coords:
            self._draw_node(self.node_coords[node_tag], color="#111827", radius=4)
            self._draw_node_label(node_tag)

        self._draw_supports()
        self._draw_load_sensor()
        self._draw_deflection_sensors()
        self._draw_deformed_shape()
        self._draw_legend()

    def _draw_node_label(self, node_tag):
        x, y = self.world_to_canvas(*self.node_coords[node_tag])
        self.canvas.create_text(
            x,
            y - 14,
            text=self.node_labels[node_tag],
            fill="#374151",
            font=("Segoe UI", 8),
        )

    def _draw_supports(self):
        for support in self.supports:
            node = int(support["node"])
            x, y = self.world_to_canvas(*self.node_coords[node])
            self.canvas.create_polygon(
                x - 12,
                y + 18,
                x + 12,
                y + 18,
                x,
                y + 4,
                fill="#9ca3af",
                outline="#4b5563",
            )

    def _draw_load_sensor(self):
        x, y = self.world_to_canvas(*self.node_coords[self.selected_load_node])
        requested_load = self._selected_node_load()
        distributed_loads = self._distributed_live_loads()
        selected_applied_load = distributed_loads.get(self.selected_load_node, 0.0)
        for node, load in distributed_loads.items():
            if node != self.selected_load_node and load > 0.0:
                self._draw_applied_load_arrow(node, load, "#fb923c", f"Distributed: {load:.1f} N")

        self._draw_applied_load_arrow(
            self.selected_load_node,
            selected_applied_load,
            "#f97316",
            (f"Requested: {requested_load:.1f} N\nApplied here: {selected_applied_load:.1f} N"),
        )
        self.canvas.create_oval(
            x - 13,
            y - 92,
            x + 13,
            y - 66,
            fill="#fed7aa",
            outline="#f97316",
            width=2,
        )
        self.canvas.create_text(
            x,
            y - 79,
            text="L",
            fill="#9a3412",
            font=("Segoe UI", 9, "bold"),
        )

    def _draw_applied_load_arrow(self, node, load, color, label):
        if load <= 0.0:
            return
        x, y = self.world_to_canvas(*self.node_coords[node])
        self.canvas.create_line(
            x,
            y - 70,
            x,
            y - 12,
            arrow=tk.LAST,
            fill=color,
            width=4,
        )
        self.canvas.create_text(
            x + 18,
            y - 88,
            anchor="w",
            text=label,
            fill="#9a3412",
            font=("Segoe UI", 9),
        )

    def _draw_deflection_sensors(self):
        for sensor in self.sensor_points:
            node = int(sensor["node"])
            x, y = self.world_to_canvas(*self.node_coords[node])
            total_uy = ops.nodeDisp(node, 2) if self._has_analysis_results() else 0.0
            live_uy = self._display_node_disp(node)[1]
            label = (
                f"{sensor['sensor_id']}: {live_uy * 1000.0:.3f} mm live"
                if self._has_live_load()
                else f"{sensor['sensor_id']}: 0.000 mm live"
            )
            if self.include_self_weight and self._has_analysis_results():
                label += f"\n(total {total_uy * 1000.0:.3f} mm)"
            self.canvas.create_rectangle(
                x - 7,
                y + 9,
                x + 7,
                y + 23,
                fill="#dbeafe",
                outline="#2563eb",
            )
            self.canvas.create_text(
                x + 10,
                y + 18,
                anchor="w",
                text=label,
                fill="#1d4ed8",
                font=("Segoe UI", 8),
            )

    def _draw_deformed_shape(self):
        if not self._has_analysis_results() or not self._has_live_load():
            return

        self._update_visual_defo_scale()
        deformed = {}
        for node_tag, (x, y) in self.node_coords.items():
            ux, uy = self._display_node_disp(node_tag)
            deformed[node_tag] = (
                x + self.visual_defo_scale * ux,
                y + self.visual_defo_scale * uy,
            )

        for element in self.elements:
            self._draw_line(
                deformed[element["i"]],
                deformed[element["j"]],
                color="#dc2626",
                width=2,
                dash=(6, 3),
            )

        for node_tag in deformed:
            self._draw_node(deformed[node_tag], color="#991b1b", radius=3)

    def _update_visual_defo_scale(self):
        self.visual_defo_scale = self.defo_scale
        if not self.auto_defo_scale or not self._has_analysis_results() or not self._has_live_load():
            return

        max_displacement = 0.0
        for node_tag in self.node_coords:
            ux, uy = self._display_node_disp(node_tag)
            max_displacement = max(max_displacement, math.hypot(ux, uy))

        if max_displacement <= 0.0:
            return

        live_load_fraction = 1.0
        if self._has_live_load() and self.reference_load_n > 0.0:
            live_load_fraction = min(
                max(self._total_applied_load() / self.reference_load_n, 0.0),
                1.0,
            )
        target_scale = self.min_deformed_offset_px * live_load_fraction / (max_displacement * self.scale)
        self.visual_defo_scale = max(self.defo_scale, target_scale)

    def _display_node_disp(self, node):
        if not self._has_analysis_results():
            return 0.0, 0.0
        if not self._has_live_load():
            return 0.0, 0.0

        base_ux, base_uy = self.dead_load_displacements.get(node, (0.0, 0.0))
        return ops.nodeDisp(node, 1) - base_ux, ops.nodeDisp(node, 2) - base_uy

    def _draw_legend(self):
        max_utilization = self._max_utilization()
        warning = ""
        if max_utilization > 1.0:
            warning = " | WARNING: utilization > 1.0"
        self.canvas.create_text(
            14,
            14,
            anchor="nw",
            text=(
                "Steel Warren truss from bridge.json | "
                "Grey: bridge | Red dashed: "
                f"{'live-load deformation scaled x' + format(self.visual_defo_scale, 'g') if self._has_live_load() else 'apply live load to show deformation'}"
                f"{warning}"
            ),
            fill="#333333",
            font=("Segoe UI", 10),
        )
        self.canvas.create_text(
            14,
            36,
            anchor="nw",
            text=(
                f"Defaults if omitted: E={self.default_e_modulus_pa / 1e9:.0f} GPa, "
                f"A={self.default_area_m2 * 1e6:.0f} mm^2, "
                f"Iz={self.default_iz_m4:.1e} m^4, "
                f"depth={self.default_section_depth_m:.2f} m"
            ),
            fill="#333333",
            font=("Segoe UI", 10),
        )
        self.canvas.create_text(
            14,
            58,
            anchor="nw",
            text=(
                "Self-weight: nodal approximation | Live load: simplified tributary "
                "distribution | Utilization: axial + approximate bending stress"
            ),
            fill="#333333",
            font=("Segoe UI", 9),
        )
        self.canvas.create_text(
            14,
            78,
            anchor="nw",
            text="Educational results only unless calibrated to real bridge geometry and section data.",
            fill="#7f1d1d",
            font=("Segoe UI", 9),
        )

    def _update_status(self):
        total_uy = ops.nodeDisp(self.selected_load_node, 2) if self._has_analysis_results() else 0.0
        live_uy = self._display_node_disp(self.selected_load_node)[1]
        active_load = self._selected_node_load()
        active_distributed_load = self._distributed_live_loads().get(self.selected_load_node, 0.0)
        live_load = self._total_applied_load()
        total_load = live_load + self.total_self_weight_n
        self.status_label.config(
            text=(
                f"Active node {self.selected_load_node}: requested {active_load:.1f} N, "
                f"applied here {active_distributed_load:.1f} N | "
                f"Live: {live_load:.1f} N | "
                f"Self-weight: {self.total_self_weight_n:.1f} N | "
                f"Total vertical: {total_load:.1f} N | "
                f"Active Uy live: {live_uy:.6e} m, total: {total_uy:.6e} m"
            )
        )
        self.sensor_var.set(self._sensor_summary() + " | " + self._engineering_summary())

    def _sensor_summary(self):
        readings = []
        for sensor in self.sensor_points:
            node = int(sensor["node"])
            total_uy = ops.nodeDisp(node, 2) if self._has_analysis_results() else 0.0
            live_uy = self._display_node_disp(node)[1]
            readings.append(
                f"{sensor['sensor_id']} node {node}: {live_uy * 1000.0:.3f} mm live ({total_uy * 1000.0:.3f} mm total)"
            )
        return "Deflection sensors: " + " | ".join(readings)

    def _collect_element_results(self):
        self.element_results = {}
        for element in self.elements:
            section = self._get_element_section(element)
            is_frame = self._is_frame_element(element)
            axial_force = 0.0
            moment = 0.0
            try:
                if is_frame:
                    local_forces = ops.eleResponse(element["id"], "localForces")
                    if len(local_forces) >= 6:
                        axial_force = max(abs(local_forces[0]), abs(local_forces[3]))
                        moment = max(abs(local_forces[2]), abs(local_forces[5]))
                else:
                    response = ops.eleResponse(element["id"], "axialForce")
                    if isinstance(response, (list, tuple)):
                        axial_force = abs(float(response[0])) if response else 0.0
                    else:
                        axial_force = abs(float(response))
            except Exception:
                axial_force = 0.0
                moment = 0.0

            axial_stress = axial_force / section["A"]
            bending_stress = moment / section["S"] if is_frame else 0.0
            combined_stress = abs(axial_stress) + abs(bending_stress) if is_frame else abs(axial_stress)
            utilization = combined_stress / section["yield_stress"]
            self.element_results[element["id"]] = {
                "axial_force": axial_force,
                "moment": moment,
                "axial_stress": axial_stress,
                "bending_stress": bending_stress,
                "combined_stress": combined_stress,
                "stress": combined_stress,
                "utilization": utilization,
            }

    def _collect_support_reactions(self):
        self.support_reactions = {}
        try:
            ops.reactions()
            for support in self.supports:
                node = int(support["node"])
                self.support_reactions[node] = (
                    ops.nodeReaction(node, 1),
                    ops.nodeReaction(node, 2),
                    ops.nodeReaction(node, 3),
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

    def _max_utilization(self):
        controlling = self._controlling_element_id()
        if controlling is None:
            return 0.0
        return self.element_results[controlling]["utilization"]

    def _engineering_summary(self):
        if not self._has_analysis_results():
            return "Engineering summary: analysis unavailable."

        max_uy, max_node = self._max_deflection_summary()
        controlling = self._controlling_element_id()
        if controlling is None:
            return "Engineering summary: analysis unavailable."

        result = self.element_results[controlling]
        reactions = []
        for node, reaction in self.support_reactions.items():
            reactions.append(f"R{node}=({reaction[0]:.1f}, {reaction[1]:.1f}, {reaction[2]:.2f})")
        reaction_text = "; ".join(reactions) if reactions else "reactions unavailable"
        return (
            "Engineering summary: "
            f"max Uy {max_uy * 1000.0:.3f} mm at node {max_node}; "
            f"max combined stress {result['combined_stress'] / 1e6:.2f} MPa; "
            f"util {result['utilization']:.3f} at element {controlling}; "
            f"{reaction_text}. Educational estimate only."
        )

    def _info_message(self, prefix):
        message = prefix
        if self.model_warnings:
            shown = "; ".join(self.model_warnings[:2])
            remaining = len(self.model_warnings) - 2
            if remaining > 0:
                shown += f"; +{remaining} more default-property warnings"
            message = f"{message} {shown}"
        if self.mqtt.enabled or self.mqtt._last_error:
            message = f"{message} | {self.mqtt.status_message}"
        return message

    def apply_load_step(self):
        if not self._refresh_force_from_entry():
            return

        increment = self.reference_load_n * self.load_step
        previous_load = self._selected_node_load()
        self.node_loads[self.selected_load_node] = previous_load + increment
        ok = self._solve_current_loads()
        if ok != 0:
            self.node_loads[self.selected_load_node] = previous_load
            self._solve_current_loads()
            self.info_label.config(text="Analysis failed to converge. Try Reset Model.")
            return

        self.info_label.config(text=self._info_message(f"Added {increment:.1f} N at node {self.selected_load_node}."))
        self._draw_bridge()
        self._update_status()

    def apply_full_load(self):
        if not self._refresh_force_from_entry():
            return

        previous_load = self._selected_node_load()
        added_load = max(0.0, self.reference_load_n - previous_load)
        self.node_loads[self.selected_load_node] = self.reference_load_n
        ok = self._solve_current_loads()
        if ok != 0:
            self.node_loads[self.selected_load_node] = previous_load
            self._solve_current_loads()
            self.info_label.config(text="Analysis failed to converge. Try Reset Model.")
            return

        self.info_label.config(text=self._info_message(f"Added {added_load:.1f} N at node {self.selected_load_node}."))
        self._draw_bridge()
        self._update_status()

    def _refresh_force_from_entry(self):
        try:
            load = float(self.load_force_var.get())
        except ValueError:
            self.info_label.config(text="Enter a numeric load in newtons.")
            return False

        if load <= 0.0:
            self.info_label.config(text="Load must be greater than zero.")
            return False

        if not math.isclose(load, self.reference_load_n):
            self.reference_load_n = load
            self.info_label.config(text="Target load changed. Existing applied loads were kept.")
        return True

    def on_space_key(self, _event):
        self.apply_load_step()

    def on_force_changed(self, _event):
        self._refresh_force_from_entry()

    def on_load_point_selected(self, _event):
        choice = self.load_node_var.get()
        for load_point in self.load_points:
            if f"node {int(load_point['node'])})" in choice:
                self.move_load_sensor(int(load_point["node"]))
                return

    def on_canvas_click(self, event):
        x, y = self.canvas_to_world(event.x, event.y)
        nearest_node = min(
            (int(item["node"]) for item in self.load_points),
            key=lambda node: self._distance(self.node_coords[node], (x, y)),
        )
        if self._distance(self.node_coords[nearest_node], (x, y)) <= 0.04:
            self.move_load_sensor(nearest_node)

    def _distance(self, p1, p2):
        return math.hypot(p1[0] - p2[0], p1[1] - p2[1])

    def move_load_sensor(self, node):
        if node == self.selected_load_node:
            return

        self.selected_load_node = node
        self._sync_load_combo()
        self.info_label.config(
            text=(f"Load sensor moved to node {node}. Existing loads stay in place; new load steps apply here.")
        )
        self._draw_bridge()
        self._update_status()

    def _solve_current_loads(self):
        self.analysis_completed = False
        try:
            self._build_model()
        except ValueError as exc:
            self.info_label.config(text=f"Model assembly failed: {exc}")
            return -1
        try:
            ok = ops.analyze(1)
        except Exception as exc:
            self.info_label.config(text=f"Analysis failed: {exc}")
            return -1
        if ok == 0:
            self.analysis_completed = True
            if not self._has_live_load():
                self._capture_dead_load_baseline()
            if self.reference_load_n > 0.0:
                self.current_lambda = self._total_applied_load() / self.reference_load_n
            self._collect_element_results()
            self._collect_support_reactions()
            self._publish_mqtt()
        return ok

    def _capture_dead_load_baseline(self):
        self.dead_load_displacements = {
            node: (ops.nodeDisp(node, 1), ops.nodeDisp(node, 2)) for node in self.node_coords
        }

    def _has_analysis_results(self):
        return self.analysis_completed

    def _has_live_load(self):
        return self._total_applied_load() > 0.0

    def _selected_node_load(self):
        return self.node_loads.get(self.selected_load_node, 0.0)

    def _total_applied_load(self):
        return sum(self.node_loads.values())

    def reset_model(self):
        self.current_lambda = 0.0
        self.node_loads.clear()
        if self._solve_current_loads() == 0:
            self.info_label.config(
                text=self._info_message("Model reset. Press Space or click Apply Load Step to load the point.")
            )
        self._draw_bridge()
        self._update_status()


def main():
    root = tk.Tk()
    app = BridgeLoadApp(root)
    _ = app
    root.mainloop()


if __name__ == "__main__":
    main()
