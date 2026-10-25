# FranklinWH aGate "Direct Connect" — protocol notes

Notes behind this integration, written by **Claude** (Anthropic) with
[@npds188](https://github.com/npds188). They combine earlier community work —
**Rico** ([richo/franklinwh-python](https://github.com/richo/franklinwh-python), the cloud
message format), **david2069**
([franklinwh-direct-connect-api](https://github.com/david2069/franklinwh-direct-connect-api),
the local command catalog and write recipes) and **voidstarr**
([franklinwh_local](https://github.com/voidstarr/franklinwh_local), the encoding key) — with
behaviour checked against the FranklinWH app (2.14) and a live aGate
(firmware `V12R02B20D00`, NZ, single-phase, one aPower).

Unofficial and undocumented by FranklinWH; may change with any firmware update.

## Transport

- Plain TCP to the aGate, **port 9000** — on its hotspot (`10.100.1.1`) or its LAN address.
  No TLS, no login for reads or the writes listed below.
- One request per connection works reliably (≈50 ms per request on a LAN).
- The FranklinWH app uses the same channel over Bluetooth when it can't reach the hotspot.

## Frame

Compact JSON, fields in this order — the same envelope the cloud `sendMqtt` API uses:

```json
{"cmdType":1301,"equipNo":"<gateway serial>","type":0,"timeStamp":<unix s>,"snno":<seq>,"len":<bytes of dataArea>,"crc":"<CRC32 of dataArea, 8 upper-hex>","dataArea":{...}}
```

**Encoding:** the first two fields (`{"cmdType":N,"equipNo":"X",`) are sent as-is. Every
following byte *b* at position *i* (0-based, after that header) is sent as

```
(b + i + len(header) + key) & 0xFF        key = sum(utf8(equipNo)) & 0xFF
```

Replies are decoded the same way. There is no terminator; read until the JSON is complete.
Before the serial is known, `equipNo` `"00000000"` works (e.g. for cmd 1101, which returns it).

**Reply** `cmdType` = request + 1. `9999` means rejected (`result` 1, `reason` e.g. 4).
`dataArea.result` 0 = OK.

**`opt`** in `dataArea`: `0` query, `1` set, `3` modify. Use `0` for reads.

## Commands this integration uses

Local codes differ from the cloud `cmdType`s; cloud equivalent shown where known.

| Local | Cloud | Purpose | Notes |
|---|---|---|---|
| 1101 | — | Firmware / serial manifest | Works with `equipNo` "00000000" |
| 1105 | — | Device list (`devMap[{id, devSN}]`) | `id` selects the aPower for 1703/1705/1833/1835 |
| 1113 | — | Connectivity (router / net / cloud) | Value meanings not confirmed |
| 1203 | — | Grid / export policy | |
| 1301 | 203 | **Live power flow** | `{"opt":0}`. With `opt:1` the aGate returns a zero-filled template — don't treat zeros as real |
| 1403 | — | Active mode programme | |
| 1407 | — | TOU schedule | |
| 1409 | 311 | Smart-circuit config | **Write** below |
| 1411 | 353 | Circuit / V2L metering | |
| 1701 | — | Install profile (supply, breaker, aPower rating) | |
| 1703 | — | Inverter / DC bus per aPower | `{"opt":0,"id":n}` |
| 1705 | — | **Battery cells** per aPower (16 V/T, SoH) | `{"opt":0,"id":n}` |
| 1707 | — | **Grid electrical + relays** | `gridLineVol` ÷10 = line voltage |
| 1709 | — | Relay faults | |
| 1723 | — | Off-grid settings/state | |
| 1725 | — | **Mode list** (`id`, `name`, `reserved_soc`, `scheduling_type`) | Read with `{"opt":1}` |
| 1727 | — | **Set mode** | **Write** below |
| 1829 | — | Stored event count | |
| 1833 / 1835 | — | Per-device firmware / states | |
| 1901 | 323 | **Generator** | **Write** below |
| 1903 | — | Solar / PV config | |

## Field meanings (verified)

### Power flow (1301)
`p_sun`, `p_uti` (grid, + import / − export), `p_load`, `p_fhp` (battery, − charging),
`p_gen` in W; `soc` %; `kwh_*` daily counters (reset at midnight); `mode` = active
programme id; `genStat` = generator state.

- **`kwh_gen`** can report a non-zero value while the cloud reports 0 — not used.
- **`offgridreason`**: locally `0` with the grid normal. Its meaning differs from the
  cloud field of the same name, so grid status is taken from `gridLineVol` (1707) instead.

### Operating mode (1725 / 1727)
`scheduling_type` identifies the mode: **1 = Time of Use, 2 = Self Consumption,
3 = Emergency Backup**. Programme `id`s are site-specific.

A site can have **more than one** `scheduling_type` 1 programme — e.g. the tariff
programme plus one named `Default`. Picking the first TOU programme can switch to the
wrong one; this integration uses the last-active TOU programme, else the first not named
`Default`.

**Set:** `1727 {"opt":3,"current_id":<programme id>}` (what the app sends). Confirmed by
the next 1301 `mode` in ≈2 s.

### Generator (1901)

| Field | Values |
|---|---|
| `mode` | **1 = Auto Schedule, 2 = Manual** (checked by switching it in the app and reading it back) |
| `manuSw` | **2 = run, 1 = stop**, 0 = none. A command: the aGate clears it. With no generator connected it is accepted and ignored |
| `genStat` | **0 disabled, 1 standby, 2 starting, 3 running, 4 quit (stopping), 5 error, 6 exercising** |
| `genStartElec` / `genCloseElec` | Auto start / stop SOC % |
| `charge{1,2,3}En`, `…StartTime`, `…EndTime` | Up to three daily operating periods (`"HH:MM"`) |
| `genEn`, `power`, `volt`, `curr`, `freq` (÷10 Hz) | Enabled flag and live values |

**Write:** read the whole 1901 block, change the fields, send it back with `opt:1`
(full-block read-modify-write), then re-read to confirm. Verified here for `mode`,
`genStartElec`, `charge1*`. `manuSw` was sent once with no generator connected (no effect);
running a generator from it is not yet verified on this system.

### Smart circuits (1409)
**Write** (same recipe as the cloud library): read the 1409 block, set every
`SwNMsgType` to 0, then for the target circuit `SwNMsgType=1`, `SwNMode` = 1 on / 0 off,
`SwNProLoad = SwNMode ^ 1`; send the whole block with `opt:1`. Confirm with
`smartRelayNStatus` in 1707 (≈1 s). Afterwards the aGate resets `MsgType` to 0 and
recomputes `ProLoad` itself. If `SwMerge` is 1, circuits 1 and 2 are switched together.

### Circuit metering (1411)
Three meter blocks: `SW1*`, `SW2*` and `CarSW*`, each with power (`SW1ExpPower`,
`SW2ExpPower`, `CarSWPower`, W), current (`SW1Curr`, `SW2Curr`, `CarSWCurr`, ÷10 A) and
lifetime energy (`SW1ExpEnergy`, `SW2ExpEnergy`, `CarSWExpEnergy`, Wh), plus `Sw1Volt` /
`Sw2Volt` (÷10 V). Which block is which circuit depends on the hardware, as in the app
(`SupportFeatures.supportAustraliaVersion`):

| aGate | Circuit 1 | Circuit 2 | `CarSW*` |
|---|---|---|---|
| `SyHdVersion` 102 and `protocolVer` ≥ V1.09.08 (Australia/NZ) | `CarSW*` (`SW1*` reads 0) | `SW2*` | circuit 1 |
| anything else | `SW1*` (`SW1*` + `SW2*` if `SwMerge` = 1) | `SW2*` | V2L / car port |

`SyHdVersion` and `protocolVer` come from the identity read (1101). Checked on an NZ aGate
(102, V1.11.01): `CarSWPower` 2945 W and `CarSWExpEnergy` 211161 Wh matched the app's
2.95 kW and 211.0 kWh for circuit 1.

### Grid (1707)
`gridLineVol` ÷ 10 = line voltage (≈239 V on a 230 V single-phase site, where the per-leg
`gridVol1/2` fields read ≈120 V). `gridCurr1` A, `gridFreq` Hz, `mainRelay1Status`,
`solarRelayStatus`, `smartRelayNStatus`.

### Not useful on this firmware
`1405` per-mode SOC block reads all zeros (use `reserved_soc` from 1725). `1801` returns
placeholder values. No local write for reserve SOC is known.
