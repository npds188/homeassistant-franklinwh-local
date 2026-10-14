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

    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
