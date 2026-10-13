"""Async client for the FranklinWH aGate local "Direct Connect" socket (TCP 9000).

Protocol (recovered from the FranklinWH app's `communication_center` package):
- One JSON frame per request on a plain TCP connection; same envelope as the
  cloud sendMqtt payload: cmdType, equipNo, type, timeStamp, snno, len, crc,
  dataArea. The reply's cmdType is the request's + 1 (9999 = rejected).
- The first two fields (`{"cmdType":N,"equipNo":"X",`) are clear text. Every
  following byte i is (b + i + len(header) + key) & 0xFF, where
  key = sum(utf8(equipNo)) & 0xFF.

Every request is a read, except the writes: set_smart_circuit() (cmd 1409,
opt 1), set_mode() (cmd 1727, opt 3) and set_generator() (cmd 1901, opt 1).
"""
from __future__ import annotations

import asyncio
import json
import time
import zlib
from typing import Any

DEFAULT_PORT = 9000
LOGIN_EQUIP_NO = "00000000"  # placeholder serial the app uses before it knows the real one


class AgateError(Exception):
    """Communication with the aGate failed."""


def _key(equip_no: str) -> int:
    return sum(equip_no.encode()) & 0xFF


def encode_frame(obj_json: str, equip_no: str) -> bytes:
    parts = obj_json.split(",")
    header = (parts[0] + "," + parts[1] + ",").encode()
    rest = ",".join(parts[2:]).encode()
    k = _key(equip_no)
    return header + bytes((b + i + len(header) + k) & 0xFF for i, b in enumerate(rest))


def decode_frame(buf: bytes) -> dict[str, Any] | None:
    """Decode one frame from buf, or return None if it is not complete yet."""
    first = buf.find(b",")
    second = buf.find(b",", first + 1) if first >= 0 else -1
    if second < 0:
        return None
    header = buf[: second + 1]
    try:
        equip_no = json.loads(buf[:second].decode() + "}").get("equipNo", "")
    except ValueError:
        return None
    k = _key(equip_no)
    body = bytes((b - i - len(header) - k) & 0xFF for i, b in enumerate(buf[second + 1 :]))
    text = header.decode() + body.decode("utf-8", "replace")
    try:
        obj, _ = json.JSONDecoder().raw_decode(text)
    except ValueError:
        return None
    return obj


class AgateClient:
    """Serialised request/response client; a fresh TCP connection per request."""

    def __init__(self, host: str, port: int = DEFAULT_PORT, timeout: float = 8.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.equip_no: str | None = None
        self._snno = 0
        self._lock = asyncio.Lock()
        # Read-modify-write sequences (several requests) must not interleave.
        self.write_lock = asyncio.Lock()

    def _build(self, cmd: int, data: dict[str, Any], equip_no: str) -> str:
        self._snno = (self._snno % 65535) + 1
        raw = json.dumps(data, separators=(",", ":"))
        blob = raw.encode()
        env = json.dumps(
            {
                "cmdType": cmd,
                "equipNo": equip_no,
                "type": 0,
                "timeStamp": int(time.time()),
                "snno": self._snno,
                "len": len(blob),
                "crc": f"{zlib.crc32(blob):08X}",
                "dataArea": "DATA",
            },
            separators=(",", ":"),
        )
        return env.replace('"DATA"', raw)

    async def request(self, cmd: int, data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send one request and return the reply's dataArea."""
        equip_no = self.equip_no or LOGIN_EQUIP_NO
        frame = encode_frame(self._build(cmd, data or {"opt": 0}, equip_no), equip_no)
        async with self._lock:
            try:
                reply = await asyncio.wait_for(self._exchange(frame), self.timeout)
            except (OSError, asyncio.TimeoutError) as err:
                raise AgateError(f"cmd {cmd}: {type(err).__name__} {err}") from err
        if reply.get("cmdType") != cmd + 1:
            area = reply.get("dataArea") or {}
            raise AgateError(
                f"cmd {cmd}: rejected (cmdType {reply.get('cmdType')}, "
                f"result {area.get('result')}, reason {area.get('reason')})"
            )
        area = reply.get("dataArea")
        if not isinstance(area, dict):
            raise AgateError(f"cmd {cmd}: reply has no dataArea")
        return area

    async def _exchange(self, frame: bytes) -> dict[str, Any]:
        reader, writer = await asyncio.open_connection(self.host, self.port)
        try:
            writer.write(frame)
            await writer.drain()
            buf = b""
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    raise AgateError("connection closed before a full reply")
                buf += chunk
                obj = decode_frame(buf)
                if obj is not None:
                    return obj
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def set_smart_circuit(self, circuit: int, on: bool) -> dict[str, Any]:
        async with self.write_lock:
            return await self._set_smart_circuit(circuit, on)

    async def _set_smart_circuit(self, circuit: int, on: bool) -> dict[str, Any]:
        """Switch smart circuit 1-3 on/off (cmd 1409, opt 1) — the only write.

        Same recipe as the app / cloud library: read the full smart-circuit block,
        mark the target circuit(s) with MsgType=1, set Mode (1 = on) and
        ProLoad = Mode ^ 1, and write the whole block back (partial blocks are
        rejected). If circuits 1 and 2 are merged (SwMerge=1) both are switched.
        Returns the circuits that were targeted.
        """
        if circuit not in (1, 2, 3):
            raise ValueError(f"circuit must be 1, 2 or 3, not {circuit!r}")
        block = {k: v for k, v in (await self.request(1409, {"opt": 0})).items()
                 if k not in ("opt", "result", "reason")}
        if f"Sw{circuit}Mode" not in block:
            raise AgateError(f"smart circuit {circuit} not present in config block")
        targets = [1, 2] if block.get("SwMerge") == 1 and circuit in (1, 2) else [circuit]
        mode = 1 if on else 0
        for i in (1, 2, 3):
            if f"Sw{i}MsgType" in block:
                block[f"Sw{i}MsgType"] = 0
        for i in targets:
            block[f"Sw{i}MsgType"] = 1
            block[f"Sw{i}Mode"] = mode
            block[f"Sw{i}ProLoad"] = mode ^ 1
        reply = await self.request(1409, {"opt": 1, **block})
        if reply.get("result") not in (0, None):
            raise AgateError(f"aGate refused smart circuit change: result {reply.get('result')}, "
                             f"reason {reply.get('reason')}")
        return {"targets": targets, "mode": mode}

    # Generator settings the app writes (cmd 1901). mode: 1 = Auto Schedule, 2 = Manual
    # (verified against the app 2026-10-13). manuSw: 2 = run, 1 = stop (Manual mode only).
    GENERATOR_WRITABLE = (
        "mode", "manuSw", "genStartElec", "genCloseElec",
        "charge1En", "charge1StartTime", "charge1EndTime",
        "charge2En", "charge2StartTime", "charge2EndTime",
        "charge3En", "charge3StartTime", "charge3EndTime",
    )
    # Commands: the aGate acts on them and may not echo them back.
    GENERATOR_COMMANDS = ("manuSw",)

    async def set_generator(self, **changes: Any) -> dict[str, Any]:
        """Change generator settings (cmd 1901, opt 1) by full-block read-modify-write.

        Reads the whole block, applies only `changes`, writes it back, then re-reads and
        raises if any (non-command) field did not take. Returns the re-read block.
        """
        unknown = set(changes) - set(self.GENERATOR_WRITABLE)
        if unknown:
            raise ValueError(f"not writable: {sorted(unknown)}")
        async with self.write_lock:
            block = {k: v for k, v in (await self.request(1901, {"opt": 0})).items()
                     if k not in ("opt", "result", "reason")}
            block.update(changes)
            reply = await self.request(1901, {"opt": 1, **block})
            if reply.get("result") not in (0, None):
                raise AgateError(f"aGate refused generator change: result {reply.get('result')}, "
                                 f"reason {reply.get('reason')}")
            post = await self.request(1901, {"opt": 0})
        bad = {k: (v, post.get(k)) for k, v in changes.items()
               if k not in self.GENERATOR_COMMANDS and post.get(k) != v}
        if bad:
            raise AgateError("generator change not applied: " +
                             ", ".join(f"{k} wanted {w!r} got {g!r}" for k, (w, g) in bad.items()))
        return post

    async def set_mode(self, programme_id: int) -> None:
        """Activate an operating-mode programme (cmd 1727 "modeSettingV2", opt 3 = modify).

        programme_id is a site-specific id from the mode list (cmd 1725). Same request
        the FranklinWH app sends in Direct Connect mode.
        """
        reply = await self.request(1727, {"opt": 3, "current_id": int(programme_id)})
        if reply.get("result") not in (0, None):
            raise AgateError(f"aGate refused mode change: result {reply.get('result')}, "
                             f"reason {reply.get('reason')}")

    async def identify(self) -> dict[str, Any]:
        """Firmware/serial manifest (cmd 1101); also learns the gateway serial."""
        info = await self.request(1101, {"opt": 0})
        serial = info.get("IBG_SN")
        if not serial:
            raise AgateError("aGate did not report a serial number")
        self.equip_no = serial
        return info
