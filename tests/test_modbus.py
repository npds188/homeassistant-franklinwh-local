"""Offline tests for the Modbus source: decoding, Direct Connect -> Modbus fallback, and the
setup-time Modbus probe (no aGate needed).

Run:  <venv with homeassistant>/bin/python tests/test_modbus.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "custom_components")))

from homeassistant.helpers.update_coordinator import UpdateFailed  # noqa: E402

from franklinwh_local import config_flow, modbus as mb  # noqa: E402
from franklinwh_local.client import AgateError  # noqa: E402
from franklinwh_local.const import CMD_POWER_FLOW, CMD_RUN_STATUS  # noqa: E402
from franklinwh_local.coordinator import AgateCoordinator, to_fast_data  # noqa: E402
from franklinwh_local.sensor import MODBUS_SENSORS  # noqa: E402

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)


def u16(v):
    return v & 0xFFFF


def regs_701(w=-250):
    a = [0] * mb.M701_LEN
    a[mb.M701["ConnSt"]] = 1
    a[mb.M701["W"]] = u16(w)                          # default: exporting 250 W
    a[mb.M701["A"]], a[mb.M701["A_SF"]] = 52, u16(-1)
    a[mb.M701["LLV"]], a[mb.M701["V_SF"]] = 2412, u16(-1)
    a[mb.M701["Hz"]:mb.M701["Hz"] + 2] = [0, 50012]
    a[mb.M701["Hz_SF"]] = u16(-3)
    a[mb.M701["TotWhInj"]:mb.M701["TotWhInj"] + 4] = [0, 0, 64, 598]    # 4194902 Wh
    a[mb.M701["TotWhAbs"]:mb.M701["TotWhAbs"] + 4] = [0, 0, 190, 21841]
    a[mb.M701["TmpAmb"]], a[mb.M701["TmpCab"]], a[mb.M701["Tmp_SF"]] = 215, 302, u16(-1)
    return a


def regs_713():
    b = [0] * mb.M713_LEN
    b[mb.M713["WHRtg"]], b[mb.M713["WHAvail"]], b[mb.M713["WH_SF"]] = 1360, 1224, 1
    b[mb.M713["SoC"]], b[mb.M713["SoH"]], b[mb.M713["Pct_SF"]] = 900, 937, u16(-1)
    return b


def regs_714(dcw=-1100):
    c = [0] * mb.M714_LEN
    c[mb.M714["DCW"]] = u16(dcw)                       # default: charging 1100 W
    c[mb.M714["DCWhInj"] + 2:mb.M714["DCWhInj"] + 4] = [176, 29012]
    return c


def regs_vendor(home=1500, solar=2800):
    v = [0] * mb.VENDOR_LEN
    v[2], v[6], v[7], v[8], v[10], v[11] = solar, home, 2, 20, 322, 59190
    return v


def regs_502(solar=2800, w_sf=0):
    r = [0] * mb.M502_LEN
    r[mb.M502["W_SF"]], r[mb.M502["OutPw"]] = u16(w_sf), u16(solar)
    return r


class FakeModbus(mb.AgateModbus):
    def __init__(self, home=1500, fail=False, w=-250, dcw=-1100, solar=2800, model_502=True, vendor_solar=None):
        super().__init__("test")
        self.w, self.dcw, self.solar = w, dcw, solar
        self.vendor_solar = solar if vendor_solar is None else vendor_solar
        self.models = {1: 40002, 701: 40070, 713: 41033, 714: 41042, **({502: 41096} if model_502 else {})}
        self.home, self.fail, self.reads, self.ranges = home, fail, 0, []

    async def read_many(self, ranges):
        self.reads += 1
        self.ranges = ranges
        if self.fail:
            raise mb.ModbusError("unreachable")
        out = [regs_701(self.w), regs_713(), regs_714(self.dcw), regs_vendor(self.home, self.vendor_solar)]
        if len(ranges) > 4:
            out.append(regs_502(self.solar))
        return out


class DeadDirect:
    async def request(self, cmd, payload=None, **_):
        raise AgateError("Direct Connect unreachable")


class GoodDirect:
    async def request(self, cmd, payload=None, **_):
        if cmd == CMD_POWER_FLOW:
            return {"result": 0, "soc": 77, "p_uti": 5, "p_load": 600, "p_fhp": 595, "p_sun": 0, "mode": 79680}
        return {"result": 0, "gridLineVol": 2400}


def coord(client, source, modbus):
    c = AgateCoordinator.__new__(AgateCoordinator)
    c.client, c.cmds, c.device_cmds, c.device_ids = client, (CMD_POWER_FLOW, CMD_RUN_STATUS), (), [1]
    c.partial_ok, c.grace, c._last_ok, c.data, c.name = False, 10, {}, None, "fast"
    c.modbus, c.source = modbus, source
    c.active_source = "modbus" if source == "modbus" else "direct"
    c.fallback_count, c.last_fallback = 0, None
    c.programmes = lambda: [{"id": 79680, "name": "Self", "scheduling_type": 2},
                            {"id": 2718, "name": "Default", "scheduling_type": 1}]
    return c


async def raises(coro, exc):
    try:
        await coro
    except exc as err:
        return err
    return None


async def main():
    # --- decoding -----------------------------------------------------------------------
    snap = await FakeModbus().snapshot()
    check("decode: grid power signed (+import/-export)", snap["grid_power"] == -250)
    check("decode: battery power signed (+discharge/-charge)", snap["battery_power"] == -1100)
    check("decode: voltage / current / frequency scaled",
          round(snap["grid_voltage"], 1) == 241.2 and round(snap["grid_current"], 1) == 5.2
          and round(snap["grid_frequency"], 3) == 50.012)
    check("decode: SOC and SoH via Pct_SF", round(snap["soc"], 1) == 90.0 and round(snap["soh"], 1) == 93.7)
    check("decode: capacity via WH_SF", snap["capacity_wh"] == 13600 and snap["available_wh"] == 12240)
    check("decode: u64 lifetime counter", snap["lifetime_wh_injected"] == 4194902)
    check("decode: vendor solar lifetime u32", snap["solar_wh"] == 322 * 65536 + 59190)
    # --- solar is the measured register, never derived ------------------------------------
    check("solar: from SunSpec 502 OutPw", snap["solar_power"] == 2800)
    f = FakeModbus(); await f.snapshot()
    check("solar: model 502 range requested", (41096, mb.M502_LEN) in f.ranges)
    unbalanced = await FakeModbus(home=1100, w=30, dcw=1030, solar=0).snapshot()   # balance leaves +40 W
    check("solar: night with a 40 W imbalance reads 0 (no phantom solar)", unbalanced["solar_power"] == 0)
    gen = await FakeModbus(home=1300, w=0, dcw=-30, solar=0).snapshot()   # generator supplying 1330 W
    check("solar: generator output isn't counted as solar", gen["solar_power"] == 0)
    no502 = await FakeModbus(model_502=False, vendor_solar=4900).snapshot()
    check("solar: without model 502, vendor register 15502 is used", no502["solar_power"] == 4900)
    check("solar: 502 W_SF applied", mb._scaled(mb._int16(u16(54)), mb._sf(u16(2))) == 5400)
    class NoSolar(FakeModbus):
        async def read_many(self, ranges):
            out = await super().read_many(ranges)
            out[4][mb.M502["OutPw"]] = 0x8000
            return out
    ns = await NoSolar().snapshot()
    check("solar: 502 OutPw unavailable -> unknown (not derived)", ns["solar_power"] is None)
    check("decode: unimplemented scale factor -> None", mb._scaled(5, -32768) is None)

    life = {d.key: d.value_fn(snap) for d in MODBUS_SENSORS}
    check("lifetime: import = absorbed (TotWhAbs), export = injected (TotWhInj)",
          life["grid_import_total"] == round(snap["lifetime_wh_absorbed"] / 1000, 3)
          and life["grid_export_total"] == round(snap["lifetime_wh_injected"] / 1000, 3))

    # --- SunSpec unavailable values never become measurements (review P1) ----------------
    class Sentinel(FakeModbus):
        async def read_many(self, ranges):
            a, b, c, v = regs_701(), regs_713(), regs_714(), regs_vendor()
            a[mb.M701["W"]] = 0x8000                                            # int16 n/a
            a[mb.M701["LLV"]] = 0xFFFF                                          # uint16 n/a
            a[mb.M701["Hz"]:mb.M701["Hz"] + 2] = [0xFFFF, 0xFFFF]               # uint32 n/a
            a[mb.M701["TotWhInj"]:mb.M701["TotWhInj"] + 4] = [0xFFFF] * 4       # all-ones counter
            a[mb.M701["TotWhAbs"]:mb.M701["TotWhAbs"] + 4] = [0, 0, 0, 0]       # uint64 zero: valid
            a[mb.M701["Tmp_SF"]] = 0x8000                                       # scale factor n/a
            b[mb.M713["SoC"]] = 0xFFFF
            c[mb.M714["DCWhAbs"]:mb.M714["DCWhAbs"] + 4] = [0xFFFF] * 4
            v[6] = 0xFFFF                                                       # vendor home power n/a
            return [a, b, c, v]
    bad = await Sentinel().snapshot()
    check("sentinel: int16 0x8000 grid power -> None (not -32768 W)", bad["grid_power"] is None)
    check("sentinel: uint16 0xFFFF SOC -> None (not 65535 %)", bad["soc"] is None)
    check("sentinel: uint16 voltage / uint32 frequency -> None",
          bad["grid_voltage"] is None and bad["grid_frequency"] is None)
    check("sentinel: all-ones uint64 lifetime counters -> None",
          bad["lifetime_wh_injected"] is None and bad["battery_wh_charged"] is None)
    check("R2-1: a zero uint64 lifetime counter is a valid 0, not unknown", bad["lifetime_wh_absorbed"] == 0)
    check("R2-1: _uint64 keeps 0, rejects all-ones",
          mb._uint64([0, 0, 0, 0]) == 0 and mb._uint64([0xFFFF] * 4) is None)
    check("sentinel: unavailable scale factor -> None", bad["temp_ambient"] is None and bad["temp_cabinet"] is None)
    check("sentinel: solar register unaffected by other unavailable inputs", bad["solar_power"] == 2800)
    check("sentinel: good readings in the same reply still decode", bad["battery_power"] == -1100)
    life_bad = {d.key: d.value_fn(bad) for d in MODBUS_SENSORS}
    check("sentinel: lifetime sensors read unknown, not huge totals",
          life_bad["grid_export_total"] is None and life_bad["grid_import_total"] == 0)

    # --- malformed frames are ModbusError (review P2) --------------------------------------
    class Writer:
        def write(self, data):
            pass

        async def drain(self):
            pass

    async def reply(frame, count=2):
        m = mb.AgateModbus("test")
        r = asyncio.StreamReader()
        r.feed_data(frame)
        r.feed_eof()
        m._tid = 0          # next request uses transaction id 1
        try:
            return await asyncio.wait_for(m._read_once(r, Writer(), 40000, count), 1)
        except mb.ModbusError as err:
            return err
        except Exception as err:  # noqa: BLE001 - the point is to catch anything else
            return f"UNEXPECTED {type(err).__name__}: {err}"

    import struct as _st
    mbap = lambda length, tid=1, proto=0, unit=1: _st.pack(">HHHB", tid, proto, length, unit)  # noqa: E731
    good = mbap(7) + bytes([3, 4]) + _st.pack(">HH", 0x5375, 0x6E53)
    check("frame: well-formed reply decodes", await reply(good) == [0x5375, 0x6E53])
    for name, frame in [
        ("one-byte body (function only)", mbap(2) + bytes([3])),
        ("byte count right, registers missing", mbap(3) + bytes([3, 4])),
        ("byte count disagrees with body", mbap(7) + bytes([3, 2]) + bytes(4)),
        ("short exception reply", mbap(2) + bytes([0x83])),
        ("exception reply", mbap(3) + bytes([0x83, 2])),
        ("wrong function", mbap(7) + bytes([4, 4]) + bytes(4)),
        ("MBAP length 0", mbap(0)),
        ("MBAP length too big", mbap(300) + bytes(299)),
        ("wrong protocol id", mbap(7, proto=1) + bytes([3, 4]) + bytes(4)),
        ("wrong unit id", mbap(7, unit=9) + bytes([3, 4]) + bytes(4)),
        ("wrong transaction id", mbap(7, tid=5) + bytes([3, 4]) + bytes(4)),
    ]:
        got = await reply(frame)
        check(f"frame: {name} -> ModbusError", isinstance(got, mb.ModbusError))
    got = await reply(mbap(7) + bytes([3]))          # truncated stream
    check("frame: truncated stream -> IncompleteReadError (read_many maps it to ModbusError)",
          isinstance(got, str) and "IncompleteReadError" in got)

    # --- shaping into the fast tier -----------------------------------------------------
    d = to_fast_data(snap, None, coord(None, "modbus", None).programmes())
    flow, run = d[CMD_POWER_FLOW], d[CMD_RUN_STATUS]
    check("shape: power flow fields", flow["p_uti"] == -250 and flow["p_fhp"] == -1100 and flow["soc"] == 90)
    check("shape: grid volts in 1707 units (x10)", run["gridLineVol"] == 2412)
    check("shape: grid relay closed from ConnSt", run["mainRelay1Status"] == 1)
    check("shape: vendor mode 2 (self) -> programme 79680", flow["mode"] == 79680 and flow["mode_type"] == 2)
    check("shape: no daily counters invented", "kwh_uti_in" not in flow)
    off = {**snap, "conn_state": 0}
    check("shape: measured solar passes through with the grid disconnected",
          to_fast_data(off, None, [])[CMD_POWER_FLOW]["p_sun"] == 2800)
    for conn, want in ((1, 1), (0, 0), (None, None)):
        got = to_fast_data({**snap, "conn_state": conn}, None, [])[CMD_RUN_STATUS]["mainRelay1Status"]
        check(f"R2-2: ConnSt {conn} -> relay status {want}", got == want)
    from franklinwh_local.binary_sensor import BINARY_SENSORS  # noqa: PLC0415
    relay = next(d for d in BINARY_SENSORS if d.key == "grid_relay")
    unknown = to_fast_data({**snap, "conn_state": None}, None, [])[CMD_RUN_STATUS]
    try:
        relay_state = relay.value_fn(unknown)
    except (TypeError, KeyError, ValueError):
        relay_state = None       # AgateEntity._value() turns these into unknown
    check("R2-2: Grid relay entity reads unknown (not open) when ConnSt is unavailable", relay_state is None)

    # --- lifetime counters never go backwards on a glitch ---------------------------
    from franklinwh_local.coordinator import CounterGuard  # noqa: PLC0415
    g = CounterGuard()
    g.apply({"lifetime_wh_absorbed": 1000.0})
    check("guard: one-off drop (glitch 0) is held back", g.apply({"lifetime_wh_absorbed": 0.0})["lifetime_wh_absorbed"] is None)
    check("guard: recovery to the old value continues", g.apply({"lifetime_wh_absorbed": 1001.0})["lifetime_wh_absorbed"] == 1001.0)
    g.apply({"lifetime_wh_absorbed": 5.0})
    check("guard: a drop confirmed on the next poll is accepted (genuine reset)",
          g.apply({"lifetime_wh_absorbed": 5.0})["lifetime_wh_absorbed"] == 5.0)
    g = CounterGuard()
    seq = [g.apply({"solar_wh": v})["solar_wh"] for v in (1000.0, 0.0, 5.0, 10.0, 15.0)]
    check("R3-1: reset then counting up is accepted from the second post-reset reading",
          seq == [1000.0, None, 5.0, 10.0, 15.0])
    g = CounterGuard()
    seq = [g.apply({"solar_wh": v})["solar_wh"] for v in (1000.0, 0.0, 1002.0, 1003.0)]
    check("R3-1: one-off drop then back in the old range is still a glitch", seq == [1000.0, None, 1002.0, 1003.0])
    g = CounterGuard()
    seq = [g.apply({"solar_wh": v})["solar_wh"] for v in (1000.0, 500.0, 0.0, 3.0)]
    check("R3-1: a lower second drop restarts the hold, then counting up confirms",
          seq == [1000.0, None, None, 3.0])
    g = CounterGuard()
    seq = [g.apply({"solar_wh": v})["solar_wh"] for v in (1000.0, 0.0, None, 7.0)]
    check("R3-1: an unknown reading in between doesn't lose the pending reset", seq == [1000.0, None, None, 7.0])
    check("guard: first reading of 0 is accepted", CounterGuard().apply({"solar_wh": 0.0})["solar_wh"] == 0.0)
    check("guard: unknown passes through", CounterGuard().apply({"solar_wh": None})["solar_wh"] is None)
    d2 = to_fast_data(snap, None, [])
    check("shape: without a mode list only mode_type is set", "mode" not in d2[CMD_POWER_FLOW])

    # --- fallback -----------------------------------------------------------------------
    fake = FakeModbus()
    c = coord(DeadDirect(), "direct_modbus_fallback", fake)
    d = await c._async_update_data()
    check("fallback: Direct Connect down -> Modbus data", d[CMD_POWER_FLOW]["source"] == "modbus")
    check("fallback: active source and count recorded", c.active_source == "modbus" and c.fallback_count == 1)
    c.data = d
    await c._async_update_data()
    check("fallback: staying on Modbus doesn't recount", c.fallback_count == 1)
    c.client = GoodDirect()
    d = await c._async_update_data()
    check("fallback: back to Direct Connect when it recovers",
          c.active_source == "direct" and d[CMD_POWER_FLOW]["soc"] == 77)
    c = coord(GoodDirect(), "direct_modbus_fallback", fake)
    before = fake.reads
    await c._async_update_data()
    check("fallback: Modbus not read while Direct Connect works", fake.reads == before)
    c = coord(DeadDirect(), "direct_modbus_fallback", FakeModbus(fail=True))
    err = await raises(c._async_update_data(), UpdateFailed)
    check("fallback: both down -> UpdateFailed naming both", err and "Direct Connect" in str(err))
    c = coord(DeadDirect(), "direct", FakeModbus())
    check("direct only: no fallback", await raises(c._async_update_data(), UpdateFailed))
    c = coord(GoodDirect(), "modbus", FakeModbus())
    d = await c._async_update_data()
    check("modbus only: Direct Connect never used", d[CMD_POWER_FLOW]["source"] == "modbus")

    # --- setup probe --------------------------------------------------------------------
    orig = mb.AgateModbus.discover

    async def ok(self):
        return {"serial": "SER1"}

    async def down(self):
        raise mb.ModbusError("ConnectionRefusedError")

    mb.AgateModbus.discover = ok
    check("probe: available with matching serial", await config_flow.probe_modbus("h", 502, "SER1") is None)
    check("probe: different serial is refused", "different serial" in (await config_flow.probe_modbus("h", 502, "SER2") or ""))
    mb.AgateModbus.discover = down
    check("probe: unreachable reported", "not available" in (await config_flow.probe_modbus("h", 502, "SER1") or ""))
    mb.AgateModbus.discover = orig

    print(f"\n{sum(ok for _, ok in results)}/{len(results)} passed")
    sys.exit(0 if all(ok for _, ok in results) else 1)


asyncio.run(main())
