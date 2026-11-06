"""Offline tests for Storm Hedge backup detection (power-flow mode code 6, seen 6-7 Nov 2026).

Run:  <venv with homeassistant>/bin/python tests/test_storm.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "custom_components")))

from franklinwh_local.binary_sensor import BINARY_SENSORS  # noqa: E402
from franklinwh_local.coordinator import MODE_CODE_OFFGRID, MODE_CODE_STORM, mode_code, resolve_mode  # noqa: E402
from franklinwh_local.sensor import OPERATING_MODE_OPTIONS, STORM_BACKUP  # noqa: E402

results = []


def check(name, got, want):
    results.append(got == want)
    print(("PASS " if got == want else "FAIL ") + f"{name}: {got!r}" + ("" if got == want else f" (want {want!r})"))


IDS = {79680, 77244, 83132, 2718}
storm = next(d for d in BINARY_SENSORS if d.key == "storm_hedge_backup")


class FakeAgate:
    def __init__(self, reply):
        self.reply = reply

    async def request(self, cmd, payload):
        return self.reply


# Live replies from 7 Nov: during the storm 1301 mode = 6 and 1403 runingMode = 83132 (Time of Use).
during = asyncio.run(resolve_mode(FakeAgate({"runingMode": 83132, "modeChoose": 6}), {"mode": 6, "soc": 99.5}, IDS))
after = asyncio.run(resolve_mode(FakeAgate({}), {"mode": 83132, "soc": 99.5}, IDS))
check("storm reply resolved to the programme", (during["mode"], during["mode_raw"]), (83132, 6))
check("mode_code during storm", mode_code(during, IDS), MODE_CODE_STORM)
check("mode_code after storm", mode_code(after, IDS), None)
check("binary sensor during storm", storm.value_fn(during), True)
check("binary sensor after storm", storm.value_fn(after), False)

# The mode list came back empty once during the storm: resolve_mode leaves mode = 6.
unresolved = asyncio.run(resolve_mode(FakeAgate({}), {"mode": 6}, set()))
check("empty mode list: code still in mode", mode_code(unresolved), MODE_CODE_STORM)
check("empty mode list: binary sensor on", storm.value_fn(unresolved), True)

check("off-grid code is not storm", mode_code({"mode": 83132, "mode_raw": 7}, IDS), MODE_CODE_OFFGRID)
check("off-grid: binary sensor off", storm.value_fn({"mode": 77244, "mode_raw": 7}), False)
check("unknown code ignored", mode_code({"mode": 5}), None)
check("programme id equal to a code is a programme", mode_code({"mode": 6}, {6}), None)
check("Modbus reply: no code", mode_code({"source": "modbus", "mode": 6}), None)
check("Modbus reply: binary sensor unknown", storm.value_fn({"source": "modbus", "mode_type": 1}), None)
check("garbage mode", mode_code({"mode": "x"}), None)
check("no reply", mode_code(None), None)
check("storm state is an enum option", STORM_BACKUP in OPERATING_MODE_OPTIONS, True)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
