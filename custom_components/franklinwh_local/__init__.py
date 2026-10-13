"""FranklinWH Local — local polling of the FranklinWH aGate (TCP 9000); read-only except smart-circuit switches, operating mode and generator controls."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .client import DEFAULT_PORT, AgateClient, AgateError
from .const import (
    CONF_CONFIG_INTERVAL,
    CONF_DETAIL_INTERVAL,
    CONF_FAST_INTERVAL,
    DEFAULT_CONFIG_INTERVAL,
    DEFAULT_DETAIL_INTERVAL,
    DEFAULT_FAST_INTERVAL,
    STATIC_INTERVAL,
)
from .coordinator import AgateRuntime, build_coordinators, discover_devices
from .entity import agate_device

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.SWITCH, Platform.SELECT,
             Platform.NUMBER, Platform.TIME, Platform.BUTTON]

type AgateConfigEntry = ConfigEntry[AgateRuntime]


async def async_setup_entry(hass: HomeAssistant, entry: AgateConfigEntry) -> bool:
    client = AgateClient(entry.data[CONF_HOST], entry.data.get(CONF_PORT, DEFAULT_PORT))
    try:
        identity = await client.identify()
    except AgateError as err:
        raise ConfigEntryNotReady(f"aGate not reachable: {err}") from err

    devices = await discover_devices(client)
    device_ids = sorted(devices)
    opts = entry.options
    fast, detail, config, static = build_coordinators(
        hass,
        entry,
        client,
        device_ids,
        opts.get(CONF_FAST_INTERVAL, DEFAULT_FAST_INTERVAL),
        opts.get(CONF_DETAIL_INTERVAL, DEFAULT_DETAIL_INTERVAL),
        opts.get(CONF_CONFIG_INTERVAL, DEFAULT_CONFIG_INTERVAL),
        STATIC_INTERVAL,
    )
    # The fast tier must work for setup to succeed; the others may come later.
    await fast.async_config_entry_first_refresh()
    for coord in (config, static, detail):
        await coord.async_refresh()

    entry.runtime_data = AgateRuntime(
        client=client,
        serial=client.equip_no or "",
        identity=identity,
        device_ids=device_ids,
        device_serials=devices,
        fast=fast,
        detail=detail,
        config=config,
        static=static,
        coordinators=[fast, detail, config, static],
    )
    # Register the aGate first so aPower devices can link to it by registry id.
    agate = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, **agate_device(entry.runtime_data))
    entry.runtime_data.agate_device_id = agate.id
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: AgateConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: AgateConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
