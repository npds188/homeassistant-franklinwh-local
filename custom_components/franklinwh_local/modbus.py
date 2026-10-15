"""Read-only SunSpec Modbus TCP client for the aGate (port 502).

Used as the fallback / alternative data source and for lifetime energy counters.
Only function 3 (read holding registers) is ever sent: the aGate's SunSpec map also
exposes writable grid-control models (703 enter service, 704 AC controls, 715 DER
control), which this integration never touches.

Point offsets come from the official SunSpec model definitions
(https://github.com/sunspec/models); model start addresses are discovered by walking
the model chain from 40002, and scale factors are read from the device.
"""
from __future__ import annotations

import asyncio
import struct
from typing import Any

DEFAULT_MODBUS_PORT = 502
DEFAULT_MODBUS_UNIT = 1
SUNSPEC_BASE = 40000
END_MODEL = 0xFFFF

# Offsets from each model's start (ID at 0, L at 1), per the SunSpec JSON definitions.
M701 = {"St": 3, "InvSt": 4, "ConnSt": 5, "W": 10, "A": 14, "LLV": 15, "Hz": 17, "TotWhInj": 19,
        "TotWhAbs": 23, "TmpAmb": 35, "TmpCab": 36, "TmpSw": 39,
        "A_SF": 113, "V_SF": 114, "Hz_SF": 115, "W_SF": 116, "TotWh_SF": 120, "Tmp_SF": 122}
M701_LEN = 123
M713 = {"WHRtg": 2, "WHAvail": 3, "SoC": 4, "SoH": 5, "Sta": 6, "WH_SF": 7, "Pct_SF": 8}
M713_LEN = 9
M714 = {"DCW": 6, "DCWhInj": 7, "DCWhAbs": 11, "DCW_SF": 17, "DCWH_SF": 18}
M714_LEN = 19
# FranklinWH vendor registers (outside the SunSpec chain)
VENDOR_BASE, VENDOR_LEN = 15500, 14   # 15506 home W, 15507 mode, 15508 reserve %, 15510-11 solar Wh (u32)
MODE_TYPE = {1: 3, 2: 2, 3: 1}        # vendor mode (1 backup, 2 self, 3 TOU) -> scheduling_type


class ModbusError(Exception):
    """Modbus communication failed or the reply was invalid."""


# SunSpec "not implemented / unavailable" values (as in sunspec/pysunspec suns.py):
# int16 0x8000, uint16/enum16 0xFFFF, uint32 0xFFFFFFFF, uint64 all-ones, sunssf 0x8000.
# Every reading is decoded through these helpers so a sentinel becomes None, never a
# measurement. The energy counters used here (701 TotWhInj/TotWhAbs, 714 DCWhInj/DCWhAbs)
# are uint64, so zero is a valid reading.
def _s16(v: int) -> int:
    return v - 0x10000 if v >= 0x8000 else v


def _uint(regs: list[int]) -> int:
    v = 0
    for r in regs:
        v = (v << 16) | r
    return v


def _int16(v: int) -> int | None:
    return None if v == 0x8000 else _s16(v)


def _uint16(v: int) -> int | None:
    return None if v == 0xFFFF else v


def _uint32(regs: list[int]) -> int | None:
    v = _uint(regs)
    return None if v == 0xFFFFFFFF else v


def _uint64(regs: list[int]) -> int | None:
    v = _uint(regs)
    return None if v == 0xFFFFFFFFFFFFFFFF else v


def _sf(v: int) -> int | None:
    sf = _s16(v)
    return None if sf == -32768 or not -10 <= sf <= 10 else sf


def _scaled(value: int | None, sf: int | None) -> float | None:
    if value is None or sf is None or sf == -32768:
        return None
    return round(value * 10 ** sf, max(0, -sf)) if sf < 0 else value * 10 ** sf


class AgateModbus:
    """Minimal async Modbus TCP client: one connection per call, requests serialised."""

    def __init__(self, host: str, port: int = DEFAULT_MODBUS_PORT, unit: int = DEFAULT_MODBUS_UNIT,
                 timeout: float = 5.0) -> None:
        self.host, self.port, self.unit, self.timeout = host, port, unit, timeout
        self._tid = 0
        self._lock = asyncio.Lock()
        self.models: dict[int, int] = {}   # model id -> start address

    async def _read_once(self, reader, writer, addr: int, count: int) -> list[int]:
        """One function-3 request. Every reply field is validated before it's used, and any
        malformed frame is a ModbusError (never IndexError / struct.error)."""
        self._tid = (self._tid + 1) & 0xFFFF
        pdu = struct.pack(">BHH", 3, addr, count)
        writer.write(struct.pack(">HHHB", self._tid, 0, len(pdu) + 1, self.unit) + pdu)
        await writer.drain()
        tid, proto, length, unit = struct.unpack(">HHHB", await reader.readexactly(7))
        # MBAP length counts the unit id + PDU; a Modbus PDU is at most 253 bytes.
        if proto != 0 or not 2 <= length <= 254:
            raise ModbusError(f"read {addr}+{count}: bad MBAP header (protocol {proto}, length {length})")
        body = await reader.readexactly(length - 1)
        if tid != self._tid:
            raise ModbusError(f"reply for transaction {tid}, expected {self._tid}")
        if unit != self.unit:
            raise ModbusError(f"reply from unit {unit}, expected {self.unit}")
        func = body[0]
        if func == 0x83:
            if len(body) != 2:
                raise ModbusError(f"read {addr}+{count}: malformed exception reply")
            raise ModbusError(f"read {addr}+{count}: exception code {body[1]}")
        if func != 3 or len(body) != 2 + 2 * count or body[1] != 2 * count:
            raise ModbusError(f"read {addr}+{count}: malformed reply (function {func}, {len(body)} bytes)")
        return list(struct.unpack(f">{count}H", body[2:]))

    async def read_many(self, ranges: list[tuple[int, int]]) -> list[list[int]]:
        """Read several (address, count) ranges over one connection."""
        async with self._lock:
            try:
                return await asyncio.wait_for(self._read_ranges(ranges), self.timeout)
            except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError) as err:
                raise ModbusError(f"{type(err).__name__} {err}") from err

    async def _read_ranges(self, ranges):
        reader, writer = await asyncio.open_connection(self.host, self.port)
        try:
            out = []
            for addr, count in ranges:
                regs: list[int] = []
                while count:                      # max 125 registers per request
                    n = min(count, 120)
                    regs += await self._read_once(reader, writer, addr, n)
                    addr += n
                    count -= n
                out.append(regs)
            return out
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def discover(self) -> dict[str, Any]:
        """Check the SunSpec marker, map model addresses, return the Common model identity."""
        (marker,) = await self.read_many([(SUNSPEC_BASE, 2)])
        if marker != [0x5375, 0x6E53]:   # "SunS"
            raise ModbusError("no SunSpec marker at 40000")
        addr, models = SUNSPEC_BASE + 2, {}
        for _ in range(64):
            (hdr,) = await self.read_many([(addr, 2)])
            mid, length = hdr
            if mid == END_MODEL:
                break
            models[mid] = addr
            addr += 2 + length
        missing = [m for m in (1, 701, 713, 714) if m not in models]
        if missing:
            raise ModbusError(f"SunSpec models missing: {missing}")
        self.models = models
        (common,) = await self.read_many([(models[1] + 2, 64)])
        text = lambda regs: b"".join(struct.pack(">H", r) for r in regs).split(b"\x00")[0].decode(errors="replace").strip()  # noqa: E731
        return {"manufacturer": text(common[0:16]), "model": text(common[16:32]),
                "version": text(common[40:48]), "serial": text(common[48:64])}

    async def snapshot(self) -> dict[str, Any]:
        """Core readings plus lifetime counters, decoded with the device's scale factors."""
        if not self.models:
            await self.discover()
        m = self.models
        a, b, c, v = await self.read_many([(m[701], M701_LEN), (m[713], M713_LEN), (m[714], M714_LEN),
                                           (VENDOR_BASE, VENDOR_LEN)])
        sf = lambda regs, key: _sf(regs[key])  # noqa: E731
        u64 = lambda regs, key: _uint64(regs[key:key + 4])  # noqa: E731
        w = _scaled(_int16(a[M701["W"]]), sf(a, M701["W_SF"]))
        dcw = _scaled(_int16(c[M714["DCW"]]), sf(c, M714["DCW_SF"]))
        home = _uint16(v[6])
        reserve, solar_wh = _uint16(v[8]), _uint32(v[10:12])
        out = {
            "conn_state": _uint16(a[M701["ConnSt"]]), "operating_state": _uint16(a[M701["St"]]),
            "inverter_state": _uint16(a[M701["InvSt"]]),
            "grid_power": w,                                            # + import / - export (aGate convention)
            "grid_current": _scaled(_int16(a[M701["A"]]), sf(a, M701["A_SF"])),
            "grid_voltage": _scaled(_uint16(a[M701["LLV"]]), sf(a, M701["V_SF"])),
            "grid_frequency": _scaled(_uint32(a[M701["Hz"]:M701["Hz"] + 2]), sf(a, M701["Hz_SF"])),
            "lifetime_wh_injected": _scaled(u64(a, M701["TotWhInj"]), sf(a, M701["TotWh_SF"])),
            "lifetime_wh_absorbed": _scaled(u64(a, M701["TotWhAbs"]), sf(a, M701["TotWh_SF"])),
            "temp_ambient": _scaled(_int16(a[M701["TmpAmb"]]), sf(a, M701["Tmp_SF"])),
            "temp_cabinet": _scaled(_int16(a[M701["TmpCab"]]), sf(a, M701["Tmp_SF"])),
            "temp_switch": _scaled(_int16(a[M701["TmpSw"]]), sf(a, M701["Tmp_SF"])),
            "capacity_wh": _scaled(_uint16(b[M713["WHRtg"]]), sf(b, M713["WH_SF"])),
            "available_wh": _scaled(_uint16(b[M713["WHAvail"]]), sf(b, M713["WH_SF"])),
            "soc": _scaled(_uint16(b[M713["SoC"]]), sf(b, M713["Pct_SF"])),
            "soh": _scaled(_uint16(b[M713["SoH"]]), sf(b, M713["Pct_SF"])),
            "battery_power": dcw,                                       # + discharge / - charge
            "battery_wh_discharged": _scaled(u64(c, M714["DCWhInj"]), sf(c, M714["DCWH_SF"])),
            "battery_wh_charged": _scaled(u64(c, M714["DCWhAbs"]), sf(c, M714["DCWH_SF"])),
            "home_power": float(home) if home is not None else None,
            "mode_type": MODE_TYPE.get(v[7]),
            "reserve": float(reserve) if reserve is not None else None,
            "solar_wh": float(solar_wh) if solar_wh is not None else None,
        }
        # No usable live solar register: derive it from the power balance, never negative.
        if None not in (home, w, dcw):
            out["solar_power"] = max(0.0, home - w - dcw)
        return out
