# MQTT_Unity_Python

# Setup Guide

## 1. Mosquitto (Windows)
1. Download from https://mosquitto.org/download/ -> `mosquitto-2.x.x-install-win64.exe`
2. Run the installer
3. It runs as a Windows service automatically on `localhost:1883`

## 2. Python Dependencies
```bash
pip install epyt
pip install paho-mqtt
```

## 3. Unity - M2Mqtt DLL
1. Go to https://www.nuget.org/packages/M2Mqtt/
2. Click **Download package** → downloads a `.nupkg` file
3. Rename `.nupkg` to `.zip` and open it
4. Find `lib/net35/M2Mqtt.Net.dll` inside
5. Copy `M2Mqtt.Net.dll` into `Assets/Plugins/` in your Unity project
