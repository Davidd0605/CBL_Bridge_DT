"""Publish demo MQTT inputs: one healthy and one damaged detection cycle.

Delta-mode detection compares:
  physical_corrected = raw_strain - tare_strain
  model_absolute     = combined_strain from the current OpenSees solve

The healthy step publishes raw strains equal to the model (with tare at zero).
The damaged step perturbs LC3/LC4 load-cell member readings to trip the health gate.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

try:
    import paho.mqtt.client as mqtt
except ImportError as exc:
    raise SystemExit("paho-mqtt required: pip install paho-mqtt") from exc

TOPIC_LOAD = "cbl/bridge/load"
TOPIC_COMMAND = "cbl/bridge/command"
TOPIC_REAL_STATE = "cbl/bridge/real/state"
TOPIC_SIM_STATE = "cbl/bridge/sim/state"
TOPIC_SIM_DAMAGE = "cbl/bridge/sim/damage"

LIVE_LOAD_N = 1000.0
DETECTION_WAIT_S = 8.0
STATE_WAIT_S = 10.0

DAMAGE_STRAIN_OFFSET = 5.0e-4


def _damage_gauge_ids(gauge_definitions: list[dict]) -> tuple[str, ...]:
    """LC3/LC4 load-cell gauges on the center bottom cross-beam members."""
    available = {g["gauge_id"] for g in gauge_definitions}
    preferred = ("LC3L", "LC3R", "LC4L", "LC4R")
    selected = tuple(gid for gid in preferred if gid in available)
    if len(selected) >= 2:
        return selected
    raise SystemExit(
        "bridge_3d_pratt.json must define strain_gauges LC3L, LC3R, LC4L, LC4R "
        "on the LC3/LC4 bottom cross-beam members."
    )


def _load_bridge_config() -> tuple[int, list[dict], tuple[str, ...], dict]:
    bridge_path = Path(__file__).with_name("bridge_3d_pratt.json")
    with bridge_path.open(encoding="utf-8") as bridge_file:
        bridge = json.load(bridge_file)
    gauge_definitions = [
        {"gauge_id": str(item["gauge_id"]), "ele_id": int(item["ele_id"])}
        for item in bridge.get("strain_gauges", [])
        if item.get("gauge_id") is not None and item.get("ele_id") is not None
    ]
    load_node = int(bridge.get("midspan_sensor_node", 4))
    damage_gauge_ids = _damage_gauge_ids(gauge_definitions)
    detection = dict(bridge.get("damage_detection", {}))
    detection.setdefault("debounce_seconds", 1.5)
    detection.setdefault("min_interval_seconds", 5.0)
    return load_node, gauge_definitions, damage_gauge_ids, detection


def _strains_from_sim_state(state: dict, gauge_definitions: list[dict]) -> dict[str, float]:
    element_ids = state.get("element_ids") or []
    combined = state.get("combined_strain") or []
    index_by_element = {int(element_id): index for index, element_id in enumerate(element_ids)}
    strains: dict[str, float] = {}
    for gauge in gauge_definitions:
        element_id = int(gauge["ele_id"])
        if element_id not in index_by_element:
            raise ValueError(f"element {element_id} missing from sim/state payload")
        strains[str(gauge["gauge_id"])] = float(combined[index_by_element[element_id]])
    return strains


class SimStateListener:
    def __init__(self) -> None:
        self._event = threading.Event()
        self._latest: dict | None = None
        self._damage_event = threading.Event()
        self._latest_damage: dict | None = None

    def on_message(self, _client, _userdata, message) -> None:
        if message.topic == TOPIC_SIM_STATE:
            try:
                payload = json.loads(message.payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return
            if not isinstance(payload, dict) or payload.get("type") != "state":
                return
            self._latest = payload
            self._event.set()
            return

        if message.topic == TOPIC_SIM_DAMAGE:
            try:
                payload = json.loads(message.payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return
            if not isinstance(payload, dict):
                return
            self._latest_damage = payload
            self._damage_event.set()

    def wait_for_state(self, timeout: float) -> dict:
        self._event.clear()
        if not self._event.wait(timeout=timeout):
            raise TimeoutError(f"no message on {TOPIC_SIM_STATE} within {timeout:.0f}s")
        assert self._latest is not None
        return self._latest

    def wait_for_damage(self, timeout: float, after_timestamp: float = 0.0) -> dict | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            if not self._damage_event.wait(timeout=remaining):
                break
            payload = self._latest_damage
            self._damage_event.clear()
            if not isinstance(payload, dict):
                continue
            if float(payload.get("timestamp", 0.0)) > after_timestamp:
                return payload
        return None


def _publish(client: mqtt.Client, topic: str, payload: dict) -> None:
    message = json.dumps(payload, separators=(",", ":"))
    info = client.publish(topic, message, qos=1, retain=False)
    info.wait_for_publish(timeout=5.0)
    print(f"  -> {topic}")


def _zero_strains(gauge_definitions: list[dict]) -> dict[str, float]:
    return {str(gauge["gauge_id"]): 0.0 for gauge in gauge_definitions}


def _damaged_strains(model_strains: dict[str, float], damage_gauge_ids: tuple[str, ...]) -> dict[str, float]:
    perturbed = dict(model_strains)
    for gauge_id in damage_gauge_ids:
        perturbed[gauge_id] = model_strains[gauge_id] + DAMAGE_STRAIN_OFFSET
    return perturbed


def _wait_for_detection(
    listener: SimStateListener,
    label: str,
    *,
    after_timestamp: float = 0.0,
    timeout: float = DETECTION_WAIT_S,
) -> dict | None:
    print(f"  waiting up to {timeout:.0f}s for {TOPIC_SIM_DAMAGE} ({label})...")
    damage = listener.wait_for_damage(timeout, after_timestamp=after_timestamp)
    if damage is None:
        print(f"  WARNING: no new message received on {TOPIC_SIM_DAMAGE}")
        print("  Restart bridge_model.py after pulling latest changes, then rerun this demo.")
        return None
    healthy = damage.get("healthy")
    flagged = damage.get("flagged_element_ids", [])
    print(
        f"  received {TOPIC_SIM_DAMAGE}: healthy={healthy}, "
        f"flagged_element_ids={flagged}"
    )
    return damage


def main() -> int:
    load_node, gauge_definitions, damage_gauge_ids, detection_settings = _load_bridge_config()
    if not gauge_definitions:
        raise SystemExit("no strain_gauges in bridge_3d_pratt.json")
    debounce_s = float(detection_settings["debounce_seconds"])
    min_interval_s = float(detection_settings["min_interval_seconds"])
    between_cycles_s = debounce_s + min_interval_s + 1.0
    host = os.environ.get("MQTT_BROKER") or "localhost"
    port = int(os.environ.get("MQTT_PORT") or 1883)
    username = os.environ.get("MQTT_USERNAME")
    password = os.environ.get("MQTT_PASSWORD")

    listener = SimStateListener()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="demo_mqtt_prompt")
    if username:
        client.username_pw_set(username, password or None)
    client.on_message = listener.on_message
    client.connect(host, port, keepalive=60)
    client.subscribe(TOPIC_SIM_STATE, qos=1)
    client.subscribe(TOPIC_SIM_DAMAGE, qos=1)
    client.loop_start()

    print(f"Connected to {host}:{port}")
    print("Ensure bridge_model.py is running (restart it after code changes).")
    print(f"Subscribe to damage results on: {TOPIC_SIM_DAMAGE}\n")

    print("1) Clear live load (session tare at 0 N)")
    _publish(client, TOPIC_LOAD, {"node": load_node, "load_n": 0.0})
    listener.wait_for_state(STATE_WAIT_S)

    zeros = _zero_strains(gauge_definitions)
    print("2) Session-tare at 0 N (readings in command — avoids spurious detection)")
    _publish(client, TOPIC_COMMAND, {"action": "tare", "readings": zeros})
    time.sleep(2.0)

    print(f"3) Apply {LIVE_LOAD_N:.0f} N at node {load_node}")
    _publish(client, TOPIC_LOAD, {"node": load_node, "load_n": LIVE_LOAD_N})
    state = listener.wait_for_state(STATE_WAIT_S)
    model_strains = _strains_from_sim_state(state, gauge_definitions)
    lc3 = damage_gauge_ids[0], damage_gauge_ids[1]
    lc4 = damage_gauge_ids[2], damage_gauge_ids[3]
    print(
        "   model strains at load "
        f"(LC3 {lc3[0]}={model_strains[lc3[0]]:.3e}, {lc3[1]}={model_strains[lc3[1]]:.3e}; "
        f"LC4 {lc4[0]}={model_strains[lc4[0]]:.3e}, {lc4[1]}={model_strains[lc4[1]]:.3e})"
    )

    print("4) Healthy reading — publish model-matched physical strains")
    _publish(client, TOPIC_REAL_STATE, {"physical_strains": model_strains})
    healthy_result = _wait_for_detection(listener, "healthy")
    healthy_ts = float((healthy_result or {}).get("timestamp", 0.0))

    print(
        f"   pausing {between_cycles_s:.1f}s so the next detection cycle is not rate-limited..."
    )
    time.sleep(between_cycles_s)

    damaged = _damaged_strains(model_strains, damage_gauge_ids)
    print(
        "5) Non-healthy reading — perturb LC3/LC4 member gauges "
        f"{', '.join(damage_gauge_ids)} by +{DAMAGE_STRAIN_OFFSET:.1e}"
    )
    _publish(client, TOPIC_REAL_STATE, {"physical_strains": damaged})
    damaged_result = _wait_for_detection(
        listener,
        "non-healthy",
        after_timestamp=healthy_ts,
        timeout=DETECTION_WAIT_S + between_cycles_s,
    )
    if damaged_result and damaged_result.get("healthy") is not False:
        print(
            "  WARNING: expected non-healthy result on LC3/LC4 members; "
            "check that bridge_model.py was restarted with the latest code."
        )

    print("\nDone. Check:")
    print(f"  {TOPIC_SIM_DAMAGE}")

    client.loop_stop()
    client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
