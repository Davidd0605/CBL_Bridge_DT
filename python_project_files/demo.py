import time
from MQTTclient import MQTTClient

broker = MQTTClient().connect()
time.sleep(1)  # let connection settle

# --- CALLBACKS ---


def log_temperature(topic, payload):
    print(f"[LOG] Temperature received: {payload['value']}{payload['unit']}")


def alert_if_hot(topic, payload):
    if payload["value"] > 30:
        print(f"[ALERT] Temperature too high: {payload['value']}{payload['unit']}")


def log_humidity(topic, payload):
    print(f"[LOG] Humidity received: {payload['value']}%")


def log_all_sensors(topic, payload):
    print(f"[ALL] {topic} -> {payload}")


def log_raw(topic, payload):
    print(f"[RAW] Got plain text on {topic}: {payload}")


# --- SUBSCRIPTIONS ---

# two callbacks on the same topic
broker.subscribe("sensors/temperature", log_temperature)
broker.subscribe("sensors/temperature", alert_if_hot)

# single callback
broker.subscribe("sensors/humidity", log_humidity)

# wildcard - fires for every sensors/* topic
broker.subscribe("sensors/#", log_all_sensors)

# plain text topic
broker.subscribe("chat/messages", log_raw)

time.sleep(1)  # let subscriptions register

# --- PUBLISH ---

print("\n-- publishing temperature 23 (below alert threshold) --")
broker.publish("sensors/temperature", {"value": 23, "unit": "C"})
time.sleep(0.5)

print("\n-- publishing temperature 35 (above alert threshold) --")
broker.publish("sensors/temperature", {"value": 35, "unit": "C"})
time.sleep(0.5)

print("\n-- publishing humidity --")
broker.publish("sensors/humidity", {"value": 60})
time.sleep(0.5)

print("\n-- publishing plain text --")
broker.publish("chat/messages", "hello from python!")
time.sleep(0.5)

broker.disconnect()
