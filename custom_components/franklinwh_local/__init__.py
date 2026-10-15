"""FranklinWH Local — local polling of the FranklinWH aGate over Direct Connect (TCP 9000), with optional read-only SunSpec Modbus (TCP 502) as fallback or sole source; read-only except smart-circuit switches, operating mode and generator controls."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er

import logging

from .client import DEFAULT_PORT, AgateClient, AgateError
from .const import (
    CONF_CONFIG_INTERVAL,
    CONF_DETAIL_INTERVAL,
    CONF_FAST_INTERVAL,
    CONF_MODBUS_PORT,
    CONF_SERIAL,
    CONF_SOURCE,
    DEFAULT_MODBUS_INTERVAL,
    DOMAIN,
    SOURCE_DIRECT,
    SOURCE_FALLBACK,
    SOURCE_MODBUS,
    DEFAULT_CONFIG_INTERVAL,
    DEFAULT_DETAIL_INTERVAL,
    DEFAULT_FAST_INTERVAL,
    MOD_CIRCUITS,
    MOD_GENERATOR,
    STATIC_INTERVAL,
)
from .coordinator import AgateRuntime, ModbusCoordinator, build_coordinators, discover_devices
from .entity import agate_device
from .modbus import DEFAULT_MODBUS_PORT, AgateModbus, ModbusError
from .modules import detect_modules, stale_module_unique_ids

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.SWITCH, Platform.SELECT,
             Platform.NUMBER, Platform.TIME, Platform.BUTTON]

type AgateConfigEntry = ConfigEntry[AgateRuntime]


async def async_setup_entry(hass: HomeAssistant, entry: AgateConfigEntry) -> bool:
    opts = entry.options
    source = opts.get(CONF_SOURCE, entry.data.get(CONF_SOURCE, SOURCE_FALLBACK))
    client = AgateClient(entry.data[CONF_HOST], entry.data.get(CONF_PORT, DEFAULT_PORT))
    modbus = None
    if source != SOURCE_DIRECT:
        modbus = AgateModbus(entry.data[CONF_HOST], opts.get(CONF_MODBUS_PORT, DEFAULT_MODBUS_PORT))

    if source == SOURCE_MODBUS:
        # Read-only core readings over Modbus; Direct Connect is never contacted.
        try:
            identity = {"modbus": await modbus.discover()}
        except ModbusError as err:
            raise ConfigEntryNotReady(f"aGate Modbus not reachable: {err}") from err
        devices, modules = {1: ""}, {}
    else:
        try:
            identity = await client.identify()
        except AgateError as err:
            # Even with Modbus fallback, setup needs Direct Connect once (identity,
            # devices, modules); HA retries setup until it answers.
            raise ConfigEntryNotReady(f"aGate not reachable: {err}") from err
        devices = await discover_devices(client)
        modules = await detect_modules(hass, client, identity, dict(opts))
        if modbus is not None:
            try:
                await modbus.discover()
            except ModbusError as err:
                _LOGGER.warning("Modbus not available (%s); fallback and lifetime energy will retry", err)
    device_ids = sorted(devices)
    fast, detail, config, static = build_coordinators(
        hass,
        entry,
        client,
        device_ids,
        opts.get(CONF_FAST_INTERVAL, DEFAULT_FAST_INTERVAL),
        opts.get(CONF_DETAIL_INTERVAL, DEFAULT_DETAIL_INTERVAL),
        opts.get(CONF_CONFIG_INTERVAL, DEFAULT_CONFIG_INTERVAL),
        STATIC_INTERVAL,
        generator=modules[MOD_GENERATOR].installed if modules else False,
        circuits=modules[MOD_CIRCUITS].installed if modules else False,
        modbus=modbus,
        source=source,
    )
    # The fast tier must work for setup to succeed; the others may come later.
    await fast.async_config_entry_first_refresh()
    if source != SOURCE_MODBUS:
        for coord in (config, static, detail):
            await coord.async_refresh()
    modbus_coord = None
    if modbus is not None:
        modbus_coord = ModbusCoordinator(hass, entry, modbus, DEFAULT_MODBUS_INTERVAL)
        await modbus_coord.async_refresh()

    entry.runtime_data = AgateRuntime(
        client=client,
        serial=client.equip_no or entry.data.get(CONF_SERIAL, ""),
        identity=identity,
        device_ids=device_ids,
        device_serials=devices,
        fast=fast,
        detail=detail,
        config=config,
        static=static,
        coordinators=[fast, detail, config, static],
        modules=modules,
        source=source,
        modbus=modbus_coord,
    )
    # Several aGates configured: add the short serial to device names so they're distinguishable.
    # (Existing entity IDs are kept; HA only derives entity IDs from names on first registration.)
    if len(hass.config_entries.async_entries(DOMAIN, include_ignore=False)) > 1:
        entry.runtime_data.label = entry.runtime_data.serial[-6:]
        _relabel_others(hass, entry)
    # Register the aGate first so aPower devices can link to it by registry id.
    agate = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, **agate_device(entry.runtime_data))
    entry.runtime_data.agate_device_id = agate.id
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Remove the entities of modules now decided not installed. Only module-owned
    # entities are touched; everything else is left alone.
    if source == SOURCE_MODBUS:
        return True   # modules unknown without Direct Connect: leave their entities alone
    ent_reg = er.async_get(hass)
    reg_entries = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
    stale = set(stale_module_unique_ids(entry.runtime_data.serial, [e.unique_id for e in reg_entries], modules))
    for reg_entry in reg_entries:
        if reg_entry.unique_id in stale:
            ent_reg.async_remove(reg_entry.entity_id)
    return True


def _relabel_others(hass: HomeAssistant, entry: ConfigEntry | None) -> None:
    """Reload other loaded aGates whose device names don't match the current aGate count."""
    entries = hass.config_entries.async_entries(DOMAIN, include_ignore=False)
    want_label = len(entries) > 1
    for other in entries:
        if entry is not None and other.entry_id == entry.entry_id:
            continue
        rt = getattr(other, "runtime_data", None)
        if isinstance(rt, AgateRuntime) and bool(rt.label) != want_label:
            hass.config_entries.async_schedule_reload(other.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: AgateConfigEntry) -> None:
    """Back to a single aGate: give the remaining one its plain device name again."""
    _relabel_others(hass, entry)


async def _async_options_updated(hass: HomeAssistant, entry: AgateConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: AgateConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
