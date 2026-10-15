"""Sensors for FranklinWH Local."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AgateConfigEntry
from .const import (
    CMD_BATTERY_CELLS,
    CMD_CIRCUIT_METER,
    CMD_CONNECTIVITY,
    CMD_DEVICE_STATES,
    CMD_EVENTS,
    CMD_GENERATOR,
    CMD_GRID_POLICY,
    CMD_INSTALL_PROFILE,
    CMD_MODBUS,
    CMD_MODE_LIST,
    CMD_OFFGRID,
    CMD_POWER_ELECTRONICS,
    CMD_POWER_FLOW,
    CMD_RUN_STATUS,
    CMD_SOLAR_PV,
    CMD_TOU_SCHEDULE,
    GENERATOR_STATES,
    MOD_CIRCUITS,
    MOD_GENERATOR,
    MODE_TYPES,
    MODULE_ENTITY_KEYS,
    SOURCE_MODBUS,
)
from .coordinator import AgateRuntime
from .entity import AgateEntity, AgateEntityDescription, num

W, KWH, V, A, HZ, C = (
    UnitOfPower.WATT,
    UnitOfEnergy.KILO_WATT_HOUR,
    UnitOfElectricPotential.VOLT,
    UnitOfElectricCurrent.AMPERE,
    UnitOfFrequency.HERTZ,
    UnitOfTemperature.CELSIUS,
)
DIAG = EntityCategory.DIAGNOSTIC
MEAS = SensorStateClass.MEASUREMENT
TOTAL_INC = SensorStateClass.TOTAL_INCREASING


@dataclass(frozen=True, kw_only=True)
class AgateSensorDescription(AgateEntityDescription, SensorEntityDescription):
    """Sensor description bound to an aGate command."""


def _power(key: str, field: str, name: str, fn=None, **kw) -> AgateSensorDescription:
    return AgateSensorDescription(
        key=key, name=name, tier="fast", cmd=CMD_POWER_FLOW,
        value_fn=fn or (lambda d: num(d[field], digits=0)),
        native_unit_of_measurement=W, device_class=SensorDeviceClass.POWER, state_class=MEAS, **kw,
    )


def _energy(key: str, field: str, name: str) -> AgateSensorDescription:
    return AgateSensorDescription(
        key=key, name=name, tier="fast", cmd=CMD_POWER_FLOW,
        value_fn=lambda d: num(d[field], digits=3),
        native_unit_of_measurement=KWH, device_class=SensorDeviceClass.ENERGY, state_class=TOTAL_INC,
    )


def _mv_to_v(values: Any) -> list[float] | None:
    return [round(v / 1000, 3) for v in values] if isinstance(values, list) else None


def _raw(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if k not in ("opt", "result", "reason")}


GATEWAY_SENSORS: tuple[AgateSensorDescription, ...] = (
    # --- fast: power flow (1301) ---
    _power("solar_power", "p_sun", "Solar power"),
    _power("grid_power", "p_uti", "Grid power"),
    _power("grid_import_power", "p_uti", "Grid import power", fn=lambda d: max(num(d["p_uti"], digits=0), 0)),
    _power("grid_export_power", "p_uti", "Grid export power", fn=lambda d: max(-num(d["p_uti"], digits=0), 0)),
    _power("home_power", "p_load", "Home power"),
    _power("battery_power", "p_fhp", "Battery power"),
    _power("battery_charge_power", "p_fhp", "Battery charge power", fn=lambda d: max(-num(d["p_fhp"], digits=0), 0)),
    _power("battery_discharge_power", "p_fhp", "Battery discharge power", fn=lambda d: max(num(d["p_fhp"], digits=0), 0)),
    _power("generator_power", "p_gen", "Generator power"),
    AgateSensorDescription(
        key="battery_soc", name="Battery state of charge", tier="fast", cmd=CMD_POWER_FLOW,
        value_fn=lambda d: num(d["soc"], digits=1),
        native_unit_of_measurement=PERCENTAGE, device_class=SensorDeviceClass.BATTERY, state_class=MEAS,
        attrs_fn=lambda d: {"apower_soc": d.get("fhpSoc"), "apower_power_kw": d.get("fhpPower"),
                            "apower_serials": d.get("fhpSn")},
    ),
    AgateSensorDescription(
        key="agate_temperature", name="aGate temperature", tier="fast", cmd=CMD_POWER_FLOW,
        value_fn=lambda d: num(d["t_amb"], digits=1),
        native_unit_of_measurement=C, device_class=SensorDeviceClass.TEMPERATURE, state_class=MEAS,
    ),
    _energy("solar_energy_today", "kwh_sun", "Solar energy today"),
    _energy("grid_import_today", "kwh_uti_in", "Grid import today"),
    _energy("grid_export_today", "kwh_uti_out", "Grid export today"),
    _energy("home_energy_today", "kwh_load", "Home energy today"),
    _energy("battery_charged_today", "kwh_fhp_chg", "Battery charged today"),
    _energy("battery_discharged_today", "kwh_fhp_di", "Battery discharged today"),
    AgateSensorDescription(
        key="generator_state", name="Generator state", tier="fast", cmd=CMD_POWER_FLOW,
        value_fn=lambda d: GENERATOR_STATES.get(int(d["genStat"]), f"unknown_{d['genStat']}"),
        device_class=SensorDeviceClass.ENUM,
        options=[*GENERATOR_STATES.values()],
        attrs_fn=lambda d: {"raw": d.get("genStat")},
    ),
    AgateSensorDescription(
        key="power_flow_status", name="Power flow status", tier="fast", cmd=CMD_POWER_FLOW,
        value_fn=lambda d: d["run_status"], entity_category=DIAG,
        attrs_fn=lambda d: {k: d.get(k) for k in ("run_status", "slaver_stat", "elecnet_state", "offgridreason",
                                                  "infi_status", "main_sw", "pro_load", "doStatus", "diStatus",
                                                  "cd_alm", "ent")},
    ),
    # --- fast: grid electrical (1707) ---
    AgateSensorDescription(
        key="grid_voltage", name="Grid voltage", tier="fast", cmd=CMD_RUN_STATUS,
        value_fn=lambda d: num(d["gridLineVol"], 0.1, 1),
        native_unit_of_measurement=V, device_class=SensorDeviceClass.VOLTAGE, state_class=MEAS,
    ),
    AgateSensorDescription(
        key="grid_current", name="Grid current", tier="fast", cmd=CMD_RUN_STATUS,
        value_fn=lambda d: num(d["gridCurr1"], digits=1),
        native_unit_of_measurement=A, device_class=SensorDeviceClass.CURRENT, state_class=MEAS,
    ),
    AgateSensorDescription(
        key="grid_frequency", name="Grid frequency", tier="fast", cmd=CMD_RUN_STATUS,
        value_fn=lambda d: num(d["gridFreq"], digits=2),
        native_unit_of_measurement=HZ, device_class=SensorDeviceClass.FREQUENCY, state_class=MEAS,
    ),
    AgateSensorDescription(
        key="run_status", name="Run status", tier="fast", cmd=CMD_RUN_STATUS,
        value_fn=lambda d: d["dspRunstatus"], entity_category=DIAG,
        attrs_fn=_raw,
    ),
    # --- detail: circuits / V2L (1411) ---
    AgateSensorDescription(
        key="circuit_voltage", name="Smart circuit voltage", tier="detail", cmd=CMD_CIRCUIT_METER,
        value_fn=lambda d: num(d["Sw1Volt"], 0.1, 1),
        native_unit_of_measurement=V, device_class=SensorDeviceClass.VOLTAGE, state_class=MEAS,
        entity_category=DIAG, attrs_fn=_raw,
    ),
    AgateSensorDescription(
        key="circuit_1_power", name="Smart circuit 1 power", tier="detail", cmd=CMD_CIRCUIT_METER,
        value_fn=lambda d: num(d["SW1ExpPower"], digits=0),
        native_unit_of_measurement=W, device_class=SensorDeviceClass.POWER, state_class=MEAS,
    ),
    AgateSensorDescription(
        key="circuit_2_power", name="Smart circuit 2 power", tier="detail", cmd=CMD_CIRCUIT_METER,
        value_fn=lambda d: num(d["SW2ExpPower"], digits=0),
        native_unit_of_measurement=W, device_class=SensorDeviceClass.POWER, state_class=MEAS,
    ),
    AgateSensorDescription(
        key="v2l_power", name="V2L power", tier="detail", cmd=CMD_CIRCUIT_METER,
        value_fn=lambda d: num(d["CarSWPower"], digits=0),
        native_unit_of_measurement=W, device_class=SensorDeviceClass.POWER, state_class=MEAS,
        entity_registry_enabled_default=False,
    ),
    # --- detail: generator (1901) ---
    AgateSensorDescription(
        key="generator_voltage", name="Generator voltage", tier="detail", cmd=CMD_GENERATOR,
        value_fn=lambda d: num(d["volt"], 0.1, 1),
        native_unit_of_measurement=V, device_class=SensorDeviceClass.VOLTAGE, state_class=MEAS,
        entity_category=DIAG, attrs_fn=_raw,
    ),
    AgateSensorDescription(
        key="generator_frequency", name="Generator frequency", tier="detail", cmd=CMD_GENERATOR,
        value_fn=lambda d: num(d["freq"], 0.1, 1),
        native_unit_of_measurement=HZ, device_class=SensorDeviceClass.FREQUENCY, state_class=MEAS,
        entity_category=DIAG,
    ),
    AgateSensorDescription(
        key="generator_current", name="Generator current", tier="detail", cmd=CMD_GENERATOR,
        value_fn=lambda d: num(d["curr"], 0.1, 1),
        native_unit_of_measurement=A, device_class=SensorDeviceClass.CURRENT, state_class=MEAS,
        entity_category=DIAG,
    ),
    # --- detail: off-grid, connectivity, events ---
    AgateSensorDescription(
        key="offgrid_soc", name="Off-grid SOC limit", tier="detail", cmd=CMD_OFFGRID,
        value_fn=lambda d: num(d["offgridSoc"]), native_unit_of_measurement=PERCENTAGE,
        entity_category=DIAG, attrs_fn=_raw,
    ),
    AgateSensorDescription(
        key="connectivity", name="Connectivity status", tier="detail", cmd=CMD_CONNECTIVITY,
        value_fn=lambda d: f"router {d['routerStatus']} / net {d['netStatus']} / aws {d['awsStatus']}",
        entity_category=DIAG, attrs_fn=_raw,
    ),
    AgateSensorDescription(
        key="event_count", name="Stored events", tier="detail", cmd=CMD_EVENTS,
        value_fn=lambda d: num(d["num"]), state_class=MEAS, entity_category=DIAG,
    ),
    # --- config ---
    AgateSensorDescription(
        key="tou_schedule", name="TOU schedule", tier="config", cmd=CMD_TOU_SCHEDULE,
        value_fn=lambda d: f"{d['workdayLv']} weekday / {d['weekendLv']} weekend periods",
        entity_category=DIAG, attrs_fn=_raw,
    ),
    AgateSensorDescription(
        key="solar_reserve_soc", name="Solar reserve SOC", tier="config", cmd=CMD_SOLAR_PV,
        value_fn=lambda d: num(d["reSolarSoc"]), native_unit_of_measurement=PERCENTAGE,
        entity_category=DIAG, attrs_fn=_raw,
    ),
    AgateSensorDescription(
        key="grid_policy", name="Grid export policy", tier="config", cmd=CMD_GRID_POLICY,
        value_fn=lambda d: d["policy"], entity_category=DIAG,
        attrs_fn=lambda d: {k: v for k, v in _raw(d).items()
                            if k not in ("gridPowerUpPeriod", "gridPowerDownPeriod")},
    ),
    # --- static ---
    AgateSensorDescription(
        key="supply_rating", name="Supply rating", tier="static", cmd=CMD_INSTALL_PROFILE,
        value_fn=lambda d: num(d["electricSupply"]), native_unit_of_measurement=A,
        entity_category=DIAG, attrs_fn=_raw,
    ),
)

def _lifetime(key: str, field: str, name: str) -> AgateSensorDescription:
    return AgateSensorDescription(
        key=key, name=name, tier="modbus", cmd=CMD_MODBUS,
        value_fn=lambda d: num(d[field], 0.001, 3),
        native_unit_of_measurement=KWH, device_class=SensorDeviceClass.ENERGY, state_class=TOTAL_INC,
    )


# Modbus (SunSpec) lifetime counters and battery capacity, only when Modbus is in use.
# Grid: SunSpec direction from the inverter's side — "absorbed" is energy taken from the grid
# (import), "injected" is energy sent to it (export). Confirmed against Direct Connect's daily
# import counter during 3.3 kWh of overnight grid charging.
MODBUS_SENSORS: tuple[AgateSensorDescription, ...] = (
    _lifetime("grid_import_total", "lifetime_wh_absorbed", "Grid import lifetime"),
    _lifetime("grid_export_total", "lifetime_wh_injected", "Grid export lifetime"),
    _lifetime("battery_charged_total", "battery_wh_charged", "Battery charged lifetime"),
    _lifetime("battery_discharged_total", "battery_wh_discharged", "Battery discharged lifetime"),
    _lifetime("solar_energy_total", "solar_wh", "Solar energy lifetime"),
    AgateSensorDescription(
        key="battery_energy_available", name="Battery energy available", tier="modbus", cmd=CMD_MODBUS,
        value_fn=lambda d: num(d["available_wh"], 0.001, 2),
        native_unit_of_measurement=KWH, device_class=SensorDeviceClass.ENERGY_STORAGE, state_class=MEAS,
    ),
    AgateSensorDescription(
        key="battery_capacity", name="Battery capacity", tier="modbus", cmd=CMD_MODBUS,
        value_fn=lambda d: num(d["capacity_wh"], 0.001, 2),
        native_unit_of_measurement=KWH, device_class=SensorDeviceClass.ENERGY_STORAGE, entity_category=DIAG,
        attrs_fn=lambda d: {"state_of_health": d.get("soh")},
    ),
    AgateSensorDescription(
        key="agate_cabinet_temperature", name="aGate cabinet temperature", tier="modbus", cmd=CMD_MODBUS,
        value_fn=lambda d: num(d["temp_cabinet"], digits=1),
        native_unit_of_measurement=C, device_class=SensorDeviceClass.TEMPERATURE, state_class=MEAS,
        entity_category=DIAG, attrs_fn=lambda d: {"switch_temperature": d.get("temp_switch")},
    ),
)

# Entities that only exist when the optional module is installed.
_MODULE_OF = {key: module for module, keys in MODULE_ENTITY_KEYS.items() for key in keys}
GATEWAY_SENSORS = tuple(replace(d, module=_MODULE_OF.get(d.key)) for d in GATEWAY_SENSORS)

APOWER_SENSORS: tuple[AgateSensorDescription, ...] = (
    AgateSensorDescription(
        key="battery_health", name="Battery health", tier="detail", cmd=CMD_BATTERY_CELLS, per_device=True,
        value_fn=lambda d: num(d["batSoh"], digits=1), native_unit_of_measurement=PERCENTAGE, state_class=MEAS,
    ),
    AgateSensorDescription(
        key="pack_voltage", name="Battery pack voltage", tier="detail", cmd=CMD_BATTERY_CELLS, per_device=True,
        value_fn=lambda d: num(d["batTotalVolt"], digits=2),
        native_unit_of_measurement=V, device_class=SensorDeviceClass.VOLTAGE, state_class=MEAS,
    ),
    AgateSensorDescription(
        key="cell_voltage_max", name="Cell voltage highest", tier="detail", cmd=CMD_BATTERY_CELLS, per_device=True,
        value_fn=lambda d: num(d["singleHighestVolt"], 0.001, 3),
        native_unit_of_measurement=V, device_class=SensorDeviceClass.VOLTAGE, state_class=MEAS,
        attrs_fn=lambda d: {"cell_voltages": _mv_to_v(d.get("batVolt"))},
    ),
    AgateSensorDescription(
        key="cell_voltage_min", name="Cell voltage lowest", tier="detail", cmd=CMD_BATTERY_CELLS, per_device=True,
        value_fn=lambda d: num(d["singleLowestVolt"], 0.001, 3),
        native_unit_of_measurement=V, device_class=SensorDeviceClass.VOLTAGE, state_class=MEAS,
    ),
    AgateSensorDescription(
        key="cell_voltage_spread", name="Cell voltage spread", tier="detail", cmd=CMD_BATTERY_CELLS, per_device=True,
        value_fn=lambda d: num(d["singleHighestVolt"]) - num(d["singleLowestVolt"]),
        native_unit_of_measurement=UnitOfElectricPotential.MILLIVOLT, device_class=SensorDeviceClass.VOLTAGE,
        state_class=MEAS,
    ),
    AgateSensorDescription(
        key="cell_temp_max", name="Cell temperature highest", tier="detail", cmd=CMD_BATTERY_CELLS, per_device=True,
        value_fn=lambda d: num(d["singleHighestTemp"], digits=1),
        native_unit_of_measurement=C, device_class=SensorDeviceClass.TEMPERATURE, state_class=MEAS,
        attrs_fn=lambda d: {"cell_temperatures": d.get("batTemp")},
    ),
    AgateSensorDescription(
        key="cell_temp_min", name="Cell temperature lowest", tier="detail", cmd=CMD_BATTERY_CELLS, per_device=True,
        value_fn=lambda d: num(d["singleLowestTemp"], digits=1),
        native_unit_of_measurement=C, device_class=SensorDeviceClass.TEMPERATURE, state_class=MEAS,
    ),
    AgateSensorDescription(
        key="apower_soc", name="aPower state of charge", tier="detail", cmd=CMD_BATTERY_CELLS, per_device=True,
        value_fn=lambda d: num(d["batSoc"], digits=1),
        native_unit_of_measurement=PERCENTAGE, device_class=SensorDeviceClass.BATTERY, state_class=MEAS,
    ),
    AgateSensorDescription(
        key="battery_alarm_level", name="Battery alarm level", tier="detail", cmd=CMD_BATTERY_CELLS,
        per_device=True, value_fn=lambda d: num(d["alarmLevel"]), entity_category=DIAG,
    ),
    AgateSensorDescription(
        key="dc_bus_voltage", name="DC bus voltage", tier="detail", cmd=CMD_POWER_ELECTRONICS, per_device=True,
        value_fn=lambda d: num(d["middleBusVolt"], digits=1),
        native_unit_of_measurement=V, device_class=SensorDeviceClass.VOLTAGE, state_class=MEAS,
        entity_category=DIAG, attrs_fn=_raw,
    ),
    AgateSensorDescription(
        key="inverter_status", name="Inverter status", tier="detail", cmd=CMD_POWER_ELECTRONICS, per_device=True,
        value_fn=lambda d: d["inverterStatus"], entity_category=DIAG,
        attrs_fn=lambda d: {"dcdc_status": d.get("DCDCStatus"), "run_mode": d.get("runMode")},
    ),
    AgateSensorDescription(
        key="device_states", name="Device states", tier="detail", cmd=CMD_DEVICE_STATES, per_device=True,
        value_fn=lambda d: f"PE {d['peState']} / BMS {d['bmsState']}", entity_category=DIAG, attrs_fn=_raw,
    ),
)


class AgateSensor(AgateEntity, SensorEntity):
    entity_description: AgateSensorDescription
    # Cell arrays change every minute — keep them out of the recorder.
    _unrecorded_attributes = frozenset({"cell_voltages", "cell_temperatures"})

    @property
    def native_value(self) -> Any:
        return self._value()


def _mode_types(rt: AgateRuntime) -> dict[int, dict[str, Any]]:
    modes = ((rt.config.data or {}).get(CMD_MODE_LIST) or {}).get("list") or []
    return {int(m["id"]): m for m in modes if isinstance(m, dict) and "id" in m}


class ModulesSensor(AgateEntity, SensorEntity):
    """Which optional modules were found, and how (override / cloud / local)."""

    _attr_icon = "mdi:puzzle"

    def __init__(self, rt: AgateRuntime) -> None:
        super().__init__(
            rt,
            AgateSensorDescription(key="installed_modules", name="Installed modules", tier="static",
                                   cmd=CMD_INSTALL_PROFILE, value_fn=lambda d: d, entity_category=DIAG),
        )

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> str:
        found = [MODULE_LABELS[m] for m, s in self.rt.modules.items() if s.installed]
        return ", ".join(found) if found else "None"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {MODULE_LABELS[m]: {"installed": s.installed, "source": s.source, "detail": s.detail}
                for m, s in self.rt.modules.items()}


MODULE_LABELS = {MOD_GENERATOR: "Generator", MOD_CIRCUITS: "Smart Circuits"}


class OperatingModeSensor(AgateEntity, SensorEntity):
    """Active mode from the 5 s power flow, named via the mode list (config tier)."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [*MODE_TYPES.values(), "unknown"]

    def __init__(self, rt: AgateRuntime) -> None:
        super().__init__(
            rt,
            AgateSensorDescription(key="operating_mode", name="Operating mode", tier="fast",
                                   cmd=CMD_POWER_FLOW, value_fn=lambda d: d["mode"]),
        )

    @property
    def native_value(self) -> str | None:
        mode_id = self._value()
        info = _mode_types(self.rt).get(int(mode_id)) if mode_id is not None else None
        if info:
            return MODE_TYPES.get(int(info["scheduling_type"]), "unknown")
        # From Modbus without a mode list: only the mode type is known.
        mode_type = (self.reply or {}).get("mode_type")
        if mode_type is not None:
            return MODE_TYPES.get(int(mode_type), "unknown")
        return None if mode_id is None else "unknown"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        reply = self.reply or {}
        info = _mode_types(self.rt).get(int(reply.get("mode", -1)), {})
        return {"mode_id": reply.get("mode"), "programme": reply.get("name"),
                "reserve_soc": info.get("reserved_soc"),
                # set off-grid, when the power flow reports a status code instead of the programme
                "power_flow_mode_code": reply.get("mode_raw")}


GRID_NORMAL, GRID_DOWN, GRID_OFF = "Normal", "Grid down", "Off-grid"
GRID_PRESENT_V = 150.0


class GridStatusSensor(AgateEntity, SensorEntity):
    """Normal / Grid down / Off-grid, from grid voltage (5 s) and the off-grid setting (detail tier).

    Mirrors the cloud integration's NORMAL / DOWN / OFF grid status.
    """

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [GRID_NORMAL, GRID_DOWN, GRID_OFF]

    def __init__(self, rt: AgateRuntime) -> None:
        super().__init__(
            rt,
            AgateSensorDescription(key="grid_status", name="Grid status", tier="fast", cmd=CMD_RUN_STATUS,
                                   value_fn=lambda d: num(d["gridLineVol"], 0.1)),
        )

    @property
    def native_value(self) -> str | None:
        volts = self._value()
        if volts is None:
            return None
        offgrid = ((self.rt.detail.data or {}).get(CMD_OFFGRID) or {}).get("offgridState")
        if offgrid not in (None, 0, "0"):
            return GRID_OFF
        return GRID_NORMAL if volts > GRID_PRESENT_V else GRID_DOWN

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        reply = self.reply or {}
        offgrid = (self.rt.detail.data or {}).get(CMD_OFFGRID) or {}
        return {"grid_voltage": num(reply.get("gridLineVol"), 0.1, 1),
                "grid_relay_closed": {1: True, 0: False}.get(reply.get("mainRelay1Status")),
                "offgrid_state": offgrid.get("offgridState")}


# --- Status: one plain-language summary, like a vacuum's "Cleaning / Docked" ---------------
ACTIVE_W = 50          # below this a flow counts as idle
GEN_VOLTS_PRESENT = 100  # genVoltage (1707, tenths of a volt) above this = generator output present
STATUS_ICONS = {
    "Generator starting": "mdi:engine-outline",
    "Generator warming up": "mdi:engine",
    "Generator on load": "mdi:generator-stationary",
    "Generator running (no load)": "mdi:engine",
    "Generator cooling down": "mdi:fan-clock",
    "Generator exercising": "mdi:engine",
    "Generator error": "mdi:engine-off",
    "Off-grid": "mdi:island",
    "Grid down – on battery": "mdi:transmission-tower-off",
    "Grid down – on solar": "mdi:solar-power",
    "Grid down": "mdi:transmission-tower-off",
    "Charging battery": "mdi:battery-charging",
    "Battery powering home": "mdi:home-battery",
    "Exporting to grid": "mdi:transmission-tower-export",
    "Importing from grid": "mdi:transmission-tower-import",
    "Idle": "mdi:home-lightning-bolt-outline",
}


def agate_status(flow: dict[str, Any], run: dict[str, Any], offgrid_state: Any = None,
                 generator: bool = True) -> str | None:
    """Summarise power flow (1301), grid electrical (1707) and the off-grid setting."""
    if not flow:
        return None
    w = lambda key: num(flow.get(key)) or 0.0  # noqa: E731
    battery, grid, gen = w("p_fhp"), w("p_uti"), w("p_gen")
    gen_state = num(flow.get("genStat"))
    if generator and gen_state is not None:
        gen_volts = num((run or {}).get("genVoltage")) or 0.0
        if gen_state == 2:
            return "Generator warming up" if gen_volts > GEN_VOLTS_PRESENT else "Generator starting"
        if gen_state == 3:
            return "Generator on load" if gen > ACTIVE_W else "Generator running (no load)"
        if gen_state == 4:
            return "Generator cooling down"
        if gen_state == 6:
            return "Generator exercising"
        if gen_state == 5:
            return "Generator error"
    if offgrid_state not in (None, 0, "0"):
        return "Off-grid"
    volts = num((run or {}).get("gridLineVol"), 0.1)
    if volts is not None and volts <= GRID_PRESENT_V:
        if battery > ACTIVE_W:
            return "Grid down – on battery"
        # Solar unknown on Modbus while a generator might be supplying: don't guess.
        return "Grid down – on solar" if (num(flow.get("p_sun")) or 0.0) > ACTIVE_W else "Grid down"
    if battery < -ACTIVE_W:
        return "Charging battery"
    if battery > ACTIVE_W:
        return "Battery powering home"
    if grid < -ACTIVE_W:
        return "Exporting to grid"
    if grid > ACTIVE_W:
        return "Importing from grid"
    return "Idle"


class StatusSensor(AgateEntity, SensorEntity):
    """What the system is doing now, in words (5 s)."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(STATUS_ICONS)

    def __init__(self, rt: AgateRuntime) -> None:
        super().__init__(
            rt,
            AgateSensorDescription(key="status", name="Status", tier="fast", cmd=CMD_POWER_FLOW,
                                   value_fn=lambda d: d),
        )

    def _parts(self) -> tuple[dict[str, Any], dict[str, Any], Any]:
        data = self.coordinator.data or {}
        offgrid = ((self.rt.detail.data or {}).get(CMD_OFFGRID) or {}).get("offgridState")
        return data.get(CMD_POWER_FLOW) or {}, data.get(CMD_RUN_STATUS) or {}, offgrid

    @property
    def native_value(self) -> str | None:
        flow, run, offgrid = self._parts()
        return agate_status(flow, run, offgrid, self.rt.has(MOD_GENERATOR))

    @property
    def icon(self) -> str:
        return STATUS_ICONS.get(self.native_value or "", "mdi:home-lightning-bolt-outline")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        flow, run, _ = self._parts()
        return {
            "battery_soc": num(flow.get("soc"), digits=1), "battery_power": num(flow.get("p_fhp"), digits=0),
            "home_power": num(flow.get("p_load"), digits=0), "solar_power": num(flow.get("p_sun"), digits=0),
            "grid_power": num(flow.get("p_uti"), digits=0), "generator_power": num(flow.get("p_gen"), digits=0),
            "generator_voltage": num(run.get("genVoltage"), 0.1, 1),
        }


class DataSourceSensor(AgateEntity, SensorEntity):
    """Which source fed the latest 5 s readings: Direct Connect or Modbus."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["Direct Connect", "Modbus"]
    _attr_icon = "mdi:swap-horizontal"
    _attr_entity_category = DIAG

    def __init__(self, rt: AgateRuntime) -> None:
        super().__init__(
            rt,
            AgateSensorDescription(key="data_source", name="Data source", tier="fast",
                                   cmd=CMD_POWER_FLOW, value_fn=lambda d: d),
        )

    @property
    def native_value(self) -> str:
        return "Modbus" if self.coordinator.active_source == SOURCE_MODBUS else "Direct Connect"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self.coordinator
        return {"configured": c.source, "fallbacks": c.fallback_count, "last_fallback": c.last_fallback}


class ModeReserveSensor(AgateEntity, SensorEntity):
    """Battery reserve of one programme from the mode list."""

    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, rt: AgateRuntime, mode_id: int, name: str) -> None:
        self.mode_id = mode_id
        super().__init__(
            rt,
            AgateSensorDescription(key=f"reserve_{mode_id}", name=f"Reserve {name}", tier="config",
                                   cmd=CMD_MODE_LIST, value_fn=lambda d: d),
        )

    @property
    def native_value(self) -> float | None:
        info = _mode_types(self.rt).get(self.mode_id)
        return num(info.get("reserved_soc")) if info else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        info = _mode_types(self.rt).get(self.mode_id, {})
        return {"mode_id": self.mode_id, "mode_type": MODE_TYPES.get(info.get("scheduling_type"))}


async def async_setup_entry(
    hass: HomeAssistant, entry: AgateConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    rt = entry.runtime_data
    entities: list[SensorEntity] = [AgateSensor(rt, d) for d in GATEWAY_SENSORS if rt.shows(d.key, d.module)]
    entities.append(StatusSensor(rt))
    entities.append(OperatingModeSensor(rt))
    entities.append(GridStatusSensor(rt))
    if rt.modbus is not None:
        entities.append(DataSourceSensor(rt))
        entities += [AgateSensor(rt, d) for d in MODBUS_SENSORS]
    if rt.source != SOURCE_MODBUS:
        entities.append(ModulesSensor(rt))
        entities += [ModeReserveSensor(rt, mid, m.get("name", str(mid))) for mid, m in _mode_types(rt).items()]
        for dev in rt.device_ids:
            entities += [AgateSensor(rt, d, dev) for d in APOWER_SENSORS]
    async_add_entities(entities)
