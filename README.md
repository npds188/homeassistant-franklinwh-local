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

## Security

The aGate's local ports — **TCP 9000** (Direct Connect) and, if enabled, **TCP 502**
(Modbus) — have **no authentication**: anything on your local network can reach them, and
port 9000 allows control. **Never open these ports to the internet** (no port forwarding).

## What you get

| | Updated | Entities |
|---|---|---|
| **Status** | every 5 s | What the system is doing, in words: *Generator starting / warming up / on load / cooling down*, *Grid down – on battery*, *Charging battery*, *Exporting to grid*… with a matching icon |
| **Live power flow** | every 5 s | Solar, grid (plus import/export), home, battery (plus charge/discharge), generator power; battery SOC; aGate temperature |
| **Energy today** | every 5 s | Solar, grid import, grid export, home, battery charged/discharged — ready for the Energy dashboard |
| **Grid** | every 5 s | Voltage, current, frequency, Grid available, Grid status (Normal / Grid down / Off-grid), relay states |
| **Operating mode** | every 5 s | Current mode, **control** (Time of Use / Self Consumption / Emergency Backup) |
| **Smart circuits** | 5 s / 60 s | Relay state per circuit, **on/off switch** per circuit; power, current and lifetime energy for circuits 1 and 2, and circuit voltage |
| **Generator** | 5 s / 60 s | State (standby / starting / running / quit / error / exercising), running, power, voltage, frequency, current; **controls** below |
| **Battery (per aPower)** | every 60 s | Health (SoH), pack voltage, highest/lowest/spread cell voltage, cell temperatures (all 16 cells as attributes), aPower SOC, alarm level, inverter/DC bus details |
| **Settings** | every 15 min | Reserve SOC for every mode, TOU schedule, solar reserve, grid export policy, off-grid settings |
| **Faults & status** | every 60 s | Grid/generator relay welded or open faults, stored events, connectivity |

### Controls

All controls write to the aGate locally. Mode, smart-circuit and generator *setting* changes
are **confirmed by reading them back**; if the aGate doesn't apply one, Home Assistant shows
an error instead of a wrong state. Generator **Run / Stop** is different: the aGate accepting
the command is confirmed, but the generator actually starting or stopping is not. Watch
*Generator state* or *Status* for that.

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
  carries the widest set of what this integration offers: exact live readings, generator and
  smart circuits, cells, modes, daily energy, and all of its controls. It's the main source
  unless you choose Modbus only.
- **SunSpec Modbus TCP** (port 502) — FranklinWH's **official** interface, a published
  standard. This integration only reads it (the aGate also exposes writable SunSpec controls,
  which this integration never uses). It provides the core readings and lifetime energy
  totals, but no generator, circuits, cells or mode names, and battery and solar power only
  in 100 W steps.

Because Direct Connect is undocumented, a firmware update could change or block it. Modbus
is the safety net: if Direct Connect stops answering, the core readings (power flow, SOC,
grid voltage/current/frequency, grid status, operating mode) carry on from Modbus under the
same entities, so outage alerts, power-flow cards and automations keep working until it's
fixed. It switches back by itself when Direct Connect answers again.

Limits of the backup: it covers the core readings while Home Assistant is running.
Generator, circuit, cell and daily-energy entities, and all controls, still need Direct
Connect. Setting up or restarting the integration also needs Direct Connect once; if Home
Assistant restarts while Direct Connect is down, the integration keeps retrying until it
answers (or switch the data source to *Modbus only* in the meantime).

| Source | What you get |
|---|---|
| **Direct Connect, Modbus backup** (default, recommended) | Everything over Direct Connect, Modbus lifetime energy sensors, and Modbus takes over the core readings if Direct Connect fails. Modbus is read every 30 s (lifetime totals), and every poll only while Direct Connect is failing. |
| **Direct Connect only** | Everything over Direct Connect; Modbus is never contacted. |
| **Modbus only (read-only)** | Core readings and lifetime energy only — no controls (this integration doesn't write over Modbus), no battery cells, generator, circuits, daily energy or mode names. |

**Getting Modbus enabled:** it's off by default on AU/NZ aGates. Ask your installer or
FranklinWH support to enable *local SunSpec Modbus TCP monitoring* for your aGate, and
mention it's for Home Assistant. US aGates often have it on already (it's used by SPAN panels).

Setup checks Modbus: with the backup selected and Modbus not answering, setup carries on
with Direct Connect only and says so; you can turn the backup on later under **Configure**,
which checks Modbus answers for the same aGate first.

With Modbus in use you also get **Grid import / export lifetime**, **Battery charged /
discharged lifetime**, **Solar energy lifetime** (good for the Energy dashboard — they don't
reset at midnight), **Battery energy available**, **Battery capacity** and **aGate cabinet
temperature**, plus a **Data source** diagnostic sensor showing which source fed the last
reading and how many times it fell back.

While on Modbus, solar power comes from the aGate's own solar measurement (SunSpec model 502
*OutPw*, the same value as FranklinWH register 15502). It's in 100 W steps and rounded down,
so it reads up to 99 W below Direct Connect, and below 100 W it reads 0. It measures the
solar input only, so generator power is never counted as solar. Modbus has no generator
reading, so generator power and state read *unknown* while on Modbus. Daily energy sensors
also read *unknown* while on Modbus and resume afterwards.

### Optional modules

The Generator and Smart Circuits modules are detected automatically; setup shows what was
found and lets you override each one (*Installed* / *Not installed*). Entities for modules
that aren't installed aren't created. Optionally, your FranklinWH account can be used to
check the module list at startup (polling and control stay local). The **Installed modules**
diagnostic sensor shows the result.

### Options

**Configure** on the integration changes the data source, modules and polling intervals:

| Option | Default | Range |
|---|---|---|
| Power flow and grid | 5 s | 2–300 s |
| Battery cells, generator, circuits, faults | 60 s | 10–3600 s |
| Modes, reserves and settings | 900 s | 60–86400 s |

The aGate refreshes its live values about every 2 s, so polling faster gains nothing.

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
- **Several aGates:** add the integration once per aGate (each by its own IP). With more than
  one, device names include the aGate's short serial, e.g. *FranklinWH aGate 170129 (local)*.
  Not yet tested with two aGates. Multiple aPowers are supported (one device each).

Protocol details and the field meanings this integration relies on are in
[PROTOCOL.md](PROTOCOL.md).

## Development

Tests need only the `homeassistant` package (no running HA). The `test_*.py` files run
offline against fakes; `live_entities.py` reads a real aGate (read-only, never writes).

```bash
python -m venv venv && venv/bin/pip install homeassistant
for t in tests/test_*.py; do venv/bin/python "$t"; done
venv/bin/python tests/live_entities.py <aGate IP>      # SOURCE=modbus or SOURCE=direct for other modes
```

## Credits

- **Claude** (Anthropic) — wrote this integration, the protocol notes and the documentation,
  with [@npds188](https://github.com/npds188) directing and testing.
- **Rico** — [richo/homeassistant-franklinwh](https://github.com/richo/homeassistant-franklinwh)
  and [richo/franklinwh-python](https://github.com/richo/franklinwh-python): the FranklinWH
  cloud integration and library this work started from, including the cloud message format
  that the local protocol reuses and the smart-circuit switching recipe.
- **david2069** — [franklinwh-direct-connect-api](https://github.com/david2069/franklinwh-direct-connect-api)
  ([PyPI](https://pypi.org/project/franklinwh-direct-connect-api/),
  [docs](https://david2069.github.io/franklinwh-direct-connect-api/)) and
  [franklinwh-direct-connect-bridge](https://github.com/david2069/franklinwh-direct-connect-bridge):
  the Direct Connect work here builds on his library and research: the local command catalog,
  per-device battery reads, and the operating-mode and generator write recipes,
  hardware-verified on an aGate X. The smart-circuit and generator write code is adapted from
  his library under the MIT licence; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
- **voidstarr** — [voidstarr/franklinwh_local](https://github.com/voidstarr/franklinwh_local):
  first published the per-serial key used by the Direct Connect byte encoding.

## License

MIT — see [LICENSE](LICENSE).
