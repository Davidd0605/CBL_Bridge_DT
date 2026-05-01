import json
import time
import paho.mqtt.client as mqtt
from epyt.epanet import epanet

# epyt network setup, can import existing map made with GUI (i think)
d = epanet("test_network.inp", createinp=True)
d.addNodeJunction("J1")
d.addNodeJunction("J2")
d.addNodeReservoir("R1")
d.addLinkPipe("P1", "J1", "J2")
d.addLinkPipe("P2", "R1", "J1")
d.setNodeJunctionData(1, 100, 50, "")
d.setNodeJunctionData(2, 90, 30, "")
d.setLinkPipeData(1, 1000, 12, 100, 0)
d.setLinkPipeData(2, 500, 10, 100, 0)

# how demand changes over time so we get some fluctuation in the readings
pattern = [
    0.5,
    0.4,
    0.4,
    0.4,
    0.5,
    0.8,
    1.0,
    1.2,
    1.2,
    1.0,
    0.9,
    0.8,
    0.8,
    0.9,
    1.0,
    1.1,
    1.2,
    1.1,
    1.0,
    0.9,
    0.8,
    0.7,
    0.6,
    0.5,
]

d.addPattern("DP", pattern)
d.setNodeJunctionData(1, 100, 50, "DP")
d.setNodeJunctionData(2, 90, 30, "DP")

# run simulation
d.setTimeSimulationDuration(24 * 3600)
d.setTimeHydraulicStep(3600)
d.setTimeReportingStep(3600)

results = d.getComputedTimeSeries()
current_step = 0


# callback mqtt


# rc = return code, for successful execution
def on_connect(client, userdata, flags, rc):
    """On connect to broker (mosquito), subscribe to epanet control topic on which unity may publish"""
    print(f"Connected to broker (rc={rc})")
    client.subscribe("epanet/control")
    print("Subscribed to epanet/control")


def on_message(client, userdata, msg):
    """Handle commands from Unity side. msgs are encoded in json files"""
    global current_step
    payload = json.loads(msg.payload.decode())
    print(f"Command from Unity: {payload}")

    action = payload.get("action")

    if action == "set_step":
        current_step = int(payload.get("step", 0))
        print(f"Step set to {current_step}")

    elif action == "set_demand":
        node = payload.get("node", 1)
        value = payload.get("demand", 50)
        d.setNodeBaseDemands(node, value)
        print(f"Demand at node {node} set to {value}")
    elif action == "mouse_click":
        print("MOUSE HAS BEEN CLICKED IN UNITY")


# creating mqtt client
client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message

client.connect("localhost", 1883)
client.loop_start()  # handles callbacks in the background, listener (kinda)

# main python loop
print("Publishing EPyT data...")
try:
    while True:
        step = min(current_step, len(results.Time) - 1)

        data = {
            "step": step,
            "time_hours": round(results.Time[step] / 3600, 1),
            "pressures": {
                "J1": round(float(results.Pressure[step, 0]), 2),
                "J2": round(float(results.Pressure[step, 1]), 2),
            },
            "flows": {
                "P1": round(float(results.Flow[step, 0]), 4),
                "P2": round(float(results.Flow[step, 1]), 4),
            },
        }

        client.publish("epanet/data", json.dumps(data))
        print(f"Published: {data}")

        current_step = (current_step + 1) % len(results.Time)
        time.sleep(1)

except KeyboardInterrupt:  # ctrlc interrupt
    print("Shutting down")
    d.unload()
    client.loop_stop()
    client.disconnect()
