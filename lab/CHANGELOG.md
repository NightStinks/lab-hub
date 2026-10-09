# Changelog

## 0.3.0
- Sensors stay listed when unplugged or reset (shown as Offline, with Remove), including after the add-on restarts.
- A factory reset done through LAB. removes the sensor from the list straight away.
- When a reset sensor is set up again on the same network it comes back as the same entry.

## 0.2.0
- Name your sensors ("Bedroom") from the list; the name is stored on the sensor and copied to its device in Home Assistant.
- Devices with older firmware no longer show as offline.
- The link back to the list sits bottom-left so it does not cover the sensor's page.

## 0.1.0
- First version (built on python:3.12-slim): finds LAB. devices (mDNS `_lab._tcp`, or ESPHome `project_name: lab.*`), shows their status, and opens their pages through Ingress.
