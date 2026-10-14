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
