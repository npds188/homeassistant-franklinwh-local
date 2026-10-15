# FranklinWH Local

A Home Assistant integration that talks to a **FranklinWH aGate directly over your
local network** — no FranklinWH cloud, no account, no internet needed — for live
monitoring every few seconds and control of the battery mode, smart circuits and
generator.

> **Made by Claude.** This integration, its protocol notes and this documentation were
> written by **Claude** (Anthropic's AI, via Claude Code), working with
> [@npds188](https://github.com/npds188) who directed the work and tested
> it on his own system. It builds on information published by **Rico**
> ([richo/homeassistant-franklinwh](https://github.com/richo/homeassistant-franklinwh),
> [richo/franklinwh-python](https://github.com/richo/franklinwh-python)) and
> **david2069** ([david2069/franklinwh-direct-connect-api](https://github.com/david2069/franklinwh-direct-connect-api)).
> See [Credits](#credits).

> **Unofficial.** Not affiliated with, endorsed by or supported by FranklinWH. It mainly uses
> the aGate's undocumented local "Direct Connect" interface (the one the FranklinWH app
> uses over the aGate hotspot / Bluetooth); a firmware update could change or block it
> at any time. It can also use FranklinWH's **official SunSpec Modbus** interface as a
> backup (see [Data source](#data-source)). Provided as-is; use at your own risk, and don't
> contact FranklinWH support about this integration.

> **Beta.** Tested on one aGate X (New Zealand, single-phase) with one aPower, a Generator
> module and Smart Circuits, including a real grid outage and generator run/stop. US
> split-phase systems, multiple aPowers and generator auto-start are untested — issues
> with your diagnostics are welcome.

## Security — read this first

The aGate's local interface has **no authentication**. Anything on your network that can
reach the aGate on **TCP port 9000** can read it *and* control it — change the battery mode,
switch smart circuits, change generator settings, go off-grid and more. The message encoding
is obfuscation, not encryption: its key is derived from the gateway serial, which the aGate
reports to anyone who asks. The Hotspot/Bluetooth password does not protect this listener.
This is how the aGate firmware works, not something this integration adds, and the protocol
is already public; but please treat the aGate as an unprotected control endpoint:

- **Put the aGate on its own VLAN** and allow **only your Home Assistant host** to reach it on
  TCP 9000 (plus outbound internet if you still use the FranklinWH app/cloud). A firewall rule
  alone does not help on a flat network: devices on the same subnet reach each other directly.
- Never expose port 9000 to the internet (no port forwarding).
- If FranklinWH has enabled **Modbus (TCP 502)** on your aGate, the same applies to it: its
  SunSpec map includes writable grid-control models (enter service, power limits, DER
  controls), also unauthenticated. This integration only ever sends Modbus *read* requests
  (function 3), but anything else on your network could write. Allow only Home Assistant to
  reach TCP 502 as well.
- Home Assistant becomes a control point for the aGate — keep its accounts and remote access
  secured.

## What you get

| | Updated | Entities |
|---|---|---|
| **Status** | every 5 s | What the system is doing, in words: *Generator starting / warming up / on load / cooling down*, *Grid down – on battery*, *Charging battery*, *Exporting to grid*… with a matching icon |
| **Live power flow** | every 5 s | Solar, grid (plus import/export), home, battery (plus charge/discharge), generator power; battery SOC; aGate temperature |
| **Energy today** | every 5 s | Solar, grid import, grid export, home, battery charged/discharged — ready for the Energy dashboard |
| **Grid** | every 5 s | Voltage, current, frequency, Grid available, Grid status (Normal / Grid down / Off-grid), relay states |
| **Operating mode** | every 5 s | Current mode, **control** (Time of Use / Self Consumption / Emergency Backup) |
| **Smart circuits** | every 5 s | Relay state per circuit, **on/off switch** per circuit |
| **Generator** | 5 s / 60 s | State (standby / starting / running / quit / error / exercising), running, power, voltage, frequency, current; **controls** below |
| **Battery (per aPower)** | every 60 s | Health (SoH), pack voltage, highest/lowest/spread cell voltage, cell temperatures (all 16 cells as attributes), aPower SOC, alarm level, inverter/DC bus details |
| **Settings** | every 15 min | Reserve SOC for every mode, TOU schedule, solar reserve, grid export policy, off-grid settings |
| **Faults & status** | every 60 s | Grid/generator relay welded or open faults, stored events, connectivity |

### Controls

All controls write to the aGate locally and **confirm the change by reading it back**;
if the aGate doesn't apply it, Home Assistant shows an error instead of a wrong state.

- **Operating mode** — Time of Use / Self Consumption / Emergency Backup (≈2 s). If your
  aGate has more than one Time-of-Use programme (e.g. your tariff plus "Default"), Time of
  Use selects the one you last used.
- **Smart circuits** — on/off per circuit (circuits 2 and 3 are disabled by default;
  enable them if they're wired). Merged circuits 1+2 are switched together.
- **Generator** (matches the app's Generator page)
  - **Generator mode** — Manual / Auto Schedule
  - **Auto start / stop SOC** — the operating range (start must be below stop)
  - **Periods 1–3** — on/off plus start and end time (overlaps are rejected)
  - **Generator run / stop** buttons — Manual mode only, like the app's Run button

Everything else is read-only. Battery reserve levels and Storm Hedge are **not** available
locally — keep the cloud integration for those if you need them.

## Requirements

- Home Assistant **2026.9** or newer
- A FranklinWH aGate on your LAN (it listens on TCP port 9000). Tested on an aGate with
  firmware `V12R02B20D00` (New Zealand, single-phase) and one aPower.
- Optional: SunSpec Modbus TCP enabled on the aGate (port 502) — see [Data source](#data-source).
- The aGate's IP address — find it in the FranklinWH app under the network settings
  (e.g. *Ethernet* or *Wi-Fi* page). A DHCP reservation on your router is recommended.

## Installation

### HACS (custom repository)
1. HACS → ⋮ → **Custom repositories** → add this repository's URL, type **Integration**.
2. Install **FranklinWH Local** and restart Home Assistant.

### Manual
Copy `custom_components/franklinwh_local` into your Home Assistant `config/custom_components/`
folder and restart Home Assistant.

## Setup

No YAML. **Settings → Devices & services → Add integration → FranklinWH Local**, enter the
aGate's IP address (port 9000). The aGate's serial number is read automatically.

### Data source

The aGate has two local interfaces:

- **Direct Connect** (TCP 9000) — the FranklinWH app's own interface. Undocumented, but it
  has everything: exact live readings, generator and smart circuits, cells, modes, daily
  energy, and all the controls. This integration always uses it as the main source.
- **SunSpec Modbus TCP** (port 502) — FranklinWH's **official** read interface, a published
  standard. It has the core readings and lifetime energy totals, but no generator, circuits,
  cells, mode names or controls, and battery power only in 100 W steps.

Because Direct Connect is undocumented, a firmware update could change or block it. Modbus
is the safety net: if Direct Connect stops answering, the core readings (power flow, SOC,
grid voltage/current/frequency, grid status, operating mode) carry on from Modbus under the
same entities, so outage alerts, power-flow cards and automations keep working until it's
fixed. It switches back by itself when Direct Connect answers again.

| Source | What you get |
|---|---|
| **Direct Connect, Modbus backup** (default, recommended) | Everything over Direct Connect, Modbus lifetime energy sensors, and Modbus takes over the core readings if Direct Connect fails. Modbus is read every 30 s (lifetime totals), and every poll only while Direct Connect is failing. |
| **Direct Connect only** | Everything over Direct Connect; Modbus is never contacted. |
| **Modbus only (read-only)** | Core readings and lifetime energy only — no controls, no battery cells, generator, circuits, daily energy or mode names. |

**Getting Modbus enabled:** it's off by default on AU/NZ aGates. Ask **FranklinWH support
directly** (in AU/NZ: info-au@franklinwh.com) to enable *local SunSpec Modbus TCP
monitoring* for your aGate, giving its serial number — installers may not know how. US
aGates often have it on already (it's used by SPAN panels).

Setup checks Modbus: with the backup selected and Modbus not answering, setup carries on
with Direct Connect only and says so; you can turn the backup on later under **Configure**,
which checks Modbus answers for the same aGate first.

With Modbus in use you also get **Grid import / export lifetime**, **Battery charged /
discharged lifetime**, **Solar energy lifetime** (good for the Energy dashboard — they don't
reset at midnight), **Battery energy available**, **Battery capacity** and **aGate cabinet
temperature**, plus a **Data source** diagnostic sensor showing which source fed the last
reading and how many times it fell back.

Modbus has no live solar register that can be trusted (it reads the inverters' standby draw
as production at night), so while on Modbus solar power is derived from home − grid −
battery and never negative — within about 1% of Direct Connect in daylight. Modbus has no
generator reading either, so with the grid down and a generator possibly running, solar
reads *unknown* rather than counting generator power as solar. Daily energy sensors read
*unknown* while on Modbus and resume afterwards.

### Optional modules

The Generator module and the Smart Circuits module are optional extras. Setup has a
**Modules** step showing what was detected; each module can be set to **Auto-detect**,
**Installed** or **Not installed**. Entities for modules that aren't installed are not
created (and are removed if a module is later set to Not installed).

- **Auto-detect (local)** — Generator: the aGate reports the generator enabled or in any
  state other than *disabled*. Smart Circuits: the aGate reports a Smart Load firmware
  version. These signals are based on one aGate that has both modules — if detection is
  wrong on yours, use the override (and please open an issue with the *Installed modules*
  sensor's attributes).
- **Cloud check (optional)** — tick *Check modules with my FranklinWH account* and enter
  your FranklinWH app login to use FranklinWH's own accessory list. It's only contacted when
  the integration starts or reloads; polling and control stay local. The password is stored
  in Home Assistant's config. If the cloud can't be reached, local detection is used.
- The diagnostic sensor **Installed modules** shows the result and how each was decided.

**Configure** (on the integration) sets the polling intervals:

Configure also changes the module settings above.

| Option | Default | Range |
|---|---|---|
| Power flow and grid | 5 s | 2–300 s |
| Battery cells, generator, circuits, faults | 60 s | 10–3600 s |
| Modes, reserves and settings | 900 s | 60–86400 s |

The aGate refreshes its live values about every 2 s, so polling faster than that gains nothing.

## Running alongside the cloud integration

This integration uses its own domain (`franklinwh_local`), so it can run alongside
[richo/homeassistant-franklinwh](https://github.com/richo/homeassistant-franklinwh) — for
example keep the cloud integration for battery reserves and Storm Hedge, and use this one
for everything live. Local polling takes load off the FranklinWH cloud, which rate-limits
heavily.

## Notes and known limitations

- **Daily energy counters** reset at midnight. An all-zero reply from the aGate is ignored so
  it can't be mistaken for a reset by the Energy dashboard.
- **Grid voltage** comes from the aGate's line voltage, so single-phase (AU/NZ) sites show the
  real ~230–240 V rather than two 120 V legs.
- **Generator run/stop**: with no generator connected, the aGate accepts and ignores the command.
  With one connected, *Run* goes *starting* → *running* after about a minute of warm-up (the
  aGate waits for stable voltage and frequency before connecting it); *Stop* goes *quit*
  (about a minute's cool-down) → *standby*.
- **Generator voltage/current** update every 60 s; generator power and state every 5 s.
- **During an outage** the aGate opens its solar relay, so solar reads 0 until it reconnects
  the panels (about an hour later on one install), and it reports a status code instead of
  the mode programme — the integration reads the real mode separately, so *Operating mode*
  stays correct.
- **Reserve SOC** is read-only here: there is no known local write.
- Only one aGate per config entry; multiple aPowers are supported (one device each).

Protocol details and the field meanings this integration relies on are in
[PROTOCOL.md](PROTOCOL.md).

## Development

`tests/live_entities.py` builds every entity against a live aGate using the
`homeassistant` package (no running HA needed):

```bash
python -m venv venv && venv/bin/pip install homeassistant
venv/bin/python tests/live_entities.py <aGate IP>
```

It only reads; it never sends a write.

`SOURCE=modbus` or `SOURCE=direct` runs it for the other data sources (default: fallback).
`tests/test_modbus.py` checks Modbus decoding, the fallback switching and the setup-time
Modbus check offline.
`tests/test_modules.py` checks module detection offline (local signals, cloud, overrides).
`tests/test_review_fixes.py` runs offline against a fake aGate (no device needed) and checks
the write-path safety rules: error replies and incomplete blocks are never written back,
generator checks run on the freshly read settings, and failed polls make entities
unavailable.

```bash
venv/bin/python tests/test_review_fixes.py
```

## Credits

- **Claude** (Anthropic) — wrote this integration, the protocol notes and the documentation,
  with [@npds188](https://github.com/npds188) directing and testing.
- **Rico** — [richo/homeassistant-franklinwh](https://github.com/richo/homeassistant-franklinwh)
  and [richo/franklinwh-python](https://github.com/richo/franklinwh-python): the FranklinWH
  cloud integration and library this work started from, including the cloud message format
  that the local protocol reuses and the smart-circuit switching recipe.
- **david2069** — [david2069/franklinwh-direct-connect-api](https://github.com/david2069/franklinwh-direct-connect-api)
  and [franklinwh-direct-connect-bridge](https://github.com/david2069/franklinwh-direct-connect-bridge):
  the local command catalog, per-device battery reads, and the operating-mode and generator
  write recipes, hardware-verified on an aGate X.
- **voidstarr** — [voidstarr/franklinwh_local](https://github.com/voidstarr/franklinwh_local):
  first published the per-serial key used by the Direct Connect byte encoding.

## License

MIT — see [LICENSE](LICENSE).
