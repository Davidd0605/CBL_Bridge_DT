# CBL Bridge Digital Twin System
This is the repository for the software side of the 4CBLW026 Digital Twins of Devices and Systems.

# Setup Guide

## 1.1 Mosquitto (Windows) — Local Hosting
1. Download from https://mosquitto.org/download/ → `mosquitto-2.x.x-install-win64.exe`
2. Run the installer
3. It runs as a Windows service automatically on `localhost:1883`

### 1.2 Remote Broker
1. Credentials `.env` file available on request

---

## 2. Python Dependencies

> **Important:** Use **Python 3.12**. Python 3.14 is not supported — several core dependencies (including `openseespy`) do not have compatible builds for it yet.

```bash
pip install openseespy paho-mqtt python-dotenv
```

### Dependency Overview

| Package | Version | Purpose |
|---|---|---|
| `openseespy` | latest | Structural FEM solver for bridge load analysis |
| `paho-mqtt` | latest | MQTT client for publishing/subscribing to the broker |
| `python-dotenv` | latest | Loading broker credentials from the `.env` file |

> `tkinter`, `json`, `math`, and `pathlib` are part of the Python standard library and do not need to be installed separately. On Linux, `tkinter` may require `sudo apt install python3-tk`.

---

## 3. Unity (6000.4.0f1) — M2Mqtt DLL
1. Go to https://www.nuget.org/packages/M2Mqtt/
2. Click **Download package** → downloads a `.nupkg` file
3. Rename `.nupkg` to `.zip` and open it
4. Find `lib/net35/M2Mqtt.Net.dll` inside
5. Copy `M2Mqtt.Net.dll` into `Assets/Plugins/` in your Unity project

---

## 4. Contributions

### General
- All contributors should work on their own **feature branch** and open a pull request to merge into `main`.
- Do not commit directly to `main`.

### Python
- Each script should be self-contained where possible.
- Shared utilities (e.g. MQTT connection helpers) go in a common module rather than being duplicated.

### Unity
To avoid merge conflicts, **all Unity contributions must be made on separate scenes.**

- Each contributor works in their own scene file (e.g. `Scenes/BridgeVisualization_Alice.unity`, `Scenes/Dashboard_Bob.unity`).
- Do **not** edit another contributor's scene directly.
- Shared prefabs and scripts in `Assets/Scripts/` and `Assets/Prefabs/` are fair game for everyone, but coordinate before making breaking changes to shared components.
- When a feature is ready to be integrated into the main scene, do it together to avoid conflicts.
