"""MQTT publisher for OpenSeesPy bridge model geometry and analysis state."""

from __future__ import annotations

import json
import os
import time
import uuid

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

try:
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover - optional at import time
    mqtt = None

import openseespy.opensees as ops


DEFAULT_HOST = "80.113.118.200"
DEFAULT_PORT = 1883
DEFAULT_USERNAME = "myuser"
DEFAULT_PASSWORD = "cblbroker123"
TOPIC_GEOMETRY = "cbl/bridge/sim/geometry"
TOPIC_STATE = "cbl/bridge/sim/state"


def _node_position(node: dict) -> tuple[float, float, float]:
    return (
        float(node["x"]),
        float(node["y"]),
        float(node.get("z", 0.0)),
    )


def _unpack_displacement(disp) -> tuple[float, float, float]:
    if len(disp) == 2:
        return float(disp[0]), float(disp[1]), 0.0
    return float(disp[0]), float(disp[1]), float(disp[2])


class BridgeMQTTPublisher:
    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        username: str | None = None,
        password: str | None = None,
        enabled: bool | None = None,
    ):
        self.host = host or os.environ.get("MQTT_BROKER_HOST") or os.environ.get("MQTT_BROKER") or DEFAULT_HOST
        self.port = int(port or os.environ.get("MQTT_BROKER_PORT") or os.environ.get("MQTT_PORT", DEFAULT_PORT))
        self.username = username or os.environ.get("MQTT_USERNAME", DEFAULT_USERNAME)
        self.password = password or os.environ.get("MQTT_PASSWORD", DEFAULT_PASSWORD)
        self.enabled = (
            enabled
            if enabled is not None
            else os.environ.get("MQTT_ENABLED", "1").strip().lower() not in ("0", "false", "no")
        )
        self._client = None
        self._connected = False
        self._last_error = None

        if not self.enabled:
            return
        if mqtt is None:
            self._last_error = "paho-mqtt is not installed (pip install paho-mqtt)"
            self.enabled = False
            return
        self._connect()

    @property
    def status_message(self) -> str:
        if not self.enabled:
            return "MQTT disabled"
        if self._connected:
            return f"MQTT connected to {self.host}:{self.port}"
        return f"MQTT offline: {self._last_error or 'unknown error'}"

    def _connect(self) -> None:
        client_id = f"steel_bridge_{uuid.uuid4().hex[:8]}"
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
        )
        if self.username:
            self._client.username_pw_set(self.username, self.password or None)
        try:
            self._client.connect(self.host, self.port, keepalive=60)
            self._client.loop_start()
            self._connected = True
            self._last_error = None
        except Exception as exc:  # noqa: BLE001 - surface broker errors to GUI
            self._connected = False
            self._last_error = str(exc)
            self.enabled = False

    def disconnect(self) -> None:
        if self._client is None:
            return
        try:
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:
            pass
        self._client = None
        self._connected = False

    def publish_geometry(self, app) -> bool:
        payload = {
            "type": "geometry",
            "timestamp": time.time(),
            "bridge_name": app.bridge.get("name", "bridge_3d_pratt"),
            "nodes": [
                {
                    "id": int(node["id"]),
                    "label": node.get("label", str(node["id"])),
                    "x": x,
                    "y": y,
                    "z": z,
                }
                for node in app.bridge["nodes"]
                for x, y, z in [_node_position(node)]
            ],
            "elements": [
                {
                    "id": element["id"],
                    "i": element["i"],
                    "j": element["j"],
                    "type": element.get("type", "member"),
                }
                for element in app.elements
            ],
            "supports": [{"node": int(support["node"]), "label": support.get("label", "")} for support in app.supports],
            "deflection_sensor_points": app.sensor_points,
        }
        return self._publish(TOPIC_GEOMETRY, payload, retain=True)

    def publish_state(self, app) -> bool:
        node_disp = getattr(app, "_mqtt_node_disp", app._display_node_disp)

        node_ids = []
        disp_x = []
        disp_y = []
        disp_z = []
        for node_id in sorted(app.node_coords):
            ux, uy, uz = _unpack_displacement(node_disp(node_id))
            node_ids.append(node_id)
            disp_x.append(ux)
            disp_y.append(uy)
            disp_z.append(uz)

        element_ids = []
        utilization = []
        axial_strain = []
        bending_strain = []
        combined_strain = []
        mqtt_strain = getattr(app, "_mqtt_element_strain_value", None)
        for element_id in sorted(app.element_results):
            element_ids.append(element_id)
            result = app.element_results[element_id]
            utilization.append(result["utilization"])
            if mqtt_strain is not None:
                axial_strain.append(mqtt_strain(element_id, "axial_strain"))
                bending_strain.append(mqtt_strain(element_id, "bending_strain"))
                combined_strain.append(mqtt_strain(element_id, "combined_strain"))
            else:
                axial_strain.append(result.get("axial_strain", 0.0))
                bending_strain.append(result.get("bending_strain", 0.0))
                combined_strain.append(result.get("combined_strain", 0.0))

        sensor_readings = []
        if app._has_analysis_results():
            for sensor in app.sensor_points:
                node = int(sensor["node"])
                total_uy = ops.nodeDisp(node, 2)
                live_uy = node_disp(node)[1]
                sensor_readings.append(
                    {
                        "sensor_id": sensor["sensor_id"],
                        "node": node,
                        "live_uy_m": live_uy,
                        "total_uy_m": total_uy,
                    }
                )

        payload = {
            "type": "state",
            "timestamp": time.time(),
            "analysis_completed": app.analysis_completed,
            "selected_load_node": app.selected_load_node,
            "live_load_n": app._total_applied_load(),
            "self_weight_n": app.total_self_weight_n,
            "node_loads": {str(k): v for k, v in app.node_loads.items()},
            "visual_defo_scale": app.visual_defo_scale,
            "node_ids": node_ids,
            "disp_x": disp_x,
            "disp_y": disp_y,
            "disp_z": disp_z,
            "element_ids": element_ids,
            "utilization": utilization,
            "axial_strain": axial_strain,
            "bending_strain": bending_strain,
            "combined_strain": combined_strain,
            "sensor_readings": sensor_readings,
        }
        return self._publish(TOPIC_STATE, payload, retain=False)

    def _publish(self, topic: str, payload: dict, retain: bool) -> bool:
        if not self.enabled or not self._connected or self._client is None:
            return False
        try:
            message = json.dumps(payload, separators=(",", ":"))
            self._client.publish(topic, message, qos=1, retain=retain)
            return True
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            return False
