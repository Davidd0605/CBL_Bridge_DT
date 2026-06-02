import tkinter as tk
from tkinter import ttk

import openseespy.opensees as ops

from bridge_model import BridgeModel


class Bridge3DLoadApp:
    def __init__(self, root=None):
        self.root = root
        self.model = None
        self.info_label = None
        self.status_label = None
        self.sensor_var = None
        if self.root is not None:
            self.root.title("OpenSeesPy 3D Pratt Bridge Load Sensor")

        self.model = BridgeModel(
            dispatch_to_main=self._dispatch_to_main,
            info_callback=self._set_info_message,
            status_callback=self._set_status_message,
            sensor_callback=self._set_sensor_summary,
            redraw_callback=self._draw_bridge,
        )
        self.load_node_var = tk.StringVar()
        self.load_force_var = tk.StringVar(value=f"{self.model.reference_load_n:.1f}")
        self.sensor_var = tk.StringVar()

        self.canvas = tk.Canvas(
            root, width=self.model.canvas_width, height=self.model.canvas_height, bg="white"
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
        tk.Button(self.button_frame, text="Apply Load Step", command=self.apply_load_step).pack(side="left")
        tk.Button(self.button_frame, text="Apply Full Load", command=self.apply_full_load).pack(side="left", padx=(8, 0))
        tk.Button(self.button_frame, text="Reset Model", command=self.reset_model).pack(side="left", padx=(8, 0))
        tk.Button(self.button_frame, text="Tare sensors", command=self.on_tare_clicked).pack(side="left", padx=(8, 0))
        tk.Button(self.button_frame, text="Clear sensor tare", command=self.on_clear_tare_clicked).pack(side="left", padx=(8, 0))
        self.sensor_label = tk.Label(root, textvariable=self.sensor_var, anchor="w")
        self.sensor_label.pack(fill="x", padx=10, pady=(0, 10))

        self._sync_load_combo()
        self._set_info_message(self.model._last_info_text)
        self._set_status_message(self.model._last_status_text)
        self._set_sensor_summary(self.model._last_sensor_text)
        self._draw_bridge()
        self.root.bind("<space>", self.on_space_key)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def __getattr__(self, name):
        model = self.__dict__.get("model")
        if model is None:
            raise AttributeError(name)
        return getattr(model, name)

    def _dispatch_to_main(self, callback, *args):
        if self.root is not None:
            self.root.after(0, callback, *args)
        else:
            callback(*args)

    def _set_info_message(self, text):
        label = self.__dict__.get("info_label")
        if label is not None:
            label.config(text=text)

    def _set_status_message(self, text):
        label = self.__dict__.get("status_label")
        if label is not None:
            label.config(text=text)

    def _set_sensor_summary(self, text):
        sensor_var = self.__dict__.get("sensor_var")
        if sensor_var is not None:
            sensor_var.set(text)

    def _on_close(self):
        self.model.close()
        if self.root is not None:
            self.root.destroy()

    def _load_point_choices(self):
        return [
            f"{item['label']} (node {int(item['node'])})"
            for item in self.model.load_points
        ]

    def _sync_load_combo(self):
        if self.load_node_var is None:
            self.model._load_node_text = f"node {self.model.selected_load_node}"
            return
        for choice in self._load_point_choices():
            if f"node {self.model.selected_load_node})" in choice:
                self.load_node_var.set(choice)
                return
        self.load_node_var.set(f"node {self.model.selected_load_node}")

    def _draw_line(self, p1, p2, color, width=2, dash=None):
        x1, y1 = self.model.world_to_canvas(*p1)
        x2, y2 = self.model.world_to_canvas(*p2)
        self.canvas.create_line(x1, y1, x2, y2, fill=color, width=width, dash=dash)

    def _draw_node(self, p, color, radius=4):
        x, y = self.model.world_to_canvas(*p)
        self.canvas.create_oval(
            x - radius, y - radius, x + radius, y + radius, fill=color, outline=color
        )

    def _draw_bridge(self):
        self.canvas.delete("all")
        controlling_element = self.model._controlling_element_id()
        for element in self.model.elements:
            element_type = element.get("type", "")
            result = self.model.element_results.get(element["id"], {})
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
                self.model.node_coords[element["i"]],
                self.model.node_coords[element["j"]],
                color,
                width=width,
            )
        for node_tag in self.model.node_coords:
            self._draw_node(self.model.node_coords[node_tag], "#111827", radius=3)
            self._draw_node_label(node_tag)
        self._draw_supports()
        self._draw_loads()
        self._draw_deflection_sensors()
        self._draw_load_cell_markers()
        self._draw_deformed_shape()
        self._draw_legend()

    def _draw_node_label(self, node_tag):
        x, y = self.model.world_to_canvas(*self.model.node_coords[node_tag])
        self.canvas.create_text(
            x,
            y - 12,
            text=self.model.node_labels[node_tag],
            fill="#374151",
            font=("Segoe UI", 7),
        )

    def _draw_supports(self):
        for support in self.model.supports:
            x, y = self.model.world_to_canvas(*self.model.node_coords[int(support["node"])])
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
        applied_loads = self.model._distributed_live_loads()
        for node, load in applied_loads.items():
            label = f"{load:.1f} N"
            color = "#f97316" if node == self.model.selected_load_node else "#fb923c"
            if node == self.model.selected_load_node:
                label = f"Applied {load:.1f} N"
            self._draw_load_arrow(node, load, color, label)
        x, y = self.model.world_to_canvas(*self.model.node_coords[self.model.selected_load_node])
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
        x, y = self.model.world_to_canvas(*self.model.node_coords[node])
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
        for sensor in self.model.sensor_points:
            x, y = self.model.world_to_canvas(*self.model.node_coords[int(sensor["node"])])
            live_uy = self.model._display_node_disp(int(sensor["node"]))[1]
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
        for cell in self.model.load_cells:
            node = int(cell["deck_node"])
            if node not in self.model.node_coords:
                continue
            x, y = self.model.world_to_canvas(*self.model.node_coords[node])
            self.canvas.create_rectangle(
                x - 5, y - 5, x + 5, y + 5, fill="#fef3c7", outline="#d97706"
            )

    def _draw_deformed_shape(self):
        if not self.model._has_analysis_results() or not self.model._has_live_load():
            return
        self._update_visual_defo_scale()
        deformed = {}
        for node_tag, (x, y, z) in self.model.node_coords.items():
            ux, uy, uz = self.model._display_node_disp(node_tag)
            deformed[node_tag] = (
                x + self.model.visual_defo_scale * ux,
                y + self.model.visual_defo_scale * uy,
                z + self.model.visual_defo_scale * uz,
            )
        for element in self.model.elements:
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
        self.model.visual_defo_scale = self.model.defo_scale
        if (
            not self.model.auto_defo_scale
            or not self.model._has_analysis_results()
            or not self.model._has_live_load()
        ):
            return
        max_displacement = 0.0
        for node_tag in self.model.node_coords:
            ux, uy, uz = self.model._display_node_disp(node_tag)
            max_displacement = max(max_displacement, (ux * ux + uy * uy + uz * uz) ** 0.5)
        if max_displacement <= 0.0:
            return
        target_scale = self.model.min_deformed_offset_px / (max_displacement * self.model.scale)
        self.model.visual_defo_scale = max(self.model.defo_scale, target_scale)

    def apply_load_step(self):
        if not self.on_force_changed():
            return
        self.model.apply_load_step()

    def apply_full_load(self):
        if not self.on_force_changed():
            return
        self.model.apply_full_load()

    def on_force_changed(self, _event=None):
        raw_load = self.load_force_var.get()
        try:
            load = float(raw_load)
        except ValueError:
            self._set_info_message("Enter a numeric load in newtons.")
            return False
        if load <= 0.0:
            self._set_info_message("Load must be greater than zero.")
            return False
        self.model.reference_load_n = load
        self.model._load_force_text = f"{load:.1f}"
        return True

    def on_space_key(self, _event):
        self.apply_load_step()

    def on_load_point_selected(self, _event):
        if self.load_node_var is None:
            return
        choice = self.load_node_var.get()
        for load_point in self.model.load_points:
            if f"node {int(load_point['node'])})" in choice:
                self.model.move_load_sensor(int(load_point["node"]))
                self._sync_load_combo()
                return

    def on_canvas_click(self, event):
        px, py = self.model.canvas_to_projected_world(event.x, event.y)
        nearest_node = min(
            (int(item["node"]) for item in self.model.load_points),
            key=lambda node: self.model._projected_distance(self.model.node_coords[node], (px, py)),
        )
        if self.model._projected_distance(self.model.node_coords[nearest_node], (px, py)) <= 0.04:
            self.model.move_load_sensor(nearest_node)
            self._sync_load_combo()

    def on_tare_clicked(self):
        result = self.model._handle_command({"action": "tare"})
        if result["ok"]:
            self._set_info_message(self.model._info_message(result["message"]))
        else:
            self._set_info_message(
                result["message"]
                or (
                    "Tare failed: subscribe to cbl/bridge/real/state and wait for "
                    "sensor data, or pass readings via MQTT command."
                )
            )
        self.model._update_status()
        self.model._publish_mqtt()

    def on_clear_tare_clicked(self):
        result = self.model._handle_command({"action": "clear_tare"})
        self._set_info_message(self.model._info_message(result["message"]))
        self.model._update_status()
        self.model._publish_mqtt()

    def reset_model(self):
        self.model.reset_model()


def main():
    root = tk.Tk()
    app = Bridge3DLoadApp(root)
    _ = app
    root.mainloop()


if __name__ == "__main__":
    main()
