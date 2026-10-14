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
from .const import (
    CMD_CIRCUIT_METER,
    CMD_DEVICE_CHECK,
    CMD_GENERATOR,
    CMD_MODE_LIST,
    CMD_POWER_FLOW,
    CMD_SMART_CIRCUITS,
    CONFIG_CMDS,
    DETAIL_CMDS,
    DETAIL_DEVICE_CMDS,
    DOMAIN,
    FAST_CMDS,
    MODE_LIST_REQUEST,
    STATIC_CMDS,
    STATIC_DEVICE_CMDS,
)

_LOGGER = logging.getLogger(__name__)

# Coordinator data: {cmd: dataArea} for whole-gateway reads and
# {(cmd, device_id): dataArea} for per-aPower reads.
AgateData = dict[Any, dict[str, Any]]


def _is_blank_power_flow(reply: dict[str, Any]) -> bool:
    """True for a template reply: no SOC and every power/energy field zero."""
    fields = ("soc", "p_sun", "p_uti", "p_load", "p_fhp", "kwh_sun", "kwh_load", "kwh_uti_in", "kwh_uti_out")
    return all(not reply.get(f) for f in fields)


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

    @callback
    def async_publish_reply(self, key: Any, reply: dict[str, Any]) -> None:
        """Publish a reply obtained outside a poll (e.g. a write's read-back) as fresh data."""
        self._last_ok[key] = time.monotonic()
        data = dict(self.data or {})
        data[key] = reply
        self.async_set_updated_data(data)

    async def _async_update_data(self) -> AgateData:
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
    # Optional modules -> ModuleStatus (see modules.py)
    modules: dict[str, Any] = field(default_factory=dict)
    # unique_ids of every entity created this setup (stale ones are removed afterwards)
    created_uids: set[str] = field(default_factory=set)

    def has(self, module: str | None) -> bool:
        """True if the entity's module is installed (or it belongs to no module)."""
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
) -> tuple[AgateCoordinator, AgateCoordinator, AgateCoordinator, AgateCoordinator]:
    # Don't poll data for modules that aren't installed.
    skip = ({CMD_GENERATOR} if not generator else set()) | ({CMD_CIRCUIT_METER, CMD_SMART_CIRCUITS} if not circuits else set())
    fast = AgateCoordinator(hass, entry, client, "fast", fast_s, FAST_CMDS)
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
    return fast, detail, config, static
