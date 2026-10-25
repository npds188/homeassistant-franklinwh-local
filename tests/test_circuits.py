"""Offline tests for smart circuit metering (cmd 1411) on both meter layouts.

Run:  <venv with homeassistant>/bin/python tests/test_circuits.py
"""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "custom_components")))

from franklinwh_local import sensor  # noqa: E402
from franklinwh_local.coordinator import AgateRuntime  # noqa: E402
from franklinwh_local.sensor import au_circuit_layout, circuit_blocks, circuit_reading  # noqa: E402

results = []


def check(name, cond):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name)


# Real 1411 reply from an NZ aGate (SyHdVersion 102, protocolVer V1.11.01), 2026-10-25, while the
# app showed 2.95 kW / 211.0 kWh for smart circuit 1. SW1* stays 0 on this hardware.
AU_REPLY = {"opt": 0, "result": 0, "Sw1Volt": 2394, "Sw2Volt": 0, "CarSWCurr": 124, "CarSWPower": 2945,
            "CarSWExpEnergy": 211161, "CarSWImpEnergy": 0, "SW1Curr": 0, "SW2Curr": 0, "SW1ExpPower": 0,
            "SW2ExpPower": 1, "SW1ExpEnergy": 0, "SW2ExpEnergy": 0}
AU_IDENT = {"SyHdVersion": 102, "protocolVer": "V1.11.01"}
# Other hardware: circuits in SW1*/SW2*, CarSW* is the V2L port.
US_REPLY = {"Sw1Volt": 2401, "Sw2Volt": 2399, "SW1Curr": 52, "SW2Curr": 30, "SW1ExpPower": 1250,
            "SW2ExpPower": 700, "SW1ExpEnergy": 5000, "SW2ExpEnergy": 2500, "CarSWCurr": 0, "CarSWPower": 0,
            "CarSWExpEnergy": 900}
US_IDENT = {"SyHdVersion": 101, "protocolVer": "V1.11.01"}


class Stub:
    last_update_success = True

    def __init__(self, data=None):
        self.data = data or {}

    def async_add_listener(self, *a, **k):
        return lambda: None


def runtime(identity, reply, merged=False):
    return AgateRuntime(client=None, serial="S", identity=identity, device_ids=[1], device_serials={1: ""},
                        fast=Stub(), detail=Stub({1411: reply}), config=Stub({1409: {"SwMerge": int(merged)}}),
                        static=Stub())


async def entities(rt):
    added = []
    await sensor.async_setup_entry(None, types.SimpleNamespace(runtime_data=rt), added.extend)
    return {e.entity_description.key: e for e in added}


async def main():
    # Layout rule, as in the app (SupportFeatures.supportAustraliaVersion).
    check("layout: hw 102, V1.11.01 -> AU", au_circuit_layout(AU_IDENT))
    check("layout: hw 102 as text -> AU", au_circuit_layout({"SyHdVersion": "102", "protocolVer": "V1.09.08"}))
    check("layout: hw 102, protocol older than V1.09.08 -> standard",
          not au_circuit_layout({"SyHdVersion": 102, "protocolVer": "V1.09.07"}))
    check("layout: V1.10.0 compares numerically (not as text)",
          au_circuit_layout({"SyHdVersion": 102, "protocolVer": "V1.10.0"}))
    check("layout: other hardware -> standard", not au_circuit_layout(US_IDENT))
    check("layout: identity missing (e.g. Modbus) -> standard", not au_circuit_layout({}))
    check("layout: unreadable protocol -> standard",
          not au_circuit_layout({"SyHdVersion": 102, "protocolVer": "beta"}))

    check("blocks: AU circuit 1 -> CarSW", circuit_blocks(1, True, False) == ("CarSW",))
    check("blocks: AU circuit 1 ignores merge", circuit_blocks(1, True, True) == ("CarSW",))
    check("blocks: standard circuit 1 -> SW1", circuit_blocks(1, False, False) == ("SW1",))
    check("blocks: standard merged circuit 1 -> SW1 + SW2", circuit_blocks(1, False, True) == ("SW1", "SW2"))
    check("blocks: circuit 2 -> SW2 on both", circuit_blocks(2, True, False) == circuit_blocks(2, False, False)
          == ("SW2",))
    check("reading: missing field -> None", circuit_reading({"SW1Curr": 3}, "power", ("SW1",)) is None)

    # AU layout end to end: matches the app (2.95 kW, 211.0 kWh) and the circuit's ~239 V.
    au = await entities(runtime(AU_IDENT, AU_REPLY))
    check("AU: circuit 1 power = CarSWPower 2945 W", au["circuit_1_power"].native_value == 2945)
    check("AU: circuit 1 current = 12.4 A", au["circuit_1_current"].native_value == 12.4)
    check("AU: circuit 1 lifetime energy = 211.161 kWh", au["circuit_1_energy"].native_value == 211.161)
    check("AU: voltage = 239.4 V", au["circuit_voltage"].native_value == 239.4)
    check("AU: circuit 2 power from SW2", au["circuit_2_power"].native_value == 1)
    check("AU: no V2L sensor (CarSW is circuit 1)", "v2l_power" not in au)
    check("AU: attributes name the meter fields",
          au["circuit_1_power"].extra_state_attributes == {"meter_fields": ["CarSWPower"]})
    check("AU: energy is total_increasing kWh",
          au["circuit_1_energy"].state_class == "total_increasing"
          and au["circuit_1_energy"].native_unit_of_measurement == "kWh")
    check("circuit 2 current/energy disabled by default, power enabled",
          not au["circuit_2_current"].entity_registry_enabled_default
          and not au["circuit_2_energy"].entity_registry_enabled_default
          and au["circuit_2_power"].entity_registry_enabled_default
          and au["circuit_1_energy"].entity_registry_enabled_default)
    check("AU: existing power entity keeps its unique id", au["circuit_1_power"].unique_id == "S_circuit_1_power")

    # Standard layout.
    us = await entities(runtime(US_IDENT, US_REPLY))
    check("standard: circuit 1 power = SW1ExpPower", us["circuit_1_power"].native_value == 1250)
    check("standard: circuit 1 current = 5.2 A", us["circuit_1_current"].native_value == 5.2)
    check("standard: circuit 1 energy = 5.0 kWh", us["circuit_1_energy"].native_value == 5.0)
    check("standard: circuit 2 power = SW2ExpPower", us["circuit_2_power"].native_value == 700)
    check("standard: circuit 2 current = 3.0 A", us["circuit_2_current"].native_value == 3.0)
    check("standard: V2L sensor still made, reads CarSW", us["v2l_power"].native_value == 0)
    merged = await entities(runtime(US_IDENT, US_REPLY, merged=True))
    check("standard merged: circuit 1 power = SW1 + SW2", merged["circuit_1_power"].native_value == 1950)
    check("standard merged: circuit 1 energy = 7.5 kWh", merged["circuit_1_energy"].native_value == 7.5)

    # Lifetime energy: a one-off 0 is held back (not a meter reset); a sustained lower value is accepted.
    rt = runtime(AU_IDENT, AU_REPLY)
    energy = (await entities(rt))["circuit_1_energy"]
    energy.async_write_ha_state = lambda: None
    rt.detail.data = {1411: {**AU_REPLY, "CarSWExpEnergy": 0}}
    energy._handle_coordinator_update()
    check("energy guard: one-off 0 held back", energy.native_value is None)
    rt.detail.data = {1411: {**AU_REPLY, "CarSWExpEnergy": 211170}}
    energy._handle_coordinator_update()
    check("energy guard: recovers to 211.17 kWh", energy.native_value == 211.17)
    for _ in range(2):
        rt.detail.data = {1411: {**AU_REPLY, "CarSWExpEnergy": 10}}
        energy._handle_coordinator_update()
    check("energy guard: sustained lower value accepted as reset", energy.native_value == 0.01)

    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
