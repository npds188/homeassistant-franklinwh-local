"""Offline tests for device names with one vs several aGates.

Run:  <venv with homeassistant>/bin/python tests/test_naming.py
"""
import os
import sys
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "custom_components")))

import franklinwh_local as integ  # noqa: E402
from franklinwh_local.coordinator import AgateRuntime  # noqa: E402
from franklinwh_local.entity import agate_device, apower_device  # noqa: E402

results = []


def check(name, got, want):
    results.append(got == want)
    print(("PASS " if got == want else "FAIL ") + f"{name}: {got!r}" + ("" if got == want else f" (want {want!r})"))


def runtime(serial, label=""):
    return AgateRuntime(client=None, serial=serial, identity={}, device_ids=[1], device_serials={1: ""},
                        fast=None, detail=None, config=None, static=None, label=label)


one = runtime("10060006A02F24170129")
check("one aGate: plain aGate name", agate_device(one)["name"], "FranklinWH aGate (local)")
check("one aGate: plain aPower name", apower_device(one, 1)["name"], "FranklinWH aPower 1 (local)")
two = runtime("10060006A02F24170129", label="170129")
check("several: aGate name has short serial", agate_device(two)["name"], "FranklinWH aGate 170129 (local)")
check("several: aPower name has its aGate", apower_device(two, 1)["name"], "FranklinWH aPower 1 – aGate 170129 (local)")
check("identifiers unchanged by label", agate_device(two)["identifiers"], agate_device(one)["identifiers"])


class FakeEntries:
    def __init__(self, entries):
        self.entries, self.reloaded = entries, []

    def async_entries(self, domain, include_ignore=True):
        return [e for e in self.entries if include_ignore or e.source != "ignore"]

    def async_schedule_reload(self, entry_id):
        self.reloaded.append(entry_id)


def entry(eid, label=None, source="user"):
    e = types.SimpleNamespace(entry_id=eid, source=source)
    if label is not None:
        e.runtime_data = runtime("X" * 14 + eid[-6:].rjust(6, "0"), label)
    return e


# Second aGate added: the first (still unlabelled) is reloaded to pick up its label.
a, b = entry("A", label=""), entry("B", label="000000")
hass = types.SimpleNamespace(config_entries=FakeEntries([a, b]))
integ._relabel_others(hass, b)
check("2nd added: first aGate reloaded", hass.config_entries.reloaded, ["A"])
# Already labelled: no reload loop.
a2 = entry("A", label="00000A")
hass = types.SimpleNamespace(config_entries=FakeEntries([a2, b]))
integ._relabel_others(hass, b)
check("both labelled: nothing reloaded", hass.config_entries.reloaded, [])
# Back to one (removed entry already gone from the list): the remaining one drops its label.
hass = types.SimpleNamespace(config_entries=FakeEntries([a2]))
integ._relabel_others(hass, b)
check("back to one: remaining aGate reloaded", hass.config_entries.reloaded, ["A"])
# An ignored discovery doesn't count as an aGate.
hass = types.SimpleNamespace(config_entries=FakeEntries([entry("A", label=""), entry("I", source="ignore")]))
integ._relabel_others(hass, None)
check("ignored entry not counted (still one aGate)", hass.config_entries.reloaded, [])
# A second aGate that isn't loaded yet is skipped; the loaded first one is relabelled.
hass = types.SimpleNamespace(config_entries=FakeEntries([entry("A", label=""), entry("C")]))
integ._relabel_others(hass, None)
check("unloaded entry skipped, loaded one relabelled", hass.config_entries.reloaded, ["A"])

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
