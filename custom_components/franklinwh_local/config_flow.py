"""Config and options flow for FranklinWH Local."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback

from .client import DEFAULT_PORT, AgateClient, AgateError
from .const import (
    CONF_CONFIG_INTERVAL,
    CONF_DETAIL_INTERVAL,
    CONF_FAST_INTERVAL,
    CONF_SERIAL,
    DEFAULT_CONFIG_INTERVAL,
    DEFAULT_DETAIL_INTERVAL,
    DEFAULT_FAST_INTERVAL,
    DOMAIN,
)


class FranklinWHLocalConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            client = AgateClient(user_input[CONF_HOST], user_input[CONF_PORT])
            try:
                await client.identify()
            except AgateError:
                errors["base"] = "cannot_connect"
            else:
                serial = client.equip_no or ""
                await self.async_set_unique_id(serial)
                self._abort_if_unique_id_configured(updates={CONF_HOST: user_input[CONF_HOST]})
                return self.async_create_entry(
                    title=f"aGate {serial[-6:]} (local)",
                    data={**user_input, CONF_SERIAL: serial},
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=(user_input or {}).get(CONF_HOST, "")): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return FranklinWHLocalOptionsFlow()


class FranklinWHLocalOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        o = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(CONF_FAST_INTERVAL, default=o.get(CONF_FAST_INTERVAL, DEFAULT_FAST_INTERVAL)): vol.All(
                    int, vol.Range(min=2, max=300)
                ),
                vol.Required(
                    CONF_DETAIL_INTERVAL, default=o.get(CONF_DETAIL_INTERVAL, DEFAULT_DETAIL_INTERVAL)
                ): vol.All(int, vol.Range(min=10, max=3600)),
                vol.Required(
                    CONF_CONFIG_INTERVAL, default=o.get(CONF_CONFIG_INTERVAL, DEFAULT_CONFIG_INTERVAL)
                ): vol.All(int, vol.Range(min=60, max=86400)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
