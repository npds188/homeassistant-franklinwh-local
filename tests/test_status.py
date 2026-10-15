"""Offline tests for the Status sensor summary (replays the 15 Oct outage / generator run).

Run:  <venv with homeassistant>/bin/python tests/test_status.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "custom_components")))

from franklinwh_local.sensor import STATUS_ICONS, agate_status  # noqa: E402

results = []


def check(name, got, want):
    results.append(got == want)
    print(("PASS " if got == want else "FAIL ") + f"{name}: {got!r}" + ("" if got == want else f" (want {want!r})"))


GRID_UP, GRID_DOWN = {"gridLineVol": 2412, "genVoltage": 1}, {"gridLineVol": 2, "genVoltage": 1}
flow = lambda **kw: {"p_fhp": 0, "p_uti": 0, "p_gen": 0, "genStat": 1, **kw}  # noqa: E731

check("on grid, battery charging", agate_status(flow(p_fhp=-3000), GRID_UP), "Charging battery")
check("on grid, battery discharging", agate_status(flow(p_fhp=1100), GRID_UP), "Battery powering home")
check("on grid, exporting", agate_status(flow(p_uti=-2000), GRID_UP), "Exporting to grid")
check("on grid, importing", agate_status(flow(p_uti=1182), GRID_UP), "Importing from grid")
check("on grid, small flows = idle", agate_status(flow(p_fhp=20, p_uti=-8), GRID_UP), "Idle")
check("08:47 grid down, on battery", agate_status(flow(p_fhp=975), GRID_DOWN), "Grid down – on battery")
check("grid down, solar covering load", agate_status(flow(p_fhp=-400, p_sun=1500), GRID_DOWN), "Grid down – on solar")
check("grid down, battery charging, solar unknown (Modbus)",
      agate_status({"p_fhp": -500, "p_uti": 0, "p_sun": None}, GRID_DOWN), "Grid down")
check("09:03 Run pressed, cranking", agate_status(flow(p_fhp=1104, genStat=2), GRID_DOWN), "Generator starting")
check("generator up to voltage, relay open",
      agate_status(flow(p_fhp=1100, genStat=2), {**GRID_DOWN, "genVoltage": 2326}), "Generator warming up")
check("09:04 relay closed, on load", agate_status(flow(p_fhp=-28, p_gen=1313, genStat=3), GRID_DOWN), "Generator on load")
check("running with no load", agate_status(flow(genStat=3), GRID_DOWN), "Generator running (no load)")
check("09:21 Stop pressed", agate_status(flow(p_fhp=2298, genStat=4), GRID_DOWN), "Generator cooling down")
check("09:22 back to standby", agate_status(flow(p_fhp=1205, genStat=1), GRID_DOWN), "Grid down – on battery")
check("generator error", agate_status(flow(genStat=5), GRID_UP), "Generator error")
check("off-grid setting wins over grid voltage", agate_status(flow(p_fhp=500), GRID_UP, offgrid_state=1), "Off-grid")
check("no generator module: genStat ignored", agate_status(flow(p_fhp=500, genStat=3), GRID_DOWN, generator=False),
      "Grid down – on battery")
check("Modbus data (no genStat/genVoltage)", agate_status({"p_fhp": -1100, "p_uti": -250}, {"gridLineVol": 2412}),
      "Charging battery")
check("no data", agate_status({}, {}), None)
check("every state has an icon", all(s in STATUS_ICONS for s in (
    "Generator starting", "Generator warming up", "Generator on load", "Generator cooling down",
    "Grid down – on battery", "Charging battery", "Idle")), True)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
