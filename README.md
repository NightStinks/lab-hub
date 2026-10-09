# LAB. for Home Assistant

A Home Assistant add-on (shown as an "app" in recent Home Assistant) that lists every **LAB.** device on the network and opens its setup page from the sidebar, like the WLED app does for WLED. Part of the LAB. range: [LAB. Zones](https://github.com/NightStinks/lab-zones), [LAB. Presence](https://github.com/NightStinks/lab-presence).

Status: 0.1.0, internal testing. Private.

## What it does

- **Finds devices** over mDNS: `_lab._tcp` (product, version), or ESPHome's `_esphomelib._tcp` with `project_name: lab.*`. Units from before the LAB. tags are matched by a friendly name starting "LAB.".
- **Shows** each device's name, product, firmware version, and whether someone is there (from its own web API, every 10 s).
- **Opens** a device's page through Home Assistant Ingress (`/d/<host>/...` is proxied to the device, including its live event stream), so it also works away from home. A "All LAB. devices" link is added at the top.
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

## Install for testing (repository is private)

Copy the `lab` folder into Home Assistant's `/addons` folder (for example from the Terminal add-on), then Settings, Add-ons, Add-on store, ⋮, Check for updates. It appears under **Local add-ons** as **LAB.**. Once the repository is public, it can instead be added as a repository URL.
