"""Operating-mode select for FranklinWH Local (local write: cmd 1727, opt 3).

Options match the cloud integration's select ("Time of Use", "Self Consumption",
"Emergency Backup"). Each maps to a programme in the aGate's mode list (cmd 1725) by
scheduling_type. A site can have several Time-of-Use programmes (e.g. a tariff
programme plus "Default"); Time of Use selects the TOU programme last seen active,
otherwise the first one not named "Default".
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AgateConfigEntry
from .client import AgateError
from .const import CMD_MODE_LIST, CMD_POWER_FLOW, MOD_GENERATOR, MODE_TYPES
from .coordinator import AgateRuntime
from .entity import AgateEntity, AgateEntityDescription
from .generator import GeneratorModeSelect

_LOGGER = logging.getLogger(__name__)

TYPE_BY_NAME = {name: t for t, name in MODE_TYPES.items()}
CONFIRM_TIMEOUT = 20.0
CONFIRM_POLL = 1.5


class OperatingModeSelect(AgateEntity, SelectEntity):
    _attr_options = list(MODE_TYPES.values())
    _attr_icon = "mdi:home-battery"

    def __init__(self, rt: AgateRuntime) -> None:
        super().__init__(
            rt,
            AgateEntityDescription(key="operating_mode_select", name="Operating mode control",
                                   tier="fast", cmd=CMD_POWER_FLOW, value_fn=lambda d: d["mode"]),
        )
        self._last_tou_id: int | None = None

    def _programmes(self) -> dict[int, dict[str, Any]]:
        modes = ((self.rt.config.data or {}).get(CMD_MODE_LIST) or {}).get("list") or []
        return {int(m["id"]): m for m in modes if isinstance(m, dict) and "id" in m}

    def _active_id(self) -> int | None:
        v = self._value()
        return int(v) if v is not None else None

    @callback
    def _handle_coordinator_update(self) -> None:
        active = self._active_id()
        prog = self._programmes().get(active) if active is not None else None
        if prog and prog.get("scheduling_type") == 1:
            self._last_tou_id = active
        super()._handle_coordinator_update()

    @property
    def current_option(self) -> str | None:
        active = self._active_id()
        prog = self._programmes().get(active) if active is not None else None
        return MODE_TYPES.get(prog.get("scheduling_type")) if prog else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        active = self._active_id()
        progs = self._programmes()
        return {
            "programme_id": active,
            "programme": (progs.get(active) or {}).get("name") if active is not None else None,
            "programmes": {str(i): f"{p.get('name')} ({MODE_TYPES.get(p.get('scheduling_type'), '?')})"
                           for i, p in progs.items()},
        }

    def _target_id(self, option: str) -> int:
        stype = TYPE_BY_NAME[option]
        progs = self._programmes()
        candidates = [i for i, p in progs.items() if p.get("scheduling_type") == stype]
        if not candidates:
            raise HomeAssistantError(f"No '{option}' programme in the aGate's mode list")
        if stype == 1:
            if self._last_tou_id in candidates:
                return self._last_tou_id
            named = [i for i in candidates if str(progs[i].get("name", "")).strip().lower() != "default"]
            return (named or candidates)[0]
        return candidates[0]

    async def async_select_option(self, option: str) -> None:
        if option not in TYPE_BY_NAME:
            raise HomeAssistantError(f"Unknown mode {option!r}")
        if not self._programmes():
            await self.rt.config.async_refresh()
        target = self._target_id(option)
        if self._active_id() == target:
            return
        client = self.rt.client
        try:
            await client.set_mode(target)
        except AgateError as err:
            raise HomeAssistantError(f"Mode change failed: {err}") from err

        deadline = asyncio.get_running_loop().time() + CONFIRM_TIMEOUT
        active = None
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(CONFIRM_POLL)
            try:
                active = int((await client.request(CMD_POWER_FLOW, {"opt": 0}))["mode"])
            except (AgateError, KeyError, TypeError, ValueError):
                continue
            if active == target:
                break
        await self.rt.fast.async_request_refresh()
        await self.rt.config.async_request_refresh()
        if active != target:
            raise HomeAssistantError(
                f"aGate accepted the mode change but still reports programme {active} "
                f"(wanted {target}) after {CONFIRM_TIMEOUT:.0f} s"
            )
        _LOGGER.info("Operating mode set to %s (programme %s), confirmed", option, target)


async def async_setup_entry(
    hass: HomeAssistant, entry: AgateConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    rt = entry.runtime_data
    entities: list[SelectEntity] = [OperatingModeSelect(rt)]
    if rt.has(MOD_GENERATOR):
        entities.append(GeneratorModeSelect(rt))
    async_add_entities(entities)
