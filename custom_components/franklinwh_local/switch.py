"""Smart-circuit switches for FranklinWH Local — the integration's only write.

State comes from the actual relay (cmd 1707 smartRelayNStatus, 5 s tier).
Turning a switch on/off writes the smart-circuit block (cmd 1409, opt 1) and
then waits for the relay to confirm.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AgateConfigEntry
from .client import AgateError
from .const import CMD_RUN_STATUS, CMD_SMART_CIRCUITS, MOD_CIRCUITS, MOD_GENERATOR
from .coordinator import AgateRuntime
from .entity import AgateEntity, AgateEntityDescription
from .generator import PERIODS, GeneratorPeriodSwitch

_LOGGER = logging.getLogger(__name__)

CONFIRM_TIMEOUT = 12.0  # s to wait for the relay to follow the command
CONFIRM_POLL = 1.0


def _relay_on(reply: dict[str, Any] | None, n: int) -> bool | None:
    if not reply or f"smartRelay{n}Status" not in reply:
        return None
    return int(reply[f"smartRelay{n}Status"]) == 1


class SmartCircuitSwitch(AgateEntity, SwitchEntity):
    _attr_device_class = SwitchDeviceClass.OUTLET

    def __init__(self, rt: AgateRuntime, n: int) -> None:
        self.n = n
        cfg = (rt.config.data or {}).get(CMD_SMART_CIRCUITS) or {}
        label = cfg.get(f"Sw{n}Name")
        super().__init__(
            rt,
            AgateEntityDescription(
                key=f"smart_circuit_{n}_switch",
                name=f"Smart circuit {n}" + (f" ({label})" if label else "") + " switch",
                tier="fast",
                cmd=CMD_RUN_STATUS,
                value_fn=lambda d: _relay_on(d, n),
                entity_registry_enabled_default=(n == 1),
            ),
        )

    @property
    def is_on(self) -> bool | None:
        return self._value()

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        cfg = (self.rt.config.data or {}).get(CMD_SMART_CIRCUITS) or {}
        p = f"Sw{self.n}"
        return {"mode": cfg.get(f"{p}Mode"), "soc_low_cutoff": cfg.get(f"{p}SocLowSet"),
                "merged_1_2": cfg.get("SwMerge") == 1}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._switch(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._switch(False)

    async def _switch(self, on: bool) -> None:
        client = self.rt.client
        try:
            await client.set_smart_circuit(self.n, on)
        except (AgateError, ValueError) as err:
            raise HomeAssistantError(f"Smart circuit {self.n}: {err}") from err

        # Wait for the relay to follow, then refresh everything that shows it.
        deadline = asyncio.get_running_loop().time() + CONFIRM_TIMEOUT
        state = None
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(CONFIRM_POLL)
            try:
                state = _relay_on(await client.request(CMD_RUN_STATUS, {"opt": 0}), self.n)
            except AgateError:
                continue
            if state is on:
                break
        await self.rt.fast.async_request_refresh()
        await self.rt.config.async_request_refresh()
        if state is not on:
            raise HomeAssistantError(
                f"Smart circuit {self.n}: aGate accepted the command but the relay still reads "
                f"{'on' if state else 'off' if state is False else 'unknown'} after {CONFIRM_TIMEOUT:.0f} s"
            )
        _LOGGER.info("Smart circuit %s switched %s (confirmed by relay)", self.n, "on" if on else "off")


async def async_setup_entry(
    hass: HomeAssistant, entry: AgateConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    rt = entry.runtime_data
    entities: list[SwitchEntity] = []
    if rt.has(MOD_CIRCUITS):
        entities += [SmartCircuitSwitch(rt, n) for n in (1, 2, 3)]
    if rt.has(MOD_GENERATOR):
        entities += [GeneratorPeriodSwitch(rt, n) for n in PERIODS]
    async_add_entities(entities)
