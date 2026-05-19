import paho.mqtt.client as mqtt

# template connection
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.username_pw_set("myuser", "cblbroker123")
client.connect("80.113.118.200", 1883)
client.publish("my/topic", "hello from python!")
client.disconnect()
