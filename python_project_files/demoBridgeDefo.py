from MQTTclient import MQTTClient
import math, time

TOPIC = "real/nodes"


def publish_node_positions(broker: MQTTClient, node_positions: dict[int, tuple[float, float, float]]):
    """
    Publish absolute node positions to Unity.
    node_positions: {node_id: (x, y, z), ...}
    Example:
        publish_node_positions(broker, {
            32: (0.30, -0.002, 0.00),
            31: (0.20, -0.001, 0.00),
        })
    You can publish all nodes or just the ones that changed — Unity keeps
    the rest at their last known position.
    """
    payload = {"nodes": [{"id": node_id, "x": x, "y": y, "z": z} for node_id, (x, y, z) in node_positions.items()]}
    broker.publish(TOPIC, payload)


if __name__ == "__main__":
    broker = MQTTClient().connect()

    # 7 nodes spread evenly across a 0.6m span
    # Sag follows a sine curve so midspan drops the most
    NUM_NODES = 7
    SPAN = 0.60  # metres
    Z_OFFSET = -0.06  # out-of-plane lane offset, same as original

    # --- static deformed shape (big visible sag) ---
    # midspan drops 0.05 m (50 mm) — very obvious in Unity at worldScale=10
    MAX_SAG = 0.05

    deformed = {}
    for i, node_id in enumerate(range(1, NUM_NODES + 1)):
        t = i / (NUM_NODES - 1)  # 0.0 ... 1.0 along span
        x = t * SPAN
        y = -MAX_SAG * math.sin(math.pi * t)  # sine sag, zero at both ends
        z = Z_OFFSET
        deformed[node_id] = (x, y, z)

    publish_node_positions(broker, deformed)
    print(f"Published {len(deformed)} node positions to {TOPIC}")
    for node_id, (x, y, z) in deformed.items():
        print(f"  node {node_id:2d}  x={x:.3f}  y={y:.4f}  z={z:.3f}")

    # --- optional: animate a bouncing sag so you can watch it move live ---
    # comment this block out if you only want a single snapshot
    print("\nAnimating... Ctrl+C to stop")
    t = 0.0
    while True:
        amplitude = MAX_SAG * abs(math.sin(t))  # amplitude pulses 0 -> MAX_SAG
        animated = {}
        for i, node_id in enumerate(range(1, NUM_NODES + 1)):
            frac = i / (NUM_NODES - 1)
            x = frac * SPAN
            y = -amplitude * math.sin(math.pi * frac)
            z = Z_OFFSET
            animated[node_id] = (x, y, z)
        publish_node_positions(broker, animated)
        t += 0.1
        time.sleep(0.05)  # ~20 Hz
