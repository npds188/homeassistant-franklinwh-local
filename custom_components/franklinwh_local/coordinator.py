"""Polling coordinators for the FranklinWH Local integration.

Four tiers share one client (requests are serialised, ~50 ms each):
  fast    every 5 s   power flow + grid electrical
  detail  every 60 s  battery cells, inverter, generator, circuits, faults
  config  every 15 min modes/reserves, TOU schedule, circuit/solar/grid settings
  static  daily       install profile, per-device firmware
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import AgateClient, AgateError
from .modbus import AgateModbus, ModbusError
from .const import (
    CMD_DEVICE_CHECK,
    CMD_MODE_CONFIG,
    CMD_MODE_LIST,
    CMD_MODBUS,
    CMD_POWER_FLOW,
    CMD_RUN_STATUS,
    CONFIG_CMDS,
    DETAIL_CMDS,
    DETAIL_DEVICE_CMDS,
    DOMAIN,
    FAST_CMDS,
    MODE_LIST_REQUEST,
    SOURCE_DIRECT,
    SOURCE_FALLBACK,
    SOURCE_MODBUS,
    MODBUS_ONLY_KEYS,
    STATIC_CMDS,
    STATIC_DEVICE_CMDS,
)

from .modules import skipped_commands

_LOGGER = logging.getLogger(__name__)

# Coordinator data: {cmd: dataArea} for whole-gateway reads and
# {(cmd, device_id): dataArea} for per-aPower reads.
AgateData = dict[Any, dict[str, Any]]


def _is_blank_power_flow(reply: dict[str, Any]) -> bool:
    """True for a template reply: no SOC and every power/energy field zero."""
    fields = ("soc", "p_sun", "p_uti", "p_load", "p_fhp", "kwh_sun", "kwh_load", "kwh_uti_in", "kwh_uti_out")
    return all(not reply.get(f) for f in fields)


def programme_ids(programmes: list[dict[str, Any]]) -> set[int]:
    return {int(p["id"]) for p in programmes if isinstance(p, dict) and "id" in p}


async def resolve_mode(client: AgateClient, reply: dict[str, Any], ids: set[int]) -> dict[str, Any]:
    """Make the power flow's `mode` a programme id.

    Off-grid the aGate reports a status code there (7) instead of the programme; the mode
    config (1403) still has the programme as runingMode. Only read when needed; the raw
    value is kept as mode_raw.
    """
    mode = reply.get("mode")
    if not ids or mode is None or mode in ids:
        return reply
    try:
        running = int((await client.request(CMD_MODE_CONFIG, {"opt": 0}))["runingMode"])
    except (AgateError, KeyError, TypeError, ValueError):
        return reply
    return {**reply, "mode": running, "mode_raw": mode} if running in ids else reply


def to_fast_data(snap: dict[str, Any], previous: AgateData | None,
                 programmes: list[dict[str, Any]]) -> AgateData:
    """Shape a Modbus snapshot like the Direct Connect fast tier (1301 / 1707 replies).

    Only the fields Modbus has are filled; entities for the rest read as unknown while
    Modbus is the source. The operating mode becomes a programme id via the mode list
    (keeping the last id if it's the same mode type), and mode_type is kept as a fallback.
    """
    solar = snap.get("solar_power")
    flow: dict[str, Any] = {
        "source": "modbus", "p_uti": snap.get("grid_power"), "p_load": snap.get("home_power"),
        "p_fhp": snap.get("battery_power"), "p_sun": solar, "soc": snap.get("soc"),
        "t_amb": snap.get("temp_ambient"), "mode_type": snap.get("mode_type"),
    }
    same = [p for p in programmes if isinstance(p, dict) and p.get("scheduling_type") == snap.get("mode_type")]
    ids = [int(p["id"]) for p in same if "id" in p]
    prev_mode = ((previous or {}).get(CMD_POWER_FLOW) or {}).get("mode")
    if prev_mode in ids:
        flow["mode"] = prev_mode
    elif ids:
        flow["mode"] = next((int(p["id"]) for p in same if str(p.get("name", "")).strip().lower() != "default"), ids[0])
    volts = snap.get("grid_voltage")
    run = {
        "source": "modbus", "gridLineVol": round(volts * 10) if volts is not None else None,
        "gridFreq": snap.get("grid_frequency"), "gridCurr1": snap.get("grid_current"),
        # ConnSt 1 connected / 0 disconnected; anything else (incl. unavailable) stays unknown
        "mainRelay1Status": {1: 1, 0: 0}.get(snap.get("conn_state")),
    }
    return {CMD_POWER_FLOW: flow, CMD_RUN_STATUS: run}


class AgateCoordinator(DataUpdateCoordinator[AgateData]):
    """Polls a fixed set of read commands."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: AgateClient,
        tier: str,
        interval: int,
        cmds: tuple[int, ...],
        device_cmds: tuple[int, ...] = (),
        device_ids: list[int] | None = None,
        partial_ok: bool = False,
        modbus: AgateModbus | None = None,
        source: str = SOURCE_DIRECT,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {tier}",
            update_interval=timedelta(seconds=interval),
            always_update=True,
        )
        self.client = client
        self.cmds = cmds
        self.device_cmds = device_cmds
        self.device_ids = device_ids or [1]
        # Slow tiers tolerate individual commands failing (see _async_update_data).
        self.partial_ok = partial_ok
        # A command that fails keeps its last good reply for this long, then its
        # entities go unavailable; a poll where every command fails is an UpdateFailed.
        self.grace = 2 * interval
        self._last_ok: dict[Any, float] = {}
        # Fast tier only: where the data comes from (see const SOURCE_*).
        self.modbus = modbus
        self.source = source
        self.active_source = SOURCE_MODBUS if source == SOURCE_MODBUS else SOURCE_DIRECT
        self.fallback_count = 0
        self.last_fallback: str | None = None
        self.programmes: Any = lambda: []   # set by setup: current mode list (for Modbus)

    @callback
    def async_publish_reply(self, key: Any, reply: dict[str, Any]) -> None:
        """Publish a reply obtained outside a poll (e.g. a write's read-back) as fresh data."""
        self._last_ok[key] = time.monotonic()
        data = dict(self.data or {})
        data[key] = reply
        self.async_set_updated_data(data)

    async def _async_update_data(self) -> AgateData:
        if self.source == SOURCE_MODBUS:
            return await self._from_modbus()
        try:
            data = await self._poll_direct()
        except UpdateFailed as err:
            if self.source != SOURCE_FALLBACK or self.modbus is None:
                raise
            data = await self._from_modbus(err)
            if self.active_source != SOURCE_MODBUS:
                _LOGGER.warning("Direct Connect failed (%s); using Modbus until it recovers", err)
                self.fallback_count += 1
                self.last_fallback = time.strftime("%Y-%m-%d %H:%M:%S")
            self.active_source = SOURCE_MODBUS
            return data
        if self.active_source == SOURCE_MODBUS and self.source == SOURCE_FALLBACK:
            _LOGGER.info("Direct Connect recovered; back from Modbus")
        self.active_source = SOURCE_DIRECT
        return data

    async def _from_modbus(self, cause: Exception | None = None) -> AgateData:
        assert self.modbus is not None
        try:
            snap = await self.modbus.snapshot()
        except ModbusError as err:
            raise UpdateFailed(f"Modbus failed ({err})" + (f" after Direct Connect failed ({cause})" if cause else "")) from err
        return to_fast_data(snap, self.data, self.programmes())

    async def _poll_direct(self) -> AgateData:
        data: AgateData = {}
        errors: list[str] = []
        requests: list[tuple[Any, int, dict[str, Any]]] = [
            (cmd, cmd, MODE_LIST_REQUEST if cmd == CMD_MODE_LIST else {"opt": 0})
            for cmd in self.cmds
        ]
        requests += [
            ((cmd, dev), cmd, {"opt": 0, "id": dev})
            for cmd in self.device_cmds
            for dev in self.device_ids
        ]
        now = time.monotonic()
        succeeded = 0
        for key, cmd, payload in requests:
            try:
                reply = await self.client.request(cmd, payload)
                if cmd == CMD_POWER_FLOW and _is_blank_power_flow(reply):
                    # A zero-filled reply would look like a midnight reset to the
                    # energy dashboard's daily counters and double-count the day.
                    raise AgateError("power flow reply is all zeros (ignored)")
                if cmd == CMD_POWER_FLOW:
                    reply = await resolve_mode(self.client, reply, programme_ids(self.programmes()))
                data[key] = reply
                self._last_ok[key] = now
                succeeded += 1
            except AgateError as err:
                if not self.partial_ok:
                    raise UpdateFailed(str(err)) from err
                errors.append(str(err))
                # Bridge a brief failure with the last good reply; once it is older
                # than the grace period, drop it so those entities go unavailable.
                if self.data and key in self.data and now - self._last_ok.get(key, 0) <= self.grace:
                    data[key] = self.data[key]
        if errors:
            if not succeeded:
                raise UpdateFailed("; ".join(errors))
            _LOGGER.debug("%s: partial update, %s", self.name, "; ".join(errors))
        return data


LIFETIME_FIELDS = ("lifetime_wh_injected", "lifetime_wh_absorbed", "battery_wh_discharged",
                   "battery_wh_charged", "solar_wh")


class CounterGuard:
    """Lifetime counters only go up. A lower reading is held back for one poll (a glitch,
    e.g. a one-off 0, would otherwise look like a meter reset to the Energy dashboard and
    double-count). The next poll decides:
      * back at or above the old total  -> it was a glitch; carry on from there
      * still below the old total, and not below the held-back reading -> a genuine reset
        (the counter is counting up from its new start); accepted
      * lower again -> held back again, as a new candidate
    """

    def __init__(self, fields: tuple[str, ...] = LIFETIME_FIELDS) -> None:
        self.fields = fields
        self.last: dict[str, float] = {}
        self.pending: dict[str, float] = {}

    def apply(self, snap: dict[str, Any]) -> dict[str, Any]:
        out = dict(snap)
        for key in self.fields:
            value, last = out.get(key), self.last.get(key)
            if value is None:
                continue
            pending = self.pending.get(key)
            if last is not None and value < last and (pending is None or value < pending):
                self.pending[key] = value
                _LOGGER.debug("%s went down (%s -> %s); holding back one poll", key, last, value)
                out[key] = None
                continue
            if pending is not None and value < last:
                _LOGGER.info("%s reset confirmed (%s -> %s)", key, last, value)
            self.pending.pop(key, None)
            self.last[key] = value
        return out


class ModbusCoordinator(DataUpdateCoordinator[AgateData]):
    """Polls the Modbus snapshot (lifetime counters, capacity, temperatures)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, modbus: AgateModbus, interval: int) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=f"{DOMAIN} modbus",
                         update_interval=timedelta(seconds=interval), always_update=True)
        self.client = modbus
        self.guard = CounterGuard()

    async def _async_update_data(self) -> AgateData:
        try:
            return {CMD_MODBUS: self.guard.apply(await self.client.snapshot())}
        except ModbusError as err:
            raise UpdateFailed(f"Modbus: {err}") from err


@dataclass
class AgateRuntime:
    """Everything the platforms need, stored on entry.runtime_data."""

    client: AgateClient
    serial: str
    identity: dict[str, Any]
    device_ids: list[int]
    device_serials: dict[int, str]
    fast: AgateCoordinator
    detail: AgateCoordinator
    config: AgateCoordinator
    static: AgateCoordinator
    coordinators: list[AgateCoordinator] = field(default_factory=list)
    agate_device_id: str | None = None
    # Set when more than one aGate is configured: short serial added to device names
    label: str = ""
    # Optional modules -> ModuleStatus (see modules.py)
    modules: dict[str, Any] = field(default_factory=dict)
    # Configured data source (const SOURCE_*) and the Modbus coordinator, if Modbus is in use
    source: str = SOURCE_DIRECT
    modbus: "ModbusCoordinator | None" = None

    def shows(self, key: str, module: str | None = None) -> bool:
        """Should an entity with this description key / module be created?"""
        if self.source == SOURCE_MODBUS:
            return key in MODBUS_ONLY_KEYS
        return self.has(module)

    def has(self, module: str | None) -> bool:
        """True if the entity's module is installed (or it belongs to no module).

        With Modbus as the only source nothing module-related can be read or controlled.
        """
        if module and self.source == SOURCE_MODBUS:
            return False
        status = self.modules.get(module) if module else None
        return status is None or status.installed


async def discover_devices(client: AgateClient) -> dict[int, str]:
    """aPower device ids -> serials, from the device check list (1105)."""
    try:
        # 1105 answers result 1 / reason 4 while carrying the device list.
        info = await client.request(CMD_DEVICE_CHECK, {"opt": 0}, allow_error_result=True)
    except AgateError as err:
        _LOGGER.warning("Could not list aPower devices (%s); assuming one", err)
        return {1: ""}
    found = {
        int(d["id"]): str(d.get("devSN", ""))
        for d in info.get("devMap") or []
        if isinstance(d, dict) and "id" in d
    }
    return found or {1: ""}


def build_coordinators(
    hass: HomeAssistant,
    entry: ConfigEntry,
    client: AgateClient,
    device_ids: list[int],
    fast_s: int,
    detail_s: int,
    config_s: int,
    static_s: int,
    generator: bool = True,
    circuits: bool = True,
    modbus: AgateModbus | None = None,
    source: str = SOURCE_DIRECT,
) -> tuple[AgateCoordinator, AgateCoordinator, AgateCoordinator, AgateCoordinator]:
    # Don't poll data for modules that aren't installed.
    skip = skipped_commands(generator, circuits)
    fast = AgateCoordinator(hass, entry, client, "fast", fast_s, FAST_CMDS, modbus=modbus, source=source)
    detail = AgateCoordinator(
        hass, entry, client, "detail", detail_s, tuple(c for c in DETAIL_CMDS if c not in skip),
        DETAIL_DEVICE_CMDS, device_ids, partial_ok=True
    )
    config = AgateCoordinator(
        hass, entry, client, "config", config_s,
        (CMD_MODE_LIST, *(c for c in CONFIG_CMDS if c not in skip)), partial_ok=True
    )
    static = AgateCoordinator(
        hass, entry, client, "static", static_s, STATIC_CMDS, STATIC_DEVICE_CMDS, device_ids, partial_ok=True
    )
    # Modbus has the operating mode type only; name it via the mode list when we have one.
    fast.programmes = lambda: ((config.data or {}).get(CMD_MODE_LIST) or {}).get("list") or []
    return fast, detail, config, static
