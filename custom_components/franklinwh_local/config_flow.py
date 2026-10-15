"""Config and options flow for FranklinWH Local."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .client import DEFAULT_PORT, AgateClient, AgateError
from .cloud import CloudAuthError, CloudError, accessory_types
from .const import (
    CONF_CIRCUITS_MODULE,
    CONF_MODBUS_PORT,
    CONF_SOURCE,
    SOURCE_CHOICES,
    SOURCE_DIRECT,
    SOURCE_FALLBACK,
    SOURCE_MODBUS,
    CONF_CLOUD_CHECK,
    CONF_CLOUD_PASSWORD,
    CONF_CLOUD_USERNAME,
    CONF_CONFIG_INTERVAL,
    CONF_DETAIL_INTERVAL,
    CONF_FAST_INTERVAL,
    CONF_GENERATOR_MODULE,
    CONF_SERIAL,
    DEFAULT_CONFIG_INTERVAL,
    DEFAULT_DETAIL_INTERVAL,
    DEFAULT_FAST_INTERVAL,
    DOMAIN,
    MOD_CIRCUITS,
    MOD_GENERATOR,
    MODULE_AUTO,
    MODULE_CHOICES,
)
from .modbus import DEFAULT_MODBUS_PORT, AgateModbus, ModbusError
from .modules import detect_local

MODULE_SELECTOR = SelectSelector(
    SelectSelectorConfig(options=MODULE_CHOICES, translation_key="module", mode=SelectSelectorMode.DROPDOWN)
)
SOURCE_SELECTOR = SelectSelector(
    SelectSelectorConfig(options=SOURCE_CHOICES, translation_key="source", mode=SelectSelectorMode.DROPDOWN)
)
PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
EMAIL_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL))


def _module_fields(values: dict[str, Any]) -> dict:
    return {
        vol.Required(CONF_GENERATOR_MODULE, default=values.get(CONF_GENERATOR_MODULE, MODULE_AUTO)): MODULE_SELECTOR,
        vol.Required(CONF_CIRCUITS_MODULE, default=values.get(CONF_CIRCUITS_MODULE, MODULE_AUTO)): MODULE_SELECTOR,
        vol.Required(CONF_CLOUD_CHECK, default=values.get(CONF_CLOUD_CHECK, False)): BooleanSelector(),
        vol.Optional(CONF_CLOUD_USERNAME, description={"suggested_value": values.get(CONF_CLOUD_USERNAME)}): EMAIL_SELECTOR,
        vol.Optional(CONF_CLOUD_PASSWORD, description={"suggested_value": values.get(CONF_CLOUD_PASSWORD)}): PASSWORD_SELECTOR,
    }


async def _validate_cloud(flow, user_input: dict[str, Any], serial: str) -> dict[str, str]:
    """Return form errors; only checks the cloud when the user turned it on."""
    if not user_input.get(CONF_CLOUD_CHECK):
        return {}
    if not user_input.get(CONF_CLOUD_USERNAME) or not user_input.get(CONF_CLOUD_PASSWORD):
        return {"base": "cloud_credentials_required"}
    try:
        await accessory_types(async_get_clientsession(flow.hass), user_input[CONF_CLOUD_USERNAME],
                              user_input[CONF_CLOUD_PASSWORD], serial)
    except CloudAuthError:
        return {"base": "invalid_auth"}
    except CloudError:
        return {"base": "cloud_failed"}
    return {}


async def probe_modbus(host: str, port: int, serial: str | None = None) -> str | None:
    """Check the aGate's Modbus (SunSpec) interface. Returns None if usable, else a reason."""
    try:
        info = await AgateModbus(host, port, timeout=5.0).discover()
    except ModbusError as err:
        return f"not available ({err})"
    if serial and info.get("serial") and info["serial"] != serial:
        return f"answered for a different serial ({info['serial']})"
    return None


def _clean(user_input: dict[str, Any]) -> dict[str, Any]:
    """Don't keep account details the user didn't ask to use."""
    out = dict(user_input)
    if not out.get(CONF_CLOUD_CHECK):
        out.pop(CONF_CLOUD_USERNAME, None)
        out.pop(CONF_CLOUD_PASSWORD, None)
    return out


class FranklinWHLocalConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._detected = ""
        self._modbus = ""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None and user_input.get(CONF_SOURCE) == SOURCE_MODBUS:
            # Modbus only: identify over Modbus (same serial as Direct Connect); no module step.
            try:
                info = await AgateModbus(user_input[CONF_HOST], DEFAULT_MODBUS_PORT, timeout=5.0).discover()
            except ModbusError:
                errors["base"] = "cannot_connect_modbus"
            else:
                serial = info.get("serial", "")
                await self.async_set_unique_id(serial)
                self._abort_if_unique_id_configured(updates={CONF_HOST: user_input[CONF_HOST]})
                return self.async_create_entry(
                    title=f"aGate {serial[-6:]} (local)", data={**user_input, CONF_SERIAL: serial}, options={}
                )
        elif user_input is not None:
            client = AgateClient(user_input[CONF_HOST], user_input[CONF_PORT])
            try:
                identity = await client.identify()
                local = await detect_local(client, identity)
            except AgateError:
                errors["base"] = "cannot_connect"
            else:
                serial = client.equip_no or ""
                await self.async_set_unique_id(serial)
                self._abort_if_unique_id_configured(updates={CONF_HOST: user_input[CONF_HOST]})
                self._data = {**user_input, CONF_SERIAL: serial}
                yes_no = {True: "found", False: "not found"}
                self._detected = (f"Generator: {yes_no[local[MOD_GENERATOR].installed]}, "
                                  f"Smart Circuits: {yes_no[local[MOD_CIRCUITS].installed]}")
                # Check Modbus now, so the fallback is only kept if it actually works.
                self._modbus = "not used (Direct Connect only)"
                if user_input[CONF_SOURCE] == SOURCE_FALLBACK:
                    problem = await probe_modbus(user_input[CONF_HOST], DEFAULT_MODBUS_PORT, serial)
                    if problem:
                        self._data[CONF_SOURCE] = SOURCE_DIRECT
                        self._modbus = (f"{problem}. Using Direct Connect only; you can turn on the "
                                        "Modbus backup in the options once FranklinWH enables Modbus")
                    else:
                        self._modbus = "available, used for lifetime energy and as a backup if Direct Connect stops working"
                return await self.async_step_modules()
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=(user_input or {}).get(CONF_HOST, "")): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                vol.Required(CONF_SOURCE, default=(user_input or {}).get(CONF_SOURCE, SOURCE_FALLBACK)): SOURCE_SELECTOR,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_modules(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await _validate_cloud(self, user_input, self._data[CONF_SERIAL])
            if not errors:
                return self.async_create_entry(
                    title=f"aGate {self._data[CONF_SERIAL][-6:]} (local)", data=self._data, options=_clean(user_input)
                )
        return self.async_show_form(
            step_id="modules",
            data_schema=vol.Schema(_module_fields(user_input or {})),
            errors=errors,
            description_placeholders={"detected": self._detected, "modbus": self._modbus},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return FranklinWHLocalOptionsFlow()


class FranklinWHLocalOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await _validate_cloud(self, user_input, self.config_entry.data.get(CONF_SERIAL, ""))
            if not errors and user_input[CONF_SOURCE] != SOURCE_DIRECT:
                problem = await probe_modbus(self.config_entry.data[CONF_HOST], user_input[CONF_MODBUS_PORT],
                                             self.config_entry.data.get(CONF_SERIAL))
                if problem:
                    errors["base"] = "cannot_connect_modbus"
            if not errors:
                return self.async_create_entry(data=_clean(user_input))
        values = {CONF_SOURCE: self.config_entry.data.get(CONF_SOURCE, SOURCE_FALLBACK),
                  **self.config_entry.options, **(user_input or {})}
        schema = vol.Schema(
            {
                vol.Required(CONF_SOURCE, default=values[CONF_SOURCE]): SOURCE_SELECTOR,
                vol.Required(CONF_MODBUS_PORT, default=values.get(CONF_MODBUS_PORT, DEFAULT_MODBUS_PORT)): vol.All(
                    int, vol.Range(min=1, max=65535)
                ),
                vol.Required(CONF_FAST_INTERVAL, default=values.get(CONF_FAST_INTERVAL, DEFAULT_FAST_INTERVAL)): vol.All(
                    int, vol.Range(min=2, max=300)
                ),
                vol.Required(
                    CONF_DETAIL_INTERVAL, default=values.get(CONF_DETAIL_INTERVAL, DEFAULT_DETAIL_INTERVAL)
                ): vol.All(int, vol.Range(min=10, max=3600)),
                vol.Required(
                    CONF_CONFIG_INTERVAL, default=values.get(CONF_CONFIG_INTERVAL, DEFAULT_CONFIG_INTERVAL)
                ): vol.All(int, vol.Range(min=60, max=86400)),
                **_module_fields(values),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
