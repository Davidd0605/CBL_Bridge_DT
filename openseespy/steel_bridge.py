import json
import math
import tkinter as tk
from pathlib import Path
from tkinter import ttk

import openseespy.opensees as ops

from bridge_mqtt import BridgeMQTTPublisher


class Bridge3DLoadApp:
    def __init__(self, root):
        self.root = root
        self.root.title("OpenSeesPy 3D Pratt Bridge Load Sensor")

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
        self.distribute_live_load = True
        self.reference_load_n = 100.0
        self.load_step = 0.1
        self.node_loads = {}

        self.element_results = {}
        self.support_reactions = {}
        self.dead_load_displacements = {}
        self.dead_load_strains = {}
        self.total_self_weight_n = 0.0
        self.analysis_completed = False
        self.model_errors = []
        self.model_warnings = []
        self.mqtt = BridgeMQTTPublisher()
        self._geometry_published = False

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

        self.load_node_var = tk.StringVar()
        self.load_force_var = tk.StringVar(value=f"{self.reference_load_n:.1f}")
        self.sensor_var = tk.StringVar()

        self.canvas = tk.Canvas(
            root, width=self.canvas_width, height=self.canvas_height, bg="white"
        )
        self.canvas.pack(padx=10, pady=10)
        self.canvas.bind("<Button-1>", self.on_canvas_click)

        self.info_label = tk.Label(
            root,
            text=(
                "3D Pratt bridge: angle steel main members, flat-bar bracing, "
                "bolted-joint concept. Pick a deck point and apply load."
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
            width=34,
        )
        self.load_combo.pack(side="left", padx=(6, 12))
        self.load_combo.bind("<<ComboboxSelected>>", self.on_load_point_selected)

        tk.Label(self.button_frame, text="Target load (N)").pack(side="left")
        self.force_entry = tk.Entry(
            self.button_frame, textvariable=self.load_force_var, width=10
        )
        self.force_entry.pack(side="left", padx=(6, 12))
        self.force_entry.bind("<Return>", self.on_force_changed)
        self.force_entry.bind("<FocusOut>", self.on_force_changed)

        tk.Button(
            self.button_frame, text="Apply Load Step", command=self.apply_load_step
        ).pack(side="left")
        tk.Button(
            self.button_frame, text="Apply Full Load", command=self.apply_full_load
        ).pack(side="left", padx=(8, 0))
        tk.Button(
            self.button_frame, text="Reset Model", command=self.reset_model
        ).pack(side="left", padx=(8, 0))

        self.sensor_label = tk.Label(root, textvariable=self.sensor_var, anchor="w")
        self.sensor_label.pack(fill="x", padx=10, pady=(0, 10))

        self.root.bind("<space>", self.on_space_key)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._sync_load_combo()
        if self._solve_current_loads() == 0:
            self.info_label.config(text=self._info_message("3D model ready."))
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
        return [
            f"{item['label']} (node {int(item['node'])})"
            for item in self.load_points
        ]

    def _sync_load_combo(self):
        for choice in self._load_point_choices():
            if f"node {self.selected_load_node})" in choice:
                self.load_node_var.set(choice)
                return
        self.load_node_var.set(f"node {self.selected_load_node}")

    def _project(self, x, y, z):
        # Isometric-style projection: X stays horizontal, Z offsets the view.
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

        return {
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

    def _element_length(self, element):
        return self._distance(
            self.node_coords[element["i"]], self.node_coords[element["j"]]
        )

    def _distance(self, p1, p2):
        return math.sqrt(
            (p1[0] - p2[0]) ** 2
            + (p1[1] - p2[1]) ** 2
            + (p1[2] - p2[2]) ** 2
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
        for load_point in self.load_points:
            if int(load_point["node"]) not in node_ids:
                errors.append(f"Load point node {load_point['node']} does not exist.")
        for sensor in self.sensor_points:
            if int(sensor["node"]) not in node_ids:
                errors.append(f"Sensor node {sensor['node']} does not exist.")
        element_ids = {element["id"] for element in self.elements}
        for cell in self.load_cells:
            if int(cell["connecting_element"]) not in element_ids:
                errors.append(f"Load cell {cell['sensor_id']} has missing element.")
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
        if not self.distribute_live_load:
            return {
                int(node): float(load)
                for node, load in self.node_loads.items()
                if load > 0.0
            }
        ordered_nodes = sorted(
            {int(point["node"]) for point in self.load_points},
            key=lambda node: self.node_coords[node][0],
        )
        if len(ordered_nodes) <= 1:
            return {
                int(node): float(load)
                for node, load in self.node_loads.items()
                if load > 0.0
            }
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
            right_node = (
                ordered_nodes[index + 1]
                if index < len(ordered_nodes) - 1
                else None
            )
            if left_node is None or right_node is None:
                neighbour = right_node if left_node is None else left_node
                shares = {selected_node: 0.70, neighbour: 0.30}
            else:
                selected_x = self.node_coords[selected_node][0]
                left_distance = max(selected_x - self.node_coords[left_node][0], 1e-9)
                right_distance = max(
                    self.node_coords[right_node][0] - selected_x, 1e-9
                )
                left_share = 0.50 * right_distance / (left_distance + right_distance)
                shares = {
                    selected_node: 0.50,
                    left_node: left_share,
                    right_node: 0.50 - left_share,
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
            # All members are beam-columns so the 6-DOF 3D model has stable rotations.
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

    def _draw_line(self, p1, p2, color, width=2, dash=None):
        x1, y1 = self.world_to_canvas(*p1)
        x2, y2 = self.world_to_canvas(*p2)
        self.canvas.create_line(x1, y1, x2, y2, fill=color, width=width, dash=dash)

    def _draw_node(self, p, color, radius=4):
        x, y = self.world_to_canvas(*p)
        self.canvas.create_oval(
            x - radius, y - radius, x + radius, y + radius, fill=color, outline=color
        )

    def _draw_bridge(self):
        self.canvas.delete("all")
        controlling_element = self._controlling_element_id()
        for element in self.elements:
            element_type = element.get("type", "")
            result = self.element_results.get(element["id"], {})
            utilization = result.get("utilization", 0.0)
            if utilization > 1.0:
                color = "#dc2626"
            elif utilization > 0.75:
                color = "#f59e0b"
            elif "pratt_diagonal" in element_type or "brace" in element_type:
                color = "#64748b"
            elif "deck" in element_type or "floor" in element_type:
                color = "#2563eb"
            else:
                color = "#374151"
            width = 5 if element["id"] == controlling_element else 3
            if "lateral_brace" in element_type:
                width = 2
            self._draw_line(
                self.node_coords[element["i"]],
                self.node_coords[element["j"]],
                color,
                width=width,
            )
        for node_tag in self.node_coords:
            self._draw_node(self.node_coords[node_tag], "#111827", radius=3)
            self._draw_node_label(node_tag)
        self._draw_supports()
        self._draw_loads()
        self._draw_deflection_sensors()
        self._draw_load_cell_markers()
        self._draw_deformed_shape()
        self._draw_legend()

    def _draw_node_label(self, node_tag):
        x, y = self.world_to_canvas(*self.node_coords[node_tag])
        self.canvas.create_text(
            x,
            y - 12,
            text=self.node_labels[node_tag],
            fill="#374151",
            font=("Segoe UI", 7),
        )

    def _draw_supports(self):
        for support in self.supports:
            x, y = self.world_to_canvas(*self.node_coords[int(support["node"])])
            self.canvas.create_polygon(
                x - 10,
                y + 16,
                x + 10,
                y + 16,
                x,
                y + 4,
                fill="#9ca3af",
                outline="#4b5563",
            )

    def _draw_loads(self):
        distributed = self._distributed_live_loads()
        requested = self._selected_node_load()
        for node, load in distributed.items():
            label = f"{load:.1f} N"
            color = "#f97316" if node == self.selected_load_node else "#fb923c"
            if node == self.selected_load_node:
                label = f"Requested {requested:.1f} N\nApplied {load:.1f} N"
            self._draw_load_arrow(node, load, color, label)
        x, y = self.world_to_canvas(*self.node_coords[self.selected_load_node])
        self.canvas.create_oval(
            x - 12,
            y - 74,
            x + 12,
            y - 50,
            fill="#fed7aa",
            outline="#f97316",
            width=2,
        )
        self.canvas.create_text(
            x, y - 62, text="L", fill="#9a3412", font=("Segoe UI", 9, "bold")
        )

    def _draw_load_arrow(self, node, load, color, label):
        if load <= 0.0:
            return
        x, y = self.world_to_canvas(*self.node_coords[node])
        self.canvas.create_line(
            x, y - 58, x, y - 10, arrow=tk.LAST, fill=color, width=4
        )
        self.canvas.create_text(
            x + 16,
            y - 66,
            anchor="w",
            text=label,
            fill="#9a3412",
            font=("Segoe UI", 8),
        )

    def _draw_deflection_sensors(self):
        for sensor in self.sensor_points:
            x, y = self.world_to_canvas(*self.node_coords[int(sensor["node"])])
            live_uy = self._display_node_disp(int(sensor["node"]))[1]
            self.canvas.create_rectangle(
                x - 6, y + 8, x + 6, y + 20, fill="#dbeafe", outline="#2563eb"
            )
            self.canvas.create_text(
                x + 8,
                y + 14,
                anchor="w",
                text=f"{sensor['sensor_id']} {live_uy * 1000.0:.3f} mm",
                fill="#1d4ed8",
                font=("Segoe UI", 8),
            )

    def _draw_load_cell_markers(self):
        for cell in self.load_cells:
            node = int(cell["deck_node"])
            if node not in self.node_coords:
                continue
            x, y = self.world_to_canvas(*self.node_coords[node])
            self.canvas.create_rectangle(
                x - 5, y - 5, x + 5, y + 5, fill="#fef3c7", outline="#d97706"
            )

    def _draw_deformed_shape(self):
        if not self._has_analysis_results() or not self._has_live_load():
            return
        self._update_visual_defo_scale()
        deformed = {}
        for node_tag, (x, y, z) in self.node_coords.items():
            ux, uy, uz = self._display_node_disp(node_tag)
            deformed[node_tag] = (
                x + self.visual_defo_scale * ux,
                y + self.visual_defo_scale * uy,
                z + self.visual_defo_scale * uz,
            )
        for element in self.elements:
            self._draw_line(
                deformed[element["i"]],
                deformed[element["j"]],
                color="#dc2626",
                width=2,
                dash=(6, 3),
            )

    def _draw_legend(self):
        self.canvas.create_text(
            14,
            14,
            anchor="nw",
            text=(
                "3D Pratt bridge from bridge_3d_pratt.json | "
                "Blue: deck/load-transfer members | Yellow squares: load cells"
            ),
            fill="#333333",
            font=("Segoe UI", 10),
        )
        self.canvas.create_text(
            14,
            36,
            anchor="nw",
            text=(
                "Profiles: 15x15x3 angle steel main members, 15x3 flat-bar bracing | "
                "Bolted joints represented as metadata"
            ),
            fill="#333333",
            font=("Segoe UI", 9),
        )
        self.canvas.create_text(
            14,
            58,
            anchor="nw",
            text=(
                "Educational 3D frame approximation; not a certified joint, buckling, "
                "fatigue, or deck-contact model."
            ),
            fill="#7f1d1d",
            font=("Segoe UI", 9),
        )

    def _update_visual_defo_scale(self):
        self.visual_defo_scale = self.defo_scale
        if (
            not self.auto_defo_scale
            or not self._has_analysis_results()
            or not self._has_live_load()
        ):
            return
        max_displacement = 0.0
        for node_tag in self.node_coords:
            ux, uy, uz = self._display_node_disp(node_tag)
            max_displacement = max(max_displacement, math.sqrt(ux * ux + uy * uy + uz * uz))
        if max_displacement <= 0.0:
            return
        target_scale = self.min_deformed_offset_px / (max_displacement * self.scale)
        self.visual_defo_scale = max(self.defo_scale, target_scale)

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
        """Displacements sent to Unity: live-load delta, or total under self-weight only."""
        if not self._has_analysis_results():
            return 0.0, 0.0, 0.0
        if self._has_live_load():
            return self._display_node_disp(node)
        return (
            ops.nodeDisp(node, 1),
            ops.nodeDisp(node, 2),
            ops.nodeDisp(node, 3),
        )

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
        """Strain component for MQTT: live-load delta, or total under self-weight only."""
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
        active_distributed_load = self._distributed_live_loads().get(
            self.selected_load_node, 0.0
        )
        live_load = self._total_applied_load()
        total_load = live_load + self.total_self_weight_n
        self.status_label.config(
            text=(
                f"Active node {self.selected_load_node}: requested {active_load:.1f} N, "
                f"applied here {active_distributed_load:.1f} N | "
                f"Live: {live_load:.1f} N | Self-weight: {self.total_self_weight_n:.1f} N | "
                f"Total vertical: {total_load:.1f} N | Uy live: {live_uy:.6e} m, "
                f"total: {total_uy:.6e} m"
            )
        )
        self.sensor_var.set(
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
                axial_force = 0.0
                moment = 0.0
            elastic_modulus = section["E"]
            axial_stress = axial_force / section["A"]
            bending_stress = moment / section["S"] if section["S"] > 0.0 else 0.0
            combined_stress = abs(axial_stress) + abs(bending_stress)
            axial_strain = axial_stress / elastic_modulus
            bending_strain = bending_stress / elastic_modulus
            combined_strain = combined_stress / elastic_modulus
            utilization = combined_stress / section["yield_stress"]
            self.element_results[element["id"]] = {
                "profile": section["profile"],
                "axial_force": axial_force,
                "moment": moment,
                "axial_stress": axial_stress,
                "bending_stress": bending_stress,
                "combined_stress": combined_stress,
                "axial_strain": axial_strain,
                "bending_strain": bending_strain,
                "combined_strain": combined_strain,
                "utilization": utilization,
            }

    def _collect_support_reactions(self):
        self.support_reactions = {}
        try:
            ops.reactions()
            for support in self.supports:
                node = int(support["node"])
                self.support_reactions[node] = tuple(ops.nodeReaction(node, dof) for dof in range(1, 7))
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
        self.info_label.config(
            text=self._info_message(
                f"Added {increment:.1f} N at node {self.selected_load_node}."
            )
        )
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
        self.info_label.config(
            text=self._info_message(
                f"Added {added_load:.1f} N at node {self.selected_load_node}."
            )
        )
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
        self.reference_load_n = load
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
        px, py = self.canvas_to_projected_world(event.x, event.y)
        nearest_node = min(
            (int(item["node"]) for item in self.load_points),
            key=lambda node: self._projected_distance(self.node_coords[node], (px, py)),
        )
        if self._projected_distance(self.node_coords[nearest_node], (px, py)) <= 0.04:
            self.move_load_sensor(nearest_node)

    def _projected_distance(self, point, projected_point):
        px, py = self._project(*point)
        return math.hypot(px - projected_point[0], py - projected_point[1])

    def move_load_sensor(self, node):
        if node == self.selected_load_node:
            return
        self.selected_load_node = node
        self._sync_load_combo()
        self.info_label.config(
            text=(
                f"Load sensor moved to node {node}. Existing loads stay in place; "
                "new load steps apply here."
            )
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
            self._collect_element_results()
            self._collect_support_reactions()
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
            self.info_label.config(
                text=self._info_message(
                    "Model reset. Press Space or click Apply Load Step to load the point."
                )
            )
        self._draw_bridge()
        self._update_status()
        self._publish_mqtt()


def main():
    root = tk.Tk()
    app = Bridge3DLoadApp(root)
    _ = app
    root.mainloop()


if __name__ == "__main__":
    main()
