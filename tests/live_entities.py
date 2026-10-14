"""Offline harness: fetch live data from the aGate with the integration's own
client/coordinator command lists, then evaluate every entity the platforms would
create. No running Home Assistant needed (uses the HA package for classes only).

Run:  <venv with homeassistant>/bin/python tests/live_entities.py <aGate IP>
"""
import asyncio
import os
import sys
import types

ROOT = os.path.join(os.path.dirname(__file__), "..", "custom_components")
sys.path.insert(0, os.path.abspath(ROOT))

from franklinwh_local import binary_sensor, button, number, select, sensor, switch, time  # noqa: E402
from franklinwh_local.client import AgateClient  # noqa: E402
from franklinwh_local.const import (  # noqa: E402
    CMD_MODE_LIST, CONFIG_CMDS, DETAIL_CMDS, DETAIL_DEVICE_CMDS, FAST_CMDS,
    MODE_LIST_REQUEST, STATIC_CMDS, STATIC_DEVICE_CMDS,
)
from franklinwh_local.coordinator import AgateRuntime, discover_devices  # noqa: E402


class StubCoordinator:
    def __init__(self, data):
        self.data = data
        self.last_update_success = True

    def async_add_listener(self, *a, **k):
        return lambda: None


async def fetch(client, cmds, dev_cmds=(), ids=(1,)):
    out = {}
    for c in cmds:
        out[c] = await client.request(c, MODE_LIST_REQUEST if c == CMD_MODE_LIST else {"opt": 0})
    for c in dev_cmds:
        for d in ids:
            out[(c, d)] = await client.request(c, {"opt": 0, "id": d})
    return out


async def main(host):
    client = AgateClient(host)
    identity = await client.identify()
    devices = await discover_devices(client)
    ids = sorted(devices)
    rt = AgateRuntime(
        client=client, serial=client.equip_no, identity=identity, device_ids=ids, device_serials=devices,
        fast=StubCoordinator(await fetch(client, FAST_CMDS)),
        detail=StubCoordinator(await fetch(client, DETAIL_CMDS, DETAIL_DEVICE_CMDS, ids)),
        config=StubCoordinator(await fetch(client, (CMD_MODE_LIST, *CONFIG_CMDS))),
        static=StubCoordinator(await fetch(client, STATIC_CMDS, STATIC_DEVICE_CMDS, ids)),
    )
    from franklinwh_local.modules import detect_local, ModuleStatus  # noqa: PLC0415
    rt.modules = await detect_local(client, identity)
    if os.environ.get("MODULES") == "none":   # simulate a site without the optional modules
        rt.modules = {m: ModuleStatus(False, "override", "test") for m in rt.modules}
    print("modules:", {m: (s.installed, s.source, s.detail) for m, s in rt.modules.items()})
    entry = types.SimpleNamespace(runtime_data=rt)
    added = []
    await sensor.async_setup_entry(None, entry, added.extend)
    await binary_sensor.async_setup_entry(None, entry, added.extend)
    await switch.async_setup_entry(None, entry, added.extend)
    await select.async_setup_entry(None, entry, added.extend)
    for plat in (number, time, button):
        await plat.async_setup_entry(None, entry, added.extend)

    bad = 0
    uids = set()
    for e in added:
        is_binary = hasattr(e, "is_on") and not hasattr(e, "native_value")
        value = (e.current_option if hasattr(e, 'current_option') else e.is_on if is_binary
                 else 'press' if hasattr(e, 'async_press') else e.native_value)
        unit = getattr(e, "native_unit_of_measurement", None) or ""
        dev = e.device_info.get("name")
        attrs = e.extra_state_attributes
        flag = ""
        if value is None:
            flag = "  <-- NONE"
            bad += 1
        if e.unique_id in uids:
            flag += "  <-- DUPLICATE UID"
            bad += 1
        uids.add(e.unique_id)
        opts = getattr(e, "options", None)
        if opts and value not in opts:
            flag += f"  <-- not in options {opts}"
            bad += 1
        print(f"{'bin' if is_binary else 'sen'} {dev[:26]:26s} {e.entity_description.name:34s} {value!s:>22} {unit:4s}"
              f"{' [attrs ' + str(len(attrs)) + ']' if attrs else ''}{flag}")
    print(f"\n{len(added)} entities, {bad} problems")


if len(sys.argv) < 2:
    sys.exit("usage: live_entities.py <aGate IP>")
asyncio.run(main(sys.argv[1]))
