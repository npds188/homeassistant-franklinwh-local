"""Decide which optional modules (Generator, Smart Circuits) the aGate has.

Precedence, per module: the user's override (Installed / Not installed) → the FranklinWH
cloud accessory list (only if the user enabled it) → local detection.

Local detection (no installed-accessory list exists locally):
- Generator: the generator block (cmd 1901) reports it enabled (genEn 1) or in any
  state other than "disabled" (genStat 0).
- Smart Circuits: the identity manifest (cmd 1101) carries a Smart Load firmware
  version (SL_VER).
Both are inferred from one aGate that has both modules; if they get it wrong, the
override fixes it.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import AgateClient, AgateError
from .cloud import ACCESSORY_GENERATOR, ACCESSORY_SMART_CIRCUITS, CloudError, accessory_types
from .const import (
    CMD_GENERATOR,
    CONF_CIRCUITS_MODULE,
    CONF_CLOUD_CHECK,
    CONF_CLOUD_PASSWORD,
    CONF_CLOUD_USERNAME,
    CONF_GENERATOR_MODULE,
    MOD_CIRCUITS,
    MOD_GENERATOR,
    MODULE_AUTO,
    MODULE_INSTALLED,
    MODULE_NOT_INSTALLED,
)

_LOGGER = logging.getLogger(__name__)

MODULE_NAMES = {MOD_GENERATOR: "Generator", MOD_CIRCUITS: "Smart Circuits"}
CLOUD_TYPES = {MOD_GENERATOR: ACCESSORY_GENERATOR, MOD_CIRCUITS: ACCESSORY_SMART_CIRCUITS}
OPTION_KEYS = {MOD_GENERATOR: CONF_GENERATOR_MODULE, MOD_CIRCUITS: CONF_CIRCUITS_MODULE}


@dataclass(frozen=True)
class ModuleStatus:
    installed: bool
    source: str  # "override", "cloud" or "local"
    detail: str


def _blank(value: Any) -> bool:
    return value is None or str(value).strip() in ("", "0", "null", "None")


async def detect_local(client: AgateClient, identity: dict[str, Any]) -> dict[str, ModuleStatus]:
    try:
        gen = await client.request(CMD_GENERATOR, {"opt": 0})
    except AgateError as err:
        gen_status = ModuleStatus(True, "local", f"generator block unreadable ({err}); assuming installed")
    else:
        enabled, stat = gen.get("genEn"), gen.get("genStat")
        installed = enabled == 1 or stat not in (0, None)
        gen_status = ModuleStatus(installed, "local", f"genEn={enabled}, genStat={stat}")
    sl_ver = identity.get("SL_VER")
    circuits_status = ModuleStatus(not _blank(sl_ver), "local", f"SL_VER={sl_ver!r}")
    return {MOD_GENERATOR: gen_status, MOD_CIRCUITS: circuits_status}


async def detect_modules(
    hass: HomeAssistant, client: AgateClient, identity: dict[str, Any], options: dict[str, Any]
) -> dict[str, ModuleStatus]:
    result = await detect_local(client, identity)

    if options.get(CONF_CLOUD_CHECK) and options.get(CONF_CLOUD_USERNAME) and options.get(CONF_CLOUD_PASSWORD):
        try:
            types = await accessory_types(async_get_clientsession(hass), options[CONF_CLOUD_USERNAME],
                                          options[CONF_CLOUD_PASSWORD], client.equip_no or "")
        except CloudError as err:
            _LOGGER.warning("FranklinWH cloud module check failed (%s); using local detection", err)
            result = {m: ModuleStatus(s.installed, s.source, f"{s.detail}; cloud check failed: {err}")
                      for m, s in result.items()}
        else:
            result = {m: ModuleStatus(CLOUD_TYPES[m] in types, "cloud", f"cloud accessory types {sorted(types)}")
                      for m in result}

    for module, key in OPTION_KEYS.items():
        choice = options.get(key, MODULE_AUTO)
        if choice in (MODULE_INSTALLED, MODULE_NOT_INSTALLED):
            result[module] = ModuleStatus(choice == MODULE_INSTALLED, "override",
                                          f"set to {choice.replace('_', ' ')} in options")

    for module, status in result.items():
        _LOGGER.info("%s module: %s (%s: %s)", MODULE_NAMES[module],
                     "installed" if status.installed else "not installed", status.source, status.detail)
    return result
