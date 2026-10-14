"""Binary sensors for FranklinWH Local."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AgateConfigEntry
from .const import (
    CMD_GENERATOR,
    CMD_OFFGRID,
    CMD_POWER_FLOW,
    CMD_RELAY_STATUS,
    CMD_RUN_STATUS,
    CMD_SMART_CIRCUITS,
    MOD_CIRCUITS,
    MOD_GENERATOR,
    MODULE_ENTITY_KEYS,
)
from .coordinator import AgateRuntime
from .entity import AgateEntity, AgateEntityDescription, num

DIAG = EntityCategory.DIAGNOSTIC
PROBLEM = BinarySensorDeviceClass.PROBLEM

# Grid counts as present above this line voltage (NZ nominal 230 V).
GRID_PRESENT_V = 150.0


@dataclass(frozen=True, kw_only=True)
class AgateBinaryDescription(AgateEntityDescription, BinarySensorEntityDescription):
    """Binary sensor description bound to an aGate command."""


BINARY_SENSORS: tuple[AgateBinaryDescription, ...] = (
    AgateBinaryDescription(
        key="grid_available", name="Grid available", tier="fast", cmd=CMD_RUN_STATUS,
        value_fn=lambda d: num(d["gridLineVol"], 0.1) > GRID_PRESENT_V,
        device_class=BinarySensorDeviceClass.POWER,
    ),
    AgateBinaryDescription(
        key="grid_relay", name="Grid relay closed", tier="fast", cmd=CMD_RUN_STATUS,
        value_fn=lambda d: int(d["mainRelay1Status"]) == 1,
        attrs_fn=lambda d: {k: d.get(k) for k in ("mainRelay1Status", "mainRelay2Status", "gridRelay2")},
    ),
    AgateBinaryDescription(
        key="solar_relay", name="Solar relay closed", tier="fast", cmd=CMD_RUN_STATUS,
        value_fn=lambda d: int(d["solarRelayStatus"]) == 1, entity_category=DIAG,
        attrs_fn=lambda d: {"pvRelay2": d.get("pvRelay2"), "blackStartRelay": d.get("blackStartRelay")},
    ),
    AgateBinaryDescription(
        key="generator_running", name="Generator running", tier="fast", cmd=CMD_POWER_FLOW,
        value_fn=lambda d: int(d["genStat"]) in (2, 3, 6),  # starting / running / exercising
        device_class=BinarySensorDeviceClass.RUNNING,
    ),
    AgateBinaryDescription(
        key="generator_enabled", name="Generator enabled", tier="detail", cmd=CMD_GENERATOR,
        value_fn=lambda d: int(d["genEn"]) == 1, entity_category=DIAG,
    ),
    AgateBinaryDescription(
        key="off_grid_mode", name="Off-grid mode", tier="detail", cmd=CMD_OFFGRID,
        value_fn=lambda d: int(d["offgridState"]) != 0,
        attrs_fn=lambda d: {"offgridSet": d.get("offgridSet"), "offgridSoc": d.get("offgridSoc")},
    ),
    AgateBinaryDescription(
        key="grid_relay_welded", name="Grid relay welded", tier="detail", cmd=CMD_RELAY_STATUS,
        value_fn=lambda d: int(d["gridRelayAdhesion"]) != 0, device_class=PROBLEM, entity_category=DIAG,
    ),
    AgateBinaryDescription(
        key="grid_relay_open_fault", name="Grid relay open fault", tier="detail", cmd=CMD_RELAY_STATUS,
        value_fn=lambda d: int(d["gridRelayOpen"]) != 0, device_class=PROBLEM, entity_category=DIAG,
    ),
    AgateBinaryDescription(
        key="generator_relay_welded", name="Generator relay welded", tier="detail", cmd=CMD_RELAY_STATUS,
        value_fn=lambda d: int(d["genRelayAdhesion"]) != 0, device_class=PROBLEM, entity_category=DIAG,
    ),
    AgateBinaryDescription(
        key="generator_relay_open_fault", name="Generator relay open fault", tier="detail", cmd=CMD_RELAY_STATUS,
        value_fn=lambda d: int(d["genRelayOpen"]) != 0, device_class=PROBLEM, entity_category=DIAG,
    ),
)


BINARY_SENSORS = tuple(replace(d, module=MOD_GENERATOR) if d.key in MODULE_ENTITY_KEYS[MOD_GENERATOR] else d
                       for d in BINARY_SENSORS)


class AgateBinarySensor(AgateEntity, BinarySensorEntity):
    entity_description: AgateBinaryDescription

    @property
    def is_on(self) -> bool | None:
        return self._value()


def _circuit_name(rt: AgateRuntime, n: int) -> str:
    cfg = (rt.config.data or {}).get(CMD_SMART_CIRCUITS) or {}
    name = cfg.get(f"Sw{n}Name")
    return f"Smart circuit {n}" + (f" ({name})" if name else "")


def _circuit_description(n: int, name: str) -> AgateBinaryDescription:
    return AgateBinaryDescription(
        key=f"smart_circuit_{n}", name=name, tier="fast", cmd=CMD_RUN_STATUS,
        value_fn=lambda d: int(d[f"smartRelay{n}Status"]) == 1,
        device_class=BinarySensorDeviceClass.POWER,
    )


class SmartCircuitSensor(AgateBinarySensor):
    """Smart-circuit relay state (5 s) plus its configuration (config tier)."""

    def __init__(self, rt: AgateRuntime, n: int) -> None:
        self.n = n
        super().__init__(rt, _circuit_description(n, _circuit_name(rt, n)))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        cfg = (self.rt.config.data or {}).get(CMD_SMART_CIRCUITS) or {}
        p = f"Sw{self.n}"
        return {
            "mode": cfg.get(f"{p}Mode"),
            "soc_low_cutoff": cfg.get(f"{p}SocLowSet"),
            "pro_load": cfg.get(f"{p}ProLoad"),
            "time_enabled": cfg.get(f"{p}TimeEn"),
            "times": cfg.get(f"{p}Time"),
        }


async def async_setup_entry(
    hass: HomeAssistant, entry: AgateConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    rt = entry.runtime_data
    entities: list[BinarySensorEntity] = [AgateBinarySensor(rt, d) for d in BINARY_SENSORS if rt.has(d.module)]
    if rt.has(MOD_CIRCUITS):
        entities += [SmartCircuitSensor(rt, n) for n in (1, 2, 3)]
    async_add_entities(entities)
