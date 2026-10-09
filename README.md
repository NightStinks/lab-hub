# LAB. for Home Assistant

A Home Assistant add-on (shown as an "app" in recent Home Assistant) that lists every **LAB.** device on the network and opens its setup page from the sidebar, like the WLED app does for WLED. Part of the LAB. range of sensors (LAB. Zones, LAB. Presence).

Status: 0.3.0, testing. This repository is public so Home Assistant can install the add-on from its URL; the device firmware lives in separate private repositories.

## What it does

- **Finds devices** over mDNS: `_lab._tcp` (product, version), or ESPHome's `_esphomelib._tcp` with `project_name: lab.*`. Units from before the LAB. tags are matched by a friendly name starting "LAB.".
- **Shows** each device's name, product, firmware version, and whether someone is there (from its own web API, every 10 s).
- **Opens** a device's page through Home Assistant Ingress (`/d/<host>/...` is proxied to the device, including its live event stream), so it also works away from home. A "All LAB. devices" link is added at the top.
- **Names**: each sensor can be given a name ("Bedroom") from the list or its own page; it is stored on the sensor and copied to the device's name in Home Assistant (matched by MAC, through the Supervisor API, once per change, so a later rename in Home Assistant is kept).
- **Offline and reset devices**: the list is saved, so an unplugged or power-cycle-reset sensor stays as Offline with a Remove button; a factory reset sent through LAB. removes it straight away.
- Only Home Assistant's Ingress proxy may connect (the add-on uses the host network for mDNS).

## Layout

| Path | What |
|---|---|
| `repository.yaml` | Makes this repo an add-on repository |
| `lab/config.yaml`, `lab/Dockerfile` | The add-on (slug `lab`) |
| `lab/app/main.py` | Discovery, status and the proxy (aiohttp + zeroconf) |
| `lab/app/index.html` | The device list and how to add a new sensor |

## Run it on a laptop

```bash
python3 -m venv .venv && .venv/bin/pip install -r lab/requirements.txt
LAB_DEV=1 .venv/bin/python lab/app/main.py
```

Then open `http://localhost:8749/`. `LAB_DEV=1` lets any client connect.

## Install

In Home Assistant: Settings, Add-ons (Apps), Add-on store, ⋮, Repositories, add `https://github.com/NightStinks/lab-hub`. Then install **LAB.**, start it, and turn on Show in sidebar. Home Assistant builds it on the device, which takes a few minutes the first time.
