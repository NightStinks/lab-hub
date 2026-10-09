"""LAB. Home Assistant add-on: finds LAB. devices on the network and opens their pages through Ingress.

- Discovery: mDNS. LAB. devices advertise _lab._tcp (product, version). ESPHome-based ones also
  advertise _esphomelib._tcp with project_name lab.* and friendly_name; older units without the LAB.
  tags are matched by a friendly name starting with "LAB.".
- Status: each device's own web API (GET /binary_sensor/Presence, /sensor/People) every 10 s.
- Pages: /d/<host>/... is proxied to http://<device>/..., streaming (the page uses Server-Sent Events).
  The device's page loads its script from /0.js; that is rewritten to a relative path, and the LAB.
  page works out its API base from its own URL, so it runs unchanged under the Ingress path.
- Names: a LAB. device stores the name its owner gives it (text "Sensor name"). The list shows it, can
  rename it, and copies it to the device's name in Home Assistant (matched by MAC) through the Supervisor
  API. A name is copied once each time it changes on the sensor, so a later rename in Home Assistant
  is left alone.
- The list is saved (/data/devices.json), so an unplugged device still shows, as Offline, with Remove. A
  factory reset sent through the add-on removes the device from the list straight away.
- Only Home Assistant's Ingress proxy (172.30.32.2) may connect, unless LAB_DEV=1 (local testing).
"""
import asyncio, gzip, json, os, socket, time
from pathlib import Path
from aiohttp import ClientConnectionResetError, ClientSession, ClientTimeout, web
from zeroconf import ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

PORT = int(os.environ.get("LAB_PORT", "8749"))
DEV = os.environ.get("LAB_DEV") == "1"
INGRESS_IP = "172.30.32.2"
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
DATA = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
SYNCED = DATA / "names_synced.json"        # mac -> the sensor name last copied to Home Assistant
KNOWN = DATA / "devices.json"              # devices seen before, so an unplugged one still shows (as Offline)
KEEP = ("host", "ip", "name", "product", "version", "mac", "label", "can_rename")
TYPES = ["_lab._tcp.local.", "_esphomelib._tcp.local."]
PRODUCTS = {"zones": "LAB. Zones", "presence": "LAB. Presence"}
HERE = Path(__file__).parent

devices: dict[str, dict] = {}   # key: host name without .local, e.g. lab-zones-a5036c


def save_known() -> None:
    try:
        KNOWN.write_text(json.dumps({h: {k: d.get(k) for k in KEEP} for h, d in devices.items()}))
    except Exception as e:
        print(f"Could not save the device list: {e}")


def load_known() -> None:
    try:
        for h, d in json.loads(KNOWN.read_text()).items():
            devices[h] = {**d, "online": None, "presence": None, "people": None, "seen": 0}
    except Exception:
        pass


def forget(host: str) -> None:
    if devices.pop(host, None) is not None:
        save_known()


def txt(info: AsyncServiceInfo) -> dict:
    return {k.decode(): (v or b"").decode(errors="replace") for k, v in info.properties.items()}


async def on_service(zc: AsyncZeroconf, type_: str, name: str) -> None:
    info = AsyncServiceInfo(type_, name)
    if not await info.async_request(zc.zeroconf, 3000) or not info.server:
        return
    t = txt(info)
    if type_.startswith("_esphomelib"):
        is_lab = t.get("project_name", "").startswith("lab.") or t.get("friendly_name", "").startswith("LAB.")
        if not is_lab:
            return
    host = info.server.rstrip(".").removesuffix(".local")
    ips = [socket.inet_ntoa(a) for a in info.addresses if len(a) == 4]
    d = devices.setdefault(host, {"host": host, "online": None, "presence": None, "people": None, "seen": 0})
    if ips:
        d["ip"] = ips[0]
    if type_.startswith("_lab"):
        d["product"] = t.get("product", d.get("product"))
        d["version"] = t.get("version", d.get("version"))
    else:
        d["name"] = t.get("friendly_name") or d.get("name")
        if not d.get("product"):
            pn = t.get("project_name", "")
            d["product"] = pn.split(".", 1)[1] if pn.startswith("lab.") else ("presence" if "presence" in host else None)
        d["version"] = t.get("project_version") or d.get("version")
        d["mac"] = t.get("mac")
    d.setdefault("name", PRODUCTS.get(d.get("product") or "", host))
    save_known()


async def discover(app: web.Application) -> None:
    zc = AsyncZeroconf()
    app["zc"] = zc

    def handler(zeroconf, service_type, name, state_change):
        if state_change in (ServiceStateChange.Added, ServiceStateChange.Updated):
            asyncio.ensure_future(on_service(zc, service_type, name))

    app["browser"] = AsyncServiceBrowser(zc.zeroconf, TYPES, handlers=[handler])


async def get_value(s: ClientSession, d: dict, path: str, missing=None):
    try:
        async with s.get(f"http://{d['ip']}/{path}") as r:
            return (await r.json(content_type=None)).get("value") if r.status == 200 else missing
    except Exception:
        return missing


async def poll(app: web.Application) -> None:
    async with ClientSession(timeout=ClientTimeout(total=4)) as s:
        while True:
            for d in list(devices.values()):
                if not d.get("ip"):
                    continue
                try:
                    async with s.get(f"http://{d['ip']}/binary_sensor/Presence") as r:
                        d["presence"] = (await r.json(content_type=None)).get("value")
                    d["online"], d["seen"] = True, time.time()
                except Exception:
                    d["online"] = False
                    continue
                # Optional: not every LAB. device has these (older firmware drops the connection instead of a 404).
                d["people"] = await get_value(s, d, "sensor/People")
                label = await get_value(s, d, "text/Sensor%20name", missing=None)
                before = (d.get("label"), d.get("can_rename"))
                d["can_rename"] = label is not None
                d["label"] = (label or "").strip()
                if (d["label"], d["can_rename"]) != before:
                    save_known()
            await sync_names()
            await asyncio.sleep(10)


# ---- Home Assistant device names ----

def mac_colons(mac: str | None) -> str | None:
    return ":".join(mac[i:i + 2] for i in range(0, 12, 2)).lower() if mac and len(mac) == 12 else None


async def ha_ws(messages: list[dict]) -> list[dict]:
    """Send messages over Home Assistant's websocket (through the Supervisor) and return the replies."""
    async with ClientSession() as s, s.ws_connect("http://supervisor/core/websocket", timeout=10) as ws:
        await ws.receive_json()
        await ws.send_json({"type": "auth", "access_token": SUPERVISOR_TOKEN})
        if (await ws.receive_json()).get("type") != "auth_ok":
            raise RuntimeError("Home Assistant refused the add-on's token")
        out = []
        for i, m in enumerate(messages, 1):
            await ws.send_json({"id": i, **m})
            while True:
                r = await ws.receive_json()
                if r.get("id") == i:
                    out.append(r); break
        return out


async def sync_names(force: str | None = None) -> None:
    """Copy each sensor's name to its Home Assistant device, once per change (or now, for `force` host)."""
    if not SUPERVISOR_TOKEN:
        return
    try:
        synced = json.loads(SYNCED.read_text()) if SYNCED.exists() else {}
    except Exception:
        synced = {}
    todo = {}
    for d in devices.values():
        mac = mac_colons(d.get("mac"))
        if mac and d.get("label") and (synced.get(mac) != d["label"] or d["host"] == force):
            todo[mac] = d["label"]
    if not todo:
        return
    try:
        reg = (await ha_ws([{"type": "config/device_registry/list"}]))[0].get("result", [])
        updates, done = [], []
        for dev in reg:
            for kind, value in dev.get("connections", []):
                if kind == "mac" and value.lower() in todo:
                    updates.append({"type": "config/device_registry/update", "device_id": dev["id"], "name_by_user": todo[value.lower()]})
                    done.append(value.lower())
        if updates:
            await ha_ws(updates)
        for mac in done:                 # only remember it once Home Assistant actually has the device
            synced[mac] = todo[mac]
        SYNCED.write_text(json.dumps(synced))
    except Exception as e:
        print(f"Could not update names in Home Assistant: {e}")


async def api_forget(request: web.Request) -> web.Response:
    host = str((await request.json()).get("host", ""))
    d = devices.get(host)
    if d and d.get("online") is not False:          # only once it has been checked and found offline
        raise web.HTTPConflict(text="Only devices found to be offline can be removed. Try again in a few seconds.")
    forget(host)
    return web.json_response({"ok": True})


async def api_rename(request: web.Request) -> web.Response:
    data = await request.json()
    host, name = str(data.get("host", "")), str(data.get("name", "")).strip()[:32]
    d = devices.get(host)
    if not d or not d.get("ip"):
        raise web.HTTPNotFound(text="That device is not on the network right now.")
    s: ClientSession = request.app["http"]
    async with s.post(f"http://{d['ip']}/text/Sensor%20name/set", params={"value": name}, data=b"") as r:
        if r.status != 200:
            raise web.HTTPBadGateway(text="The device did not accept the name. It may need a firmware update.")
    d["label"] = name
    save_known()
    await sync_names(force=host)
    return web.json_response({"ok": True, "label": name, "home_assistant": bool(SUPERVISOR_TOKEN)})


@web.middleware
async def only_ingress(request: web.Request, handler):
    if not DEV and request.remote != INGRESS_IP:
        raise web.HTTPForbidden(text="Open LAB. from the Home Assistant sidebar.")
    return await handler(request)


async def index(request: web.Request) -> web.Response:
    return web.Response(text=(HERE / "index.html").read_text(), content_type="text/html")


async def api_devices(request: web.Request) -> web.Response:
    out = sorted(devices.values(), key=lambda d: (d.get("name") or d["host"]).lower())
    return web.json_response(out)


OLD_BASE = b'const BASE = DEV ? `http://${DEV}` : "";'
NEW_BASE = b'const BASE = DEV ? `http://${DEV}` : location.pathname.replace(/\\/[^/]*$/, "");'

BACK = ('<a href="../../" style="position:fixed;z-index:30;bottom:16px;left:16px;'
        'font:500 13px system-ui,sans-serif;color:#E0D8CE;background:#333;border-radius:999px;padding:6px 14px;'
        'text-decoration:none;opacity:.92">&larr; All LAB. devices</a>')


async def proxy(request: web.Request) -> web.StreamResponse:
    host, tail = request.match_info["host"], request.match_info["tail"]
    d = devices.get(host)
    if not d or not d.get("ip"):
        raise web.HTTPNotFound(text="That device is not on the network right now.")
    url = f"http://{d['ip']}/{tail}"
    body = await request.read()
    headers = {k: v for k, v in request.headers.items() if k.lower() in ("accept", "content-type", "last-event-id")}
    s: ClientSession = request.app["http"]
    try:
        upstream = await s.request(request.method, url, params=request.query, data=body or None, headers=headers,
                                   timeout=ClientTimeout(total=None, sock_connect=5))
    except Exception:
        raise web.HTTPBadGateway(text="Could not reach the device.")
    async with upstream:
        ctype = upstream.headers.get("Content-Type", "")
        # A factory reset sent through here: the device is about to leave the network, so drop it from the list.
        if request.method == "POST" and upstream.status == 200 and tail.replace("%20", " ") == "button/Factory reset/press":
            forget(host)
        if tail in ("", "index.html") and "text/html" in ctype and not upstream.headers.get("Content-Encoding"):
            html = (await upstream.text()).replace("src=/0.js", "src=0.js").replace('src="/0.js"', 'src="0.js"')
            html = html.replace("<body>", "<body>" + BACK, 1)
            return web.Response(text=html, content_type="text/html", status=upstream.status)
        if tail == "0.js" and upstream.headers.get("Content-Encoding") == "gzip":
            # Device pages from before LAB. Zones 0.6 / the Presence fix load the API from "/", which under
            # Ingress is Home Assistant's own root. Patch that one line so they work until they are updated.
            raw = gzip.decompress(await upstream.read())
            fixed = raw.replace(OLD_BASE, NEW_BASE)
            return web.Response(body=gzip.compress(fixed) if fixed != raw else gzip.compress(raw), status=upstream.status,
                                headers={"Content-Type": ctype or "text/javascript", "Content-Encoding": "gzip"})
        resp = web.StreamResponse(status=upstream.status)
        for k in ("Content-Type", "Content-Encoding", "Cache-Control"):
            if k in upstream.headers:
                resp.headers[k] = upstream.headers[k]
        if "text/event-stream" in ctype:
            resp.headers["X-Accel-Buffering"] = "no"
        await resp.prepare(request)
        try:
            async for chunk in upstream.content.iter_any():
                await resp.write(chunk)
            await resp.write_eof()
        except (ConnectionResetError, ClientConnectionResetError, asyncio.CancelledError):
            pass                     # the page was closed (normal for the live event stream); drop the device side too
        return resp


async def start(app: web.Application) -> None:
    load_known()
    app["http"] = ClientSession(auto_decompress=False)
    await discover(app)
    app["poller"] = asyncio.create_task(poll(app))


async def stop(app: web.Application) -> None:
    app["poller"].cancel()
    await app["browser"].async_cancel()
    await app["zc"].async_close()
    await app["http"].close()


def main() -> None:
    app = web.Application(middlewares=[only_ingress])
    app.router.add_get("/", index)
    app.router.add_get("/api/devices", api_devices)
    app.router.add_post("/api/rename", api_rename)
    app.router.add_post("/api/forget", api_forget)
    app.router.add_get("/d/{host}", lambda r: web.HTTPFound(f"{r.match_info['host']}/"))
    app.router.add_route("*", "/d/{host}/{tail:.*}", proxy)
    app.on_startup.append(start)
    app.on_cleanup.append(stop)
    print(f"LAB. listening on {PORT}{' (dev: any client)' if DEV else ''}")
    web.run_app(app, host="0.0.0.0", port=PORT, print=None)


if __name__ == "__main__":
    main()
