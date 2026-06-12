"""Publish demo MQTT inputs: one healthy and one damaged detection cycle.

Delta-mode detection compares:
  physical_corrected = raw_strain - tare_strain
  model_absolute     = combined_strain from the current OpenSees solve

The healthy step publishes raw strains equal to the model (with tare at zero).
The damaged step perturbs midspan gauge readings to trip the health gate.
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

LOAD_NODE = 32
LIVE_LOAD_N = 1000.0
DETECTION_WAIT_S = 8.0
STATE_WAIT_S = 10.0

# Gauges on element 77 (midspan load-cell region) — perturbed in the damaged step.
DAMAGE_GAUGE_IDS = ("LC3L", "LC3R")
DAMAGE_STRAIN_OFFSET = 5.0e-4


def _load_gauge_definitions() -> list[dict]:
    bridge_path = Path(__file__).with_name("bridge_3d_pratt.json")
    with bridge_path.open(encoding="utf-8") as bridge_file:
        bridge = json.load(bridge_file)
    return [
        {"gauge_id": str(item["gauge_id"]), "ele_id": int(item["ele_id"])}
        for item in bridge.get("strain_gauges", [])
        if item.get("gauge_id") is not None and item.get("ele_id") is not None
    ]


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

    def on_message(self, _client, _userdata, message) -> None:
        if message.topic != TOPIC_SIM_STATE:
            return
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict) or payload.get("type") != "state":
            return
        self._latest = payload
        self._event.set()

    def wait_for_state(self, timeout: float) -> dict:
        self._event.clear()
        if not self._event.wait(timeout=timeout):
            raise TimeoutError(f"no message on {TOPIC_SIM_STATE} within {timeout:.0f}s")
        assert self._latest is not None
        return self._latest


def _publish(client: mqtt.Client, topic: str, payload: dict) -> None:
    message = json.dumps(payload, separators=(",", ":"))
    info = client.publish(topic, message, qos=1, retain=False)
    info.wait_for_publish(timeout=5.0)
    print(f"  -> {topic}")


def _zero_strains(gauge_definitions: list[dict]) -> dict[str, float]:
    return {str(gauge["gauge_id"]): 0.0 for gauge in gauge_definitions}


def _damaged_strains(model_strains: dict[str, float]) -> dict[str, float]:
    perturbed = dict(model_strains)
    for gauge_id in DAMAGE_GAUGE_IDS:
        perturbed[gauge_id] = model_strains[gauge_id] + DAMAGE_STRAIN_OFFSET
    return perturbed


def _wait_for_detection() -> None:
    print(f"  waiting {DETECTION_WAIT_S:.0f}s for debounce + detection interval...")
    time.sleep(DETECTION_WAIT_S)


def main() -> int:
    gauge_definitions = _load_gauge_definitions()
    if not gauge_definitions:
        raise SystemExit("no strain_gauges in bridge_3d_pratt.json")

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
    client.loop_start()

    print(f"Connected to {host}:{port}")
    print("Ensure bridge_model.py is running (restart it after code changes).\n")

    print("1) Clear live load (session tare at 0 N)")
    _publish(client, TOPIC_LOAD, {"node": LOAD_NODE, "load_n": 0.0})
    listener.wait_for_state(STATE_WAIT_S)

    zeros = _zero_strains(gauge_definitions)
    print("2) Session-tare at 0 N (readings in command — avoids spurious detection)")
    _publish(client, TOPIC_COMMAND, {"action": "tare", "readings": zeros})
    time.sleep(2.0)

    print(f"3) Apply {LIVE_LOAD_N:.0f} N at node {LOAD_NODE}")
    _publish(client, TOPIC_LOAD, {"node": LOAD_NODE, "load_n": LIVE_LOAD_N})
    state = listener.wait_for_state(STATE_WAIT_S)
    model_strains = _strains_from_sim_state(state, gauge_definitions)
    print(
        "   model strains at load "
        f"(sample LC3L={model_strains['LC3L']:.3e}, LC3R={model_strains['LC3R']:.3e})"
    )

    print("4) Healthy cycle — publish model-matched physical strains")
    _publish(client, TOPIC_REAL_STATE, {"physical_strains": model_strains})
    _wait_for_detection()
    print(f"   expect {TOPIC_SIM_DAMAGE} with healthy=true, flagged_element_ids=[]\n")

    damaged = _damaged_strains(model_strains)
    print(
        "5) Damaged cycle — perturb "
        f"{', '.join(DAMAGE_GAUGE_IDS)} by +{DAMAGE_STRAIN_OFFSET:.1e}"
    )
    _publish(client, TOPIC_REAL_STATE, {"physical_strains": damaged})
    _wait_for_detection()
    print(
        f"   expect {TOPIC_SIM_DAMAGE} with healthy=false and non-empty flagged_element_ids"
    )

    print("\nDone. Check:")
    print(f"  {TOPIC_SIM_DAMAGE}")

    client.loop_stop()
    client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
