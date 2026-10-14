"""Offline tests for optional-module detection (no aGate, no cloud).

Run:  <venv with homeassistant>/bin/python tests/test_modules.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "custom_components")))

from franklinwh_local import modules  # noqa: E402
from franklinwh_local.client import AgateClient, AgateError  # noqa: E402
from franklinwh_local.cloud import CloudAuthError  # noqa: E402
from franklinwh_local.const import MOD_CIRCUITS, MOD_GENERATOR  # noqa: E402

results = []


def check(name, cond):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name)


class Fake(AgateClient):
    def __init__(self, gen=None, fail=False):
        super().__init__("test")
        self.equip_no = "TESTSERIAL0000000001"
        self.gen, self.fail = gen, fail

    async def request(self, cmd, data=None, **kw):
        if self.fail:
            raise AgateError("unreachable")
        return dict(self.gen)


def installed(r):
    return {m: (s.installed, s.source) for m, s in r.items()}


async def main():
    with_both = {"IBG_SN": "X", "SL_VER": "V12R02B07D00"}
    no_circuits = {"IBG_SN": "X", "SL_VER": ""}

    r = await modules.detect_local(Fake({"genEn": 1, "genStat": 1}), with_both)
    check("local: genEn=1 + SL_VER -> both installed", installed(r) == {MOD_GENERATOR: (True, "local"), MOD_CIRCUITS: (True, "local")})
    r = await modules.detect_local(Fake({"genEn": 0, "genStat": 0}), no_circuits)
    check("local: genEn=0/genStat=0 + blank SL_VER -> neither", not r[MOD_GENERATOR].installed and not r[MOD_CIRCUITS].installed)
    r = await modules.detect_local(Fake({"genEn": 0, "genStat": 3}), {"SL_VER": None})
    check("local: generator running though genEn=0 -> installed", r[MOD_GENERATOR].installed and not r[MOD_CIRCUITS].installed)
    r = await modules.detect_local(Fake(fail=True), with_both)
    check("local: generator block unreadable -> assume installed", r[MOD_GENERATOR].installed)

    modules.async_get_clientsession = lambda hass: None
    gen_off = Fake({"genEn": 0, "genStat": 0})

    async def cloud_both(*a):
        return {3, 4}

    async def cloud_gen_only(*a):
        return {3}

    async def cloud_bad_login(*a):
        raise CloudAuthError("wrong password")

    on = {"cloud_check": True, "cloud_username": "a@b.c", "cloud_password": "pw"}
    modules.accessory_types = cloud_both
    r = await modules.detect_modules(None, gen_off, no_circuits, on)
    check("cloud {3,4} overrides local 'neither'", installed(r) == {MOD_GENERATOR: (True, "cloud"), MOD_CIRCUITS: (True, "cloud")})
    modules.accessory_types = cloud_gen_only
    r = await modules.detect_modules(None, Fake({"genEn": 1, "genStat": 1}), with_both, on)
    check("cloud {3} -> generator yes, circuits no", installed(r) == {MOD_GENERATOR: (True, "cloud"), MOD_CIRCUITS: (False, "cloud")})
    modules.accessory_types = cloud_bad_login
    r = await modules.detect_modules(None, Fake({"genEn": 1, "genStat": 1}), with_both, on)
    check("cloud login fails -> falls back to local, says why",
          r[MOD_GENERATOR] .source == "local" and r[MOD_GENERATOR].installed and "cloud check failed" in r[MOD_GENERATOR].detail)
    r = await modules.detect_modules(None, gen_off, no_circuits, {**on, "cloud_check": False})
    check("cloud check off -> cloud not used", installed(r) == {MOD_GENERATOR: (False, "local"), MOD_CIRCUITS: (False, "local")})

    modules.accessory_types = cloud_both
    r = await modules.detect_modules(None, gen_off, no_circuits,
                                     {**on, "generator_module": "not_installed", "smart_circuits_module": "installed"})
    check("override beats cloud and local", installed(r) == {MOD_GENERATOR: (False, "override"), MOD_CIRCUITS: (True, "override")})
    r = await modules.detect_modules(None, gen_off, no_circuits, {"generator_module": "auto"})
    check("auto with no cloud -> local result", r[MOD_GENERATOR].source == "local")

    # ---- round 3: clean-up scope, V2L polling, entity/key consistency, cloud parsing ----
    from franklinwh_local.cloud import CloudError, accessory_types as real_accessory_types
    from franklinwh_local.const import MODULE_ENTITY_KEYS
    from franklinwh_local.modules import ModuleStatus, skipped_commands, stale_module_unique_ids

    S = "SER"
    regs = [f"{S}_solar_power", f"{S}_reserve_83132", f"{S}_battery_health_apower2", f"{S}_installed_modules",
            f"{S}_generator_mode", f"{S}_generator_period_2_end", f"{S}_v2l_power",
            f"{S}_smart_circuit_1_switch", f"{S}_circuit_voltage", "OTHER_generator_mode"]
    gen_off = {MOD_GENERATOR: ModuleStatus(False, "override", ""), MOD_CIRCUITS: ModuleStatus(True, "local", "")}
    stale = stale_module_unique_ids(S, regs, gen_off)
    check("cleanup: only the absent generator's own entities are removed",
          sorted(stale) == sorted([f"{S}_generator_mode", f"{S}_generator_period_2_end", f"{S}_v2l_power"]))
    both_on = {m: ModuleStatus(True, "local", "") for m in (MOD_GENERATOR, MOD_CIRCUITS)}
    check("cleanup: nothing removed when both modules are installed", stale_module_unique_ids(S, regs, both_on) == [])
    check("cleanup: core entities (reserves, other aPowers) are never candidates",
          not {f"{S}_reserve_83132", f"{S}_battery_health_apower2", f"{S}_solar_power"} & set(
              stale_module_unique_ids(S, regs, {m: ModuleStatus(False, "override", "") for m in both_on})))

    check("V2L: generator only -> 1411 still polled", 1411 not in skipped_commands(True, False))
    check("V2L: circuits only -> 1411 polled, 1901 skipped", skipped_commands(False, True) == {1901})
    check("neither module -> 1901, 1409, 1411 skipped", skipped_commands(False, False) == {1901, 1409, 1411})

    # Build every platform with stub data, with and without modules, and compare to the key map.
    import types as _types
    from franklinwh_local import binary_sensor, button, number, select, sensor, switch, time as time_pf
    from franklinwh_local.coordinator import AgateRuntime

    class Stub:
        data, last_update_success = {}, True

        def async_add_listener(self, *a, **k):
            return lambda: None

    async def build(mods):
        rt = AgateRuntime(client=None, serial=S, identity={}, device_ids=[1], device_serials={1: ""},
                          fast=Stub(), detail=Stub(), config=Stub(), static=Stub(), modules=mods)
        added = []
        entry = _types.SimpleNamespace(runtime_data=rt)
        for plat in (sensor, binary_sensor, switch, select, number, time_pf, button):
            await plat.async_setup_entry(None, entry, added.extend)
        return added

    on_list = await build(both_on)
    off_list = await build({m: ModuleStatus(False, "override", "") for m in both_on})
    keys_on = {e.entity_description.key for e in on_list}
    keys_off = {e.entity_description.key for e in off_list}
    all_module_keys = set().union(*MODULE_ENTITY_KEYS.values())
    check("key map: every module key is a real entity (no typos)", all_module_keys <= keys_on)
    check("key map: no module entity is created when modules are absent", not (all_module_keys & keys_off))
    check("key map: absent modules remove exactly the mapped entities", keys_on - keys_off == all_module_keys)

    # Cloud reply parsing with a fake aiohttp session.
    class Resp:
        def __init__(self, js): self.js = js
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def raise_for_status(self): pass
        async def json(self, content_type=None): return self.js

    class Session:
        def __init__(self, acc): self.acc = acc
        def post(self, *a, **k): return Resp({"code": 200, "result": {"token": "t"}})
        def get(self, *a, **k): return Resp(self.acc)

    async def cloud(acc):
        try:
            return await real_accessory_types(Session(acc), "a", "b", "GW")
        except CloudError as err:
            return err

    check("cloud: non-object reply -> CloudError", isinstance(await cloud(["x"]), CloudError))
    check("cloud: invalid accessoryType -> CloudError",
          isinstance(await cloud({"code": 200, "result": [{"id": "GW", "accessoryType": "abc"}]}), CloudError))
    check("cloud: result not a list -> CloudError", isinstance(await cloud({"code": 200, "result": {"x": 1}}), CloudError))
    check("cloud: empty list is valid (no modules)", await cloud({"code": 200, "result": []}) == set())
    check("cloud: other gateways on the account are ignored",
          await cloud({"code": 200, "result": [{"id": "GW", "accessoryType": 3}, {"id": "OTHER", "accessoryType": 4}]}) == {3})

    async def malformed(*a):
        return await real_accessory_types(Session({"code": 200, "result": [{"accessoryType": None}]}), "a", "b", "GW")

    modules.accessory_types = malformed
    r = await modules.detect_modules(None, Fake({"genEn": 1, "genStat": 1}), with_both, on)
    check("cloud: malformed reply during setup falls back to local (no crash)",
          r[MOD_GENERATOR].source == "local" and "cloud check failed" in r[MOD_GENERATOR].detail)

    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
