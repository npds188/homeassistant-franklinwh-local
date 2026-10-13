"""Time entities for FranklinWH Local: generator operating periods (start/end)."""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AgateConfigEntry
from .generator import PERIODS, GeneratorPeriodTime


async def async_setup_entry(
    hass: HomeAssistant, entry: AgateConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    rt = entry.runtime_data
    async_add_entities([GeneratorPeriodTime(rt, n, edge) for n in PERIODS for edge in ("start", "end")])
