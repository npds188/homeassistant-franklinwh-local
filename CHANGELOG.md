# Changelog

Release notes for 0.2.2 and earlier are on the
[GitHub releases page](https://github.com/npds188/homeassistant-franklinwh-local/releases).

## 0.2.3

### Fixed
- **Smart circuit 1 power read 0 on Australia/NZ aGates.** These aGates (hardware 102,
  protocol V1.09.08 or later) report circuit 1 in the `CarSW*` meter fields, not `SW1*`.
  The integration now picks the fields the same way the FranklinWH app does, so circuit 1
  power matches the app. Other aGates are unchanged (`SW1*`, or `SW1*` + `SW2*` when
  circuits 1 and 2 are merged).

### Added
- Smart circuit 1 and 2 **current** (A) and **lifetime energy** (kWh, usable in the Energy
  dashboard). A one-off low or 0 energy reading is held back for one poll so it doesn't
  look like a meter reset. The circuit 2 current and energy sensors are disabled by
  default, like the circuit 2 switch; enable them if circuit 2 is wired.
- Circuit sensors show which meter fields they read in a `meter_fields` attribute.

### Changed
- On Australia/NZ aGates the *V2L power* sensor is no longer created: the field it read is
  smart circuit 1. If you had enabled it, it stays in the entity registry as unavailable and
  can be deleted.
