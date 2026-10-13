"""Number entities for FranklinWH Local: generator Auto operating range."""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AgateConfigEntry
from .generator import GeneratorSocNumber


async def async_setup_entry(
    hass: HomeAssistant, entry: AgateConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    rt = entry.runtime_data
    async_add_entities([
        GeneratorSocNumber(rt, "genStartElec", "generator_auto_start_soc", "Generator auto start SOC", "mdi:battery-low"),
        GeneratorSocNumber(rt, "genCloseElec", "generator_auto_stop_soc", "Generator auto stop SOC", "mdi:battery-high"),
    ])
