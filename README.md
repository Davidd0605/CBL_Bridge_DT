# CBL Bridge Digital twin system

This is the repository for the software side of the 4CBLW026 Digital Twins of Devices and Systems.

# Setup Guide

## 1.1 Mosquitto (Windows) (Local hosting)
1. Download from https://mosquitto.org/download/ -> `mosquitto-2.x.x-install-win64.exe`
2. Run the installer
3. It runs as a Windows service automatically on `localhost:1883`
### 1.2 Remote broker
1. Credentials .env file on request
## 2. Python Dependencies 
```bash

```

## 3. Unity - M2Mqtt DLL
1. Go to https://www.nuget.org/packages/M2Mqtt/
2. Click **Download package** → downloads a `.nupkg` file
3. Rename `.nupkg` to `.zip` and open it
4. Find `lib/net35/M2Mqtt.Net.dll` inside
5. Copy `M2Mqtt.Net.dll` into `Assets/Plugins/` in your Unity project
