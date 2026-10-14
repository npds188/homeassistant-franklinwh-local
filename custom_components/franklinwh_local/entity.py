"""Shared entity helpers for FranklinWH Local."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import AgateCoordinator, AgateRuntime


def num(value: Any, scale: float = 1.0, digits: int | None = None) -> float | None:
    """Coerce a reading to float (optionally scaled/rounded); None if unusable."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value) * scale
    except (TypeError, ValueError):
        return None
    return round(out, digits) if digits is not None else out


def at(seq: Any, index: int) -> Any:
    return seq[index] if isinstance(seq, list) and len(seq) > index else None


@dataclass(frozen=True, kw_only=True)
class AgateEntityDescription(EntityDescription):
    """Which tier/command feeds the entity and how to read it."""

    tier: str
    cmd: int
    value_fn: Callable[[dict[str, Any]], Any]
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    per_device: bool = False


def agate_device(rt: AgateRuntime) -> DeviceInfo:
    ident = rt.identity
    return DeviceInfo(
        identifiers={(DOMAIN, rt.serial)},
        manufacturer=MANUFACTURER,
        model="aGate",
        name="FranklinWH aGate (local)",
        serial_number=rt.serial,
        sw_version=ident.get("IBG_VER"),
        hw_version=str(ident.get("SyHdVersion", "")) or None,
    )


def apower_device(rt: AgateRuntime, dev_id: int) -> DeviceInfo:
    serial = rt.device_serials.get(dev_id) or f"{rt.serial}-apower{dev_id}"
    bms = rt.identity.get("BMS_VER")
    return DeviceInfo(
        identifiers={(DOMAIN, serial)},
        manufacturer=MANUFACTURER,
        model="aPower",
        name=f"FranklinWH aPower {dev_id} (local)",
        serial_number=rt.device_serials.get(dev_id) or None,
        sw_version=at(bms, dev_id - 1) if isinstance(bms, list) else None,
        via_device_id=rt.agate_device_id,
    )


class AgateEntity(CoordinatorEntity[AgateCoordinator]):
    """Entity bound to one coordinator tier and one command's reply."""

    _attr_has_entity_name = True
    entity_description: AgateEntityDescription

    def __init__(self, rt: AgateRuntime, description: AgateEntityDescription, dev_id: int | None = None) -> None:
        super().__init__(getattr(rt, description.tier))
        self.rt = rt
        self.entity_description = description
        self.dev_id = dev_id
        self._key = (description.cmd, dev_id) if description.per_device else description.cmd
        suffix = f"_apower{dev_id}" if dev_id is not None else ""
        self._attr_unique_id = f"{rt.serial}_{description.key}{suffix}"
        self._attr_device_info = apower_device(rt, dev_id) if dev_id is not None else agate_device(rt)

    @property
    def reply(self) -> dict[str, Any] | None:
        data = self.coordinator.data or {}
        return data.get(self._key)

    @property
    def available(self) -> bool:
        return super().available and self.reply is not None

    def _value(self) -> Any:
        reply = self.reply
        if reply is None:
            return None
        try:
            return self.entity_description.value_fn(reply)
        except (KeyError, TypeError, ValueError, IndexError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        reply = self.reply
        if fn is None or reply is None:
            return None
        try:
            return fn(reply)
        except (KeyError, TypeError, ValueError, IndexError):
            return None
