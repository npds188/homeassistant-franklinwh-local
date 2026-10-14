"""Offline regression tests for the PR #1 review findings (no aGate needed).

A fake aGate answers by command; every frame the client sends is decoded and recorded,
so the tests can assert that nothing is written when a check refuses.

Run:  <venv with homeassistant>/bin/python tests/test_review_fixes.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "custom_components")))

from homeassistant.exceptions import ServiceValidationError  # noqa: E402
from homeassistant.helpers.update_coordinator import UpdateFailed  # noqa: E402

from franklinwh_local.client import AgateClient, AgateError, decode_frame  # noqa: E402
from franklinwh_local.coordinator import AgateCoordinator  # noqa: E402
from franklinwh_local.generator import RUN, check_manual, check_periods, check_soc_range  # noqa: E402

GEN = {"opt": 0, "result": 0, "reason": 0, "genEn": 1, "mode": 2, "genStat": 1, "genStartElec": 20,
       "genCloseElec": 60, "manuSw": 0,
       "charge1En": 0, "charge1StartTime": "02:30", "charge1EndTime": "03:00",
       "charge2En": 1, "charge2StartTime": "02:00", "charge2EndTime": "04:00",
       "charge3En": 0, "charge3StartTime": "00:00", "charge3EndTime": "00:00"}
CIRCUITS = {"opt": 0, "result": 0, "SwMerge": 0, "Sw1Name": "A", "Sw1MsgType": 0, "Sw1Mode": 1, "Sw1ProLoad": 1}


class FakeAgate(AgateClient):
    """Answers from `replies[(cmd, opt)]`; records decoded frames in `sent`."""

    def __init__(self, replies):
        super().__init__("test")
        self.equip_no = "TESTSERIAL0000000001"
        self.replies = replies
        self.sent = []

    async def _exchange(self, frame):
        obj = decode_frame(frame)
        self.sent.append(obj)
        cmd, opt = obj["cmdType"], obj["dataArea"].get("opt")
        area = self.replies.get((cmd, opt), {"result": 0})
        return {"cmdType": cmd + 1, "equipNo": self.equip_no, "dataArea": dict(area)}

    def writes(self):
        return [f for f in self.sent if f["dataArea"].get("opt") in (1, 3)]


results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)


async def raises(coro, exc):
    try:
        await coro
    except exc as err:
        return err
    return None


async def main():
    # --- Finding 2: error results are rejected centrally -------------------------------
    a = FakeAgate({(1301, 0): {"result": 1, "reason": 4}})
    check("2a: request() raises on dataArea.result=1", await raises(a.request(1301, {"opt": 0}), AgateError))
    a = FakeAgate({(1101, 0): {"result": 1, "reason": -2, "IBG_SN": "X"}})
    r = await a.request(1101, {"opt": 0}, allow_error_result=True)
    check("2b: allow_error_result passes 1101's result=1 manifest", r.get("IBG_SN") == "X")
    a = FakeAgate({(1901, 0): {"result": 1, "reason": 4}})
    err = await raises(a.set_generator({"genStartElec": 30}), AgateError)
    check("2c: generator RMW refuses an error read, no write sent", err and not a.writes())
    a = FakeAgate({(1901, 0): {"result": 0, "mode": 2}})
    err = await raises(a.set_generator({"genStartElec": 30}), AgateError)
    check("2d: generator RMW refuses an incomplete block, no write sent", err and not a.writes())
    a = FakeAgate({(1409, 0): {"result": 0, "SwMerge": 0}})
    err = await raises(a.set_smart_circuit(1, False), AgateError)
    check("2e: smart-circuit RMW refuses an incomplete block, no write sent", err and not a.writes())
    a = FakeAgate({(1409, 0): CIRCUITS})
    await a.set_smart_circuit(1, False)
    check("2f: smart-circuit write still works with a full block", len(a.writes()) == 1)

    # --- Finding 1: checks run on the fresh merged block --------------------------------
    # ChatGPT's case: cached stop 90 %, actual stop 60 %, request start 70 %.
    a = FakeAgate({(1901, 0): GEN})
    err = await raises(a.set_generator({"genStartElec": 70}, check_soc_range), ServiceValidationError)
    check("1a: start 70 refused against fresh stop 60, no write sent", err and not a.writes())
    a = FakeAgate({(1901, 0): GEN, (1901, 1): {"result": 0}})
    # read-back after the write returns the same fresh block, so start stays 20 -> mismatch is fine
    # here; what matters is that a valid change is sent.
    await raises(a.set_generator({"genStartElec": 50}, check_soc_range), AgateError)
    check("1b: valid start 50 (< 60) is written", len(a.writes()) == 1)
    a = FakeAgate({(1901, 0): GEN})
    err = await raises(a.set_generator({"charge1En": 1}, check_periods), ServiceValidationError)
    check("1c: enabling period 1 refused - overlaps fresh period 2, no write", err and not a.writes())
    a = FakeAgate({(1901, 0): {**GEN, "mode": 1}})
    err = await raises(a.set_generator({"manuSw": RUN}, check_manual(RUN)), ServiceValidationError)
    check("1d: Run refused when the fresh block is Auto, no write sent", err and not a.writes())

    # --- Finding 3: failed slow-tier polls ----------------------------------------------
    def coord(client, data, last_ok, grace=120):
        c = AgateCoordinator.__new__(AgateCoordinator)
        c.client, c.cmds, c.device_cmds, c.device_ids = client, (1901, 1709), (), [1]
        c.partial_ok, c.grace, c._last_ok, c.data, c.name = True, grace, last_ok, data, "test"
        return c

    class Dead(FakeAgate):
        async def _exchange(self, frame):
            raise OSError("unreachable")

    old = {1901: {"result": 0, "x": 1}, 1709: {"result": 0, "y": 1}}
    import time as _t
    now = _t.monotonic()
    c = coord(Dead({}), old, {1901: now, 1709: now})
    err = await raises(c._async_update_data(), UpdateFailed)
    check("3a: every command failing -> UpdateFailed (entities unavailable)", err)

    class Half(FakeAgate):
        async def _exchange(self, frame):
            if decode_frame(frame)["cmdType"] == 1709:
                raise OSError("timeout")
            return await super()._exchange(frame)

    c = coord(Half({(1901, 0): {"result": 0, "x": 2}}), old, {1901: now, 1709: now})
    d = await c._async_update_data()
    check("3b: brief failure keeps last good value within grace", d.get(1709) == old[1709] and d[1901]["x"] == 2)
    c = coord(Half({(1901, 0): {"result": 0, "x": 2}}), old, {1901: now, 1709: now - 1000})
    d = await c._async_update_data()
    check("3c: failure older than grace drops the value (unavailable)", 1709 not in d and 1901 in d)

    print(f"\n{sum(ok for _, ok in results)}/{len(results)} passed")
    sys.exit(0 if all(ok for _, ok in results) else 1)


asyncio.run(main())
