"""Offline tests for Storm Hedge backup detection (power-flow mode code 6, seen 6-7 Nov 2026).

Run:  <venv with homeassistant>/bin/python tests/test_storm.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "custom_components")))

from types import SimpleNamespace  # noqa: E402

from franklinwh_local.binary_sensor import BINARY_SENSORS, StormBackupSensor  # noqa: E402
from franklinwh_local.const import CMD_MODE_LIST  # noqa: E402
from franklinwh_local.coordinator import (  # noqa: E402
    MODE_CODE_OFFGRID, MODE_CODE_STORM, AgateRuntime, mode_code, resolve_mode, storm_backup,
)
from franklinwh_local.sensor import OPERATING_MODE_OPTIONS, STORM_BACKUP, OperatingModeSensor  # noqa: E402

results = []


def check(name, got, want):
    results.append(got == want)
    print(("PASS " if got == want else "FAIL ") + f"{name}: {got!r}" + ("" if got == want else f" (want {want!r})"))


IDS = {79680, 77244, 83132, 2718}
PROGRAMMES = [{"id": 79680, "scheduling_type": 2}, {"id": 77244, "scheduling_type": 3},
              {"id": 83132, "scheduling_type": 1}, {"id": 2718, "scheduling_type": 1}]


def rt_with(programmes):
    """A runtime with just the mode list (config tier) filled in."""
    rt = object.__new__(AgateRuntime)
    rt.config = SimpleNamespace(data={CMD_MODE_LIST: {"list": programmes}})
    return rt


def entities(reply, programmes=PROGRAMMES):
    """(operating mode state, its storm attribute, binary sensor state) for one 1301 reply."""
    rt = rt_with(programmes)

    class Op(OperatingModeSensor):
        reply = property(lambda self: reply)

    class Storm(StormBackupSensor):
        reply = property(lambda self: reply)

    op, st = object.__new__(Op), object.__new__(Storm)
    op.rt = st.rt = rt
    op.entity_description = SimpleNamespace(value_fn=lambda d: d["mode"])
    attrs = op.extra_state_attributes if reply is not None else {}
    return op.native_value, attrs.get("storm_hedge_backup"), st.is_on


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
check("entities during storm", entities(during), (STORM_BACKUP, True, True))
check("entities after storm", entities(after), ("Time of Use", False, False))

# The mode list came back empty once during the storm: resolve_mode leaves mode = 6.
unresolved = asyncio.run(resolve_mode(FakeAgate({}), {"mode": 6}, set()))
check("empty mode list: code still in mode", mode_code(unresolved), MODE_CODE_STORM)
check("empty mode list: entities show storm", entities(unresolved, []), (STORM_BACKUP, True, True))

check("off-grid code is not storm", mode_code({"mode": 83132, "mode_raw": 7}, IDS), MODE_CODE_OFFGRID)
check("off-grid: storm off", storm_backup({"mode": 77244, "mode_raw": 7}, IDS), False)
check("unknown code ignored", mode_code({"mode": 5}), None)
check("programme id equal to a code is a programme", mode_code({"mode": 6}, {6}), None)
check("Modbus reply: no code", mode_code({"source": "modbus", "mode": 6}), None)
# R8-1: a real programme numbered 6 is a programme for every entity.
check("programme id 6: entities agree it's not a storm",
      entities({"mode": 6}, [{"id": 6, "scheduling_type": 1, "name": "TOU"}]), ("Time of Use", False, False))
# R8-2: unknown stays unknown in the attribute and the binary sensor alike.
check("Modbus fallback: storm unknown everywhere",
      entities({"source": "modbus", "mode_type": 1}), ("Time of Use", None, None))
check("Direct reply without mode: storm unknown", entities({"soc": 90})[1:], (None, None))
check("no reply: binary unknown", entities(None)[2], None)
check("fallback then recovery", [entities(r)[2] for r in (during, {"source": "modbus", "mode_type": 1}, after)],
      [True, None, False])
check("storm sensor not in the generic table", any(d.key == "storm_hedge_backup" for d in BINARY_SENSORS), False)
check("garbage mode", mode_code({"mode": "x"}), None)
check("no reply", mode_code(None), None)
check("storm state is an enum option", STORM_BACKUP in OPERATING_MODE_OPTIONS, True)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
