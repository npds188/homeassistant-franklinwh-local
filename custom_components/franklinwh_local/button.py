"""Button entities for FranklinWH Local: generator manual Run / Stop (Manual mode only)."""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AgateConfigEntry
from .const import MOD_GENERATOR
from .generator import RUN, STOP, GeneratorCommandButton


async def async_setup_entry(
    hass: HomeAssistant, entry: AgateConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    rt = entry.runtime_data
    if not rt.has(MOD_GENERATOR):
        return
    async_add_entities([GeneratorCommandButton(rt, RUN), GeneratorCommandButton(rt, STOP)])
