"""Constants for the FranklinWH Local integration."""
from __future__ import annotations

DOMAIN = "franklinwh_local"
MANUFACTURER = "FranklinWH"

CONF_SERIAL = "serial"
CONF_FAST_INTERVAL = "fast_interval"
CONF_DETAIL_INTERVAL = "detail_interval"
CONF_CONFIG_INTERVAL = "config_interval"

# Optional modules: how each is decided (see modules.py)
CONF_GENERATOR_MODULE = "generator_module"
CONF_CIRCUITS_MODULE = "smart_circuits_module"
MODULE_AUTO, MODULE_INSTALLED, MODULE_NOT_INSTALLED = "auto", "installed", "not_installed"
MODULE_CHOICES = [MODULE_AUTO, MODULE_INSTALLED, MODULE_NOT_INSTALLED]
MOD_GENERATOR, MOD_CIRCUITS = "generator", "smart_circuits"

# Entity description keys owned by each optional module. Used both to decide which
# entities to create and, on reload, which entities to remove when a module is set to
# not installed. Nothing outside these sets is ever removed automatically.
MODULE_ENTITY_KEYS = {
    MOD_GENERATOR: frozenset({
        "generator_power", "generator_state", "generator_voltage", "generator_frequency",
        "generator_current", "v2l_power",
        "generator_running", "generator_enabled", "generator_relay_welded", "generator_relay_open_fault",
        "generator_mode", "generator_auto_start_soc", "generator_auto_stop_soc", "generator_run", "generator_stop",
        *(f"generator_period_{n}" for n in (1, 2, 3)),
        *(f"generator_period_{n}_{e}" for n in (1, 2, 3) for e in ("start", "end")),
    }),
    MOD_CIRCUITS: frozenset({
        "circuit_voltage", "circuit_1_power", "circuit_2_power",
        *(f"smart_circuit_{n}" for n in (1, 2, 3)),
        *(f"smart_circuit_{n}_switch" for n in (1, 2, 3)),
    }),
}

# Data source
CONF_SOURCE = "source"
CONF_MODBUS_PORT = "modbus_port"
SOURCE_FALLBACK = "direct_modbus_fallback"   # Direct Connect, Modbus when Direct Connect fails
SOURCE_DIRECT = "direct"                     # Direct Connect only
SOURCE_MODBUS = "modbus"                     # SunSpec Modbus only (read-only, core readings)
SOURCE_CHOICES = [SOURCE_FALLBACK, SOURCE_DIRECT, SOURCE_MODBUS]
CMD_MODBUS = "modbus"                        # data key for the Modbus snapshot

# Entities that exist when the source is Modbus only (everything else needs Direct Connect).
MODBUS_ONLY_KEYS = frozenset({
    "solar_power", "grid_power", "grid_import_power", "grid_export_power", "home_power", "battery_power",
    "battery_charge_power", "battery_discharge_power", "battery_soc", "agate_temperature", "grid_voltage",
    "grid_current", "grid_frequency", "grid_status", "operating_mode", "grid_available", "grid_relay",
    "data_source", "status",
    # Modbus lifetime counters (coordinator tier "modbus")
    "grid_import_total", "grid_export_total", "battery_charged_total", "battery_discharged_total",
    "solar_energy_total", "battery_energy_available", "battery_capacity", "agate_cabinet_temperature",
})
DEFAULT_MODBUS_INTERVAL = 30  # s — lifetime counters, capacity, temperatures

# Optional cloud check of the account's accessory list (setup/reload only)
CONF_CLOUD_CHECK = "cloud_check"
CONF_CLOUD_USERNAME = "cloud_username"
CONF_CLOUD_PASSWORD = "cloud_password"

DEFAULT_FAST_INTERVAL = 5  # s — power flow + grid electrical
DEFAULT_DETAIL_INTERVAL = 60  # s — battery cells, generator, circuits, faults
DEFAULT_CONFIG_INTERVAL = 900  # s — modes/reserves, schedules, settings
STATIC_INTERVAL = 86400  # s — firmware, install profile

# Local cmdType codes (request; reply is +1). All reads.
CMD_IDENTIFY = 1101
CMD_DEVICE_CHECK = 1105
CMD_CONNECTIVITY = 1113
CMD_GRID_POLICY = 1203
CMD_POWER_FLOW = 1301
CMD_MODE_CONFIG = 1403
CMD_TOU_SCHEDULE = 1407
CMD_SMART_CIRCUITS = 1409
CMD_CIRCUIT_METER = 1411
CMD_INSTALL_PROFILE = 1701
CMD_POWER_ELECTRONICS = 1703  # per device
CMD_BATTERY_CELLS = 1705  # per device
CMD_RUN_STATUS = 1707
CMD_RELAY_STATUS = 1709
CMD_OFFGRID = 1723
CMD_MODE_LIST = 1725
CMD_EVENTS = 1829
CMD_DEVICE_FIRMWARE = 1833  # per device
CMD_DEVICE_STATES = 1835  # per device
CMD_GENERATOR = 1901
CMD_SOLAR_PV = 1903

FAST_CMDS = (CMD_POWER_FLOW, CMD_RUN_STATUS)
DETAIL_CMDS = (CMD_CIRCUIT_METER, CMD_GENERATOR, CMD_RELAY_STATUS, CMD_OFFGRID, CMD_CONNECTIVITY, CMD_EVENTS)
DETAIL_DEVICE_CMDS = (CMD_BATTERY_CELLS, CMD_POWER_ELECTRONICS, CMD_DEVICE_STATES)
CONFIG_CMDS = (CMD_MODE_CONFIG, CMD_TOU_SCHEDULE, CMD_SMART_CIRCUITS, CMD_SOLAR_PV, CMD_GRID_POLICY)
STATIC_CMDS = (CMD_INSTALL_PROFILE,)
STATIC_DEVICE_CMDS = (CMD_DEVICE_FIRMWARE,)

# Mode list (1725) is read with opt=1, exactly as the app/reference client do;
# every other read uses opt=0.
MODE_LIST_REQUEST = {"opt": 1}

# scheduling_type from the mode list -> operating mode
MODE_TYPES = {1: "Time of Use", 2: "Self Consumption", 3: "Emergency Backup"}

# App enum GeneratorStatus
GENERATOR_STATES = {
    0: "disabled",
    1: "standby",
    2: "starting",
    3: "running",
    4: "quit",
    5: "error",
    6: "exercising",
}
