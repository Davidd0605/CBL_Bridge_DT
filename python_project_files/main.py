import paho.mqtt.client as mqtt
from dotenv import load_dotenv
import os

load_dotenv()

# Template connection to topic

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.username_pw_set(os.getenv("MQTT_USERNAME"), os.getenv("MQTT_PASSWORD"))
client.connect(os.getenv("MQTT_BROKER"), int(os.getenv("MQTT_PORT", 1883)))
client.publish("my/topic", "hello from python!")
client.disconnect()
