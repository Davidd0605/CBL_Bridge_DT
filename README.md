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

---

## 5. MQTT messages published by Python
The Python bridge app publishes three JSON MQTT messages:

1. `cbl/bridge/sim/geometry` (retained)
   - `type`: `geometry`
   - `timestamp`: Unix epoch time
   - `bridge_name`: model name from `bridge_3d_pratt.json`
   - `nodes`: list of node coordinates (`id`, `label`, `x`, `y`, `z`)
   - `elements`: list of element connectivity (`id`, `i`, `j`, `type`)
   - `supports`: support node definitions
   - `deflection_sensor_points`: sensor nodes used for deflection readout

2. `cbl/bridge/sim/state`
   - `type`: `state`
   - `timestamp`: Unix epoch time
   - `analysis_completed`: whether the current static analysis finished
   - `selected_load_node`: currently active load node
   - `live_load_n`: total applied live load in newtons
   - `self_weight_n`: self-weight load in newtons
   - `node_loads`: applied live loads per node
   - `visual_defo_scale`: display scaling factor for deformations
   - `comparison_mode`, `comparison_tare_active`, `comparison_tare_load_n`: current comparison settings and session tare metadata
   - `node_ids`, `disp_x`, `disp_y`, `disp_z`: per-node displacement values published as arrays
   - `element_ids`, `utilization`, `axial_strain`, `bending_strain`, `combined_strain`: per-element beam/element results
   - `sensor_readings`: per-sensor node deflection values including live and total vertical displacement

3. `cbl/bridge/sim/damage` (non-retained, published after each detection cycle)
   - `type`: `damage_detection`
   - `timestamp`: Unix epoch time
   - `healthy`: whether strain patterns match the healthy reference
   - `flagged_element_ids`: OpenSees element IDs flagged as most likely damaged (empty when healthy)
   - `comparison_mode`, `comparison_tare_active`: comparison settings used for the cycle
   - `best_ortho`, `best_nrmse`: ranked damage scenarios with `scenario_id`, `element_ids`, `MAC`, and error metrics
   - `agreement`: whether the orthogonality and NRMSE rankers picked the same scenario
   - `is_healthy_metrics`: MAC / orthogonality / NRMSE gate values when the bridge is classified healthy

Physical strain input for detection arrives on `cbl/bridge/real/state` (`strain_readings` or `physical_strains`). Configure gauge-to-element mapping in `strain_gauges` inside `bridge_3d_pratt.json`.

For the default `delta` comparison mode, use a session tare: publish healthy bridge strains on `cbl/bridge/real/state`, then send `{"action":"tare"}` on `cbl/bridge/command` once at the start of the monitoring session. After that, vary live load through `cbl/bridge/load` and continue consuming `cbl/bridge/sim/damage`; load changes do not require another tare. Use `{"action":"clear_tare"}` only when ending or resetting the session baseline.


### What is calculated
- Node deformations are node-based displacements, published as `disp_x`, `disp_y`, and `disp_z` for each node.
- Strains are calculated per-element and published as element-level `axial_strain`, `bending_strain`, and `combined_strain` values.
- Sensor readouts are taken from the bridge model sensor points and include both live-load delta and total vertical displacement when available.

### Notes
- Geometry is sent once and retained, so Unity can rebuild the bridge model from node and element data.
- State updates are sent continuously while the model is active and the MQTT publisher is enabled.
