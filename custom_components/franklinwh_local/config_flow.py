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
from .modules import detect_local

MODULE_SELECTOR = SelectSelector(
    SelectSelectorConfig(options=MODULE_CHOICES, translation_key="module", mode=SelectSelectorMode.DROPDOWN)
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

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
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
                return await self.async_step_modules()
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=(user_input or {}).get(CONF_HOST, "")): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
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
            description_placeholders={"detected": self._detected},
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
            if not errors:
                return self.async_create_entry(data=_clean(user_input))
        values = {**self.config_entry.options, **(user_input or {})}
        schema = vol.Schema(
            {
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
