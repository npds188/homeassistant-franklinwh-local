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
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import AgateClient, AgateError
from .const import (
    CMD_DEVICE_CHECK,
    CMD_MODE_LIST,
    CMD_POWER_FLOW,
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
        # Slow tiers keep whatever they did get if one command fails.
        self.partial_ok = partial_ok

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
        for key, cmd, payload in requests:
            try:
                reply = await self.client.request(cmd, payload)
                if cmd == CMD_POWER_FLOW and _is_blank_power_flow(reply):
                    # A zero-filled reply would look like a midnight reset to the
                    # energy dashboard's daily counters and double-count the day.
                    raise AgateError("power flow reply is all zeros (ignored)")
                data[key] = reply
            except AgateError as err:
                if not self.partial_ok:
                    raise UpdateFailed(str(err)) from err
                errors.append(str(err))
                if self.data and key in self.data:
                    data[key] = self.data[key]  # keep the last good value
        if errors:
            _LOGGER.debug("%s: partial update, %s", self.name, "; ".join(errors))
            if not data:
                raise UpdateFailed("; ".join(errors))
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


async def discover_devices(client: AgateClient) -> dict[int, str]:
    """aPower device ids -> serials, from the device check list (1105)."""
    try:
        info = await client.request(CMD_DEVICE_CHECK, {"opt": 0})
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
) -> tuple[AgateCoordinator, AgateCoordinator, AgateCoordinator, AgateCoordinator]:
    fast = AgateCoordinator(hass, entry, client, "fast", fast_s, FAST_CMDS)
    detail = AgateCoordinator(
        hass, entry, client, "detail", detail_s, DETAIL_CMDS, DETAIL_DEVICE_CMDS, device_ids, partial_ok=True
    )
    config = AgateCoordinator(
        hass, entry, client, "config", config_s, (CMD_MODE_LIST, *CONFIG_CMDS), partial_ok=True
    )
    static = AgateCoordinator(
        hass, entry, client, "static", static_s, STATIC_CMDS, STATIC_DEVICE_CMDS, device_ids, partial_ok=True
    )
    return fast, detail, config, static
