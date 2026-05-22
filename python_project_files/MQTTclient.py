import paho.mqtt.client as mqtt
from dotenv import load_dotenv
import os
import json

load_dotenv()


class MQTTClient:
    """
    Simple wrapper for publishing and subscribing on the python side of the system
    Here you have a little demo:
            broker = MQTTClient().connect()
            # publish
            broker.publish("sensors/temperature", {"value": 23.5, "unit": "C"})
            # subscribe
            def on_temperature(topic, payload):
                print(f"Got temperature: {payload['value']}")
            broker.subscribe("sensors/temperature", on_temperature)
            broker.subscribe("sensors/#", lambda topic, payload: print(f"{topic}: {payload}"))
    This auto serializez/deserializez json.
    """

    def __init__(self):
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.username_pw_set(os.getenv("MQTT_USERNAME"), os.getenv("MQTT_PASSWORD"))
        self.client.on_message = self._on_message
        self._handlers = {}  # topic -> list of callbacks

    def connect(self):
        self.client.connect(os.getenv("MQTT_BROKER"), int(os.getenv("MQTT_PORT", 1883)))
        self.client.loop_start()
        return self

    def disconnect(self):
        self.client.loop_stop()
        self.client.disconnect()

    def publish(self, topic: str, payload: dict | str):
        if isinstance(payload, dict):
            payload = json.dumps(payload)
        self.client.publish(topic, payload)

    def subscribe(self, topic: str, callback):
        """callback receives (topic, payload) where payload is auto-parsed if JSON"""
        if topic not in self._handlers:
            self._handlers[topic] = []
            self.client.subscribe(topic)  # only subscribe to broker once per topic
        self._handlers[topic].append(callback)

    def _on_message(self, client, userdata, msg):
        topic = msg.topic
        try:
            payload = json.loads(msg.payload.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = msg.payload.decode()

        for pattern, callbacks in self._handlers.items():
            if pattern == topic or self._matches(pattern, topic):
                for callback in callbacks:
                    callback(topic, payload)

    def _matches(self, pattern: str, topic: str) -> bool:
        """handle MQTT wildcards: + (single level) and # (multi level)"""
        pattern_parts = pattern.split("/")
        topic_parts = topic.split("/")
        for i, part in enumerate(pattern_parts):
            if part == "#":
                return True
            if i >= len(topic_parts):
                return False
            if part != "+" and part != topic_parts[i]:
                return False
        return len(pattern_parts) == len(topic_parts)
