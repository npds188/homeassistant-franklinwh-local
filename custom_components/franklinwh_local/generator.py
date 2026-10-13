"""Generator controls for FranklinWH Local (cmd 1901 writes).

Mirrors the app's Generator page: Manual / Auto Schedule, the Auto operating range
(start/stop SOC), up to three daily operating periods, and the manual Run/Stop
command. All writes go through AgateClient.set_generator (full-block
read-modify-write, verified by re-read).
"""
from __future__ import annotations

from datetime import time as dtime
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.select import SelectEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.components.time import TimeEntity
from homeassistant.const import PERCENTAGE
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .client import AgateError
from .const import CMD_GENERATOR
from .coordinator import AgateRuntime
from .entity import AgateEntity, AgateEntityDescription

MODE_AUTO, MODE_MANUAL = "Auto Schedule", "Manual"
MODE_VALUES = {MODE_AUTO: 1, MODE_MANUAL: 2}
RUN, STOP = 2, 1   # manuSw commands
PERIODS = (1, 2, 3)


def _desc(key: str, name: str, **kw) -> AgateEntityDescription:
    return AgateEntityDescription(key=key, name=name, tier="detail", cmd=CMD_GENERATOR,
                                  value_fn=lambda d: d, **kw)


def _minutes(hhmm: str) -> int:
    h, m = str(hhmm).split(":")[:2]
    return int(h) * 60 + int(m)


def _spans(start: str, end: str) -> list[tuple[int, int]]:
    s, e = _minutes(start), _minutes(end)
    return [(s, e)] if s < e else [(s, 1440), (0, e)]   # crosses midnight


def check_periods(block: dict[str, Any]) -> None:
    """Enabled periods need start != end and must not overlap (the app wants a 1-min gap)."""
    enabled = []
    for n in PERIODS:
        if int(block.get(f"charge{n}En") or 0) != 1:
            continue
        start, end = block.get(f"charge{n}StartTime"), block.get(f"charge{n}EndTime")
        if not start or not end or _minutes(start) == _minutes(end):
            raise ServiceValidationError(f"Generator period {n}: set different start and end times first")
        enabled.append((n, _spans(start, end)))
    for i, (a, sa) in enumerate(enabled):
        for b, sb in enabled[i + 1:]:
            if any(x0 <= y1 and y0 <= x1 for x0, x1 in sa for y0, y1 in sb):
                raise ServiceValidationError(f"Generator periods {a} and {b} overlap (keep at least 1 minute apart)")


class GeneratorEntity(AgateEntity):
    """Bound to the generator block (detail tier); writes refresh it immediately."""

    @property
    def gen(self) -> dict[str, Any]:
        return self.reply or {}

    async def _write(self, **changes: Any) -> None:
        try:
            post = await self.rt.client.set_generator(**changes)
        except (AgateError, ValueError) as err:
            raise HomeAssistantError(f"Generator: {err}") from err
        data = dict(self.rt.detail.data or {})
        data[CMD_GENERATOR] = post
        self.rt.detail.async_set_updated_data(data)


class GeneratorModeSelect(GeneratorEntity, SelectEntity):
    _attr_options = [MODE_AUTO, MODE_MANUAL]
    _attr_icon = "mdi:generator-stationary"

    def __init__(self, rt: AgateRuntime) -> None:
        super().__init__(rt, _desc("generator_mode", "Generator mode"))

    @property
    def current_option(self) -> str | None:
        v = self.gen.get("mode")
        return next((k for k, n in MODE_VALUES.items() if n == v), None)

    async def async_select_option(self, option: str) -> None:
        await self._write(mode=MODE_VALUES[option])


class GeneratorSocNumber(GeneratorEntity, NumberEntity):
    _attr_native_min_value = 1
    _attr_native_max_value = 99
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.BOX

    def __init__(self, rt: AgateRuntime, field: str, key: str, name: str, icon: str) -> None:
        self.field = field
        self._attr_icon = icon
        super().__init__(rt, _desc(key, name))

    @property
    def native_value(self) -> float | None:
        v = self.gen.get(self.field)
        return float(v) if v is not None else None

    async def async_set_native_value(self, value: float) -> None:
        start = int(value) if self.field == "genStartElec" else int(self.gen.get("genStartElec", 0))
        stop = int(value) if self.field == "genCloseElec" else int(self.gen.get("genCloseElec", 100))
        if start >= stop:
            raise ServiceValidationError(f"Generator start SOC ({start}%) must be below stop SOC ({stop}%)")
        await self._write(**{self.field: int(value)})


class GeneratorPeriodSwitch(GeneratorEntity, SwitchEntity):
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, rt: AgateRuntime, n: int) -> None:
        self.n = n
        super().__init__(rt, _desc(f"generator_period_{n}", f"Generator period {n}"))

    @property
    def is_on(self) -> bool | None:
        v = self.gen.get(f"charge{self.n}En")
        return None if v is None else int(v) == 1

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        return {"start": self.gen.get(f"charge{self.n}StartTime"), "end": self.gen.get(f"charge{self.n}EndTime")}

    async def async_turn_on(self, **kwargs: Any) -> None:
        check_periods({**self.gen, f"charge{self.n}En": 1})
        await self._write(**{f"charge{self.n}En": 1})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write(**{f"charge{self.n}En": 0})


class GeneratorPeriodTime(GeneratorEntity, TimeEntity):
    _attr_icon = "mdi:clock-outline"

    def __init__(self, rt: AgateRuntime, n: int, edge: str) -> None:
        self.field = f"charge{n}{'StartTime' if edge == 'start' else 'EndTime'}"
        super().__init__(rt, _desc(f"generator_period_{n}_{edge}", f"Generator period {n} {edge}"))

    @property
    def native_value(self) -> dtime | None:
        v = self.gen.get(self.field)
        try:
            h, m = str(v).split(":")[:2]
            return dtime(int(h), int(m))
        except (ValueError, AttributeError):
            return None

    async def async_set_value(self, value: dtime) -> None:
        hhmm = f"{value.hour:02d}:{value.minute:02d}"
        check_periods({**self.gen, self.field: hhmm})
        await self._write(**{self.field: hhmm})


class GeneratorCommandButton(GeneratorEntity, ButtonEntity):
    """The app's Run button: Manual mode only."""

    def __init__(self, rt: AgateRuntime, command: int) -> None:
        self.command = command
        run = command == RUN
        self._attr_icon = "mdi:play-circle" if run else "mdi:stop-circle"
        super().__init__(rt, _desc("generator_run" if run else "generator_stop",
                                   "Generator run" if run else "Generator stop"))

    async def async_press(self) -> None:
        if self.gen.get("mode") != MODE_VALUES[MODE_MANUAL]:
            raise ServiceValidationError("Switch Generator mode to Manual before starting or stopping it by hand")
        if self.command == RUN and int(self.gen.get("genEn") or 0) != 1:
            raise ServiceValidationError("Generator is not enabled on the aGate")
        await self._write(manuSw=self.command)
