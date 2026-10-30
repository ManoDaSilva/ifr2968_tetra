# ifr2968_tetra

A Python script that reads TETRA transmitter measurements from an IFR 2968 through an Agilent E5810A LAN/GPIB gateway. It prints one reading per line in a format that is easy to parse from other tools.

It uses only pure Python (`pyvisa` and `pyvisa-py`), so it runs on Windows, Linux and a Raspberry Pi. No VISA library is needed.

## Install

```bash
pip install -r requirements.txt
```

## Instrument setup

1. Connect the E5810A to the 2968 with a GPIB cable and to your network over Ethernet.
2. On the 2968, set the GPIB address in SET-UP I/O: GPIB (IEEE 488). Remote input and output must be set to GPIB.
3. Put the 2968 in Digital DUPLEX or SYSTEMS mode with a TETRA system selected, and make sure it is receiving bursts from the unit under test. The script does not configure the instrument.

The script addresses the instrument as `TCPIP0::<ip>::gpib0,<address>::INSTR`.

## Usage

```
python3 tetra_meas.py HOST -a GPIB_ADDR [options]
```

`HOST` is the IP address or hostname of the E5810A. `-a` is the GPIB address of the 2968.

Single reading, JSON output:

```bash
python3 tetra_meas.py 192.168.1.50 -a 4
```

```json
{"tx_frequency_mhz": 390.0123, "freq_error_hz": 40.1, "vector_error_peak_pct": 5.11, "vector_error_rms_pct": 2.01, "residual_carrier_pct": 1.23, "power_profile": "PASS", "timing_error_symbols": 0.0, "burst_power_dbm": 25.1}
```

The numbers above are examples. They are not from a real instrument.

Check the connection and show the instrument identity:

```bash
python3 tetra_meas.py 192.168.1.50 -a 4 --idn
```

Ten readings, one per second, with a timestamp, as CSV:

```bash
python3 tetra_meas.py 192.168.1.50 -a 4 -n 10 -i 1 --timestamp -f csv > log.csv
```

Keep reading until Ctrl-C, as key=value pairs:

```bash
python3 tetra_meas.py 192.168.1.50 -a 4 -n 0 -i 0.5 -f kv
```

Include the settled/invalid flag next to each value:

```bash
python3 tetra_meas.py 192.168.1.50 -a 4 --status
```

Only the absolute Tx frequency:

```bash
python3 tetra_meas.py 192.168.1.50 -a 4 -m txfreq
```

## Options

| Option | Meaning |
| --- | --- |
| `-a`, `--gpib-addr` | GPIB primary address of the 2968 (required) |
| `--sad` | GPIB secondary address, if used |
| `--interface` | VXI-11 name of the E5810A GPIB interface (default `gpib0`) |
| `-t`, `--timeout` | I/O timeout in seconds (default 10) |
| `--backend` | PyVISA backend (default `@py`) |
| `-m`, `--measure` | Measurements to run (default `txfreq alltx`) |
| `-n`, `--count` | Number of readings, 0 for no limit (default 1) |
| `-i`, `--interval` | Seconds between readings |
| `--wait-settled` | Re-read for up to this many seconds while a value is settling (default 5, 0 turns it off) |
| `-f`, `--format` | `json`, `csv` or `kv` (default `json`) |
| `--status` | Add `*_status` fields |
| `--idn` | Print `*IDN?` to stderr first |
| `--timestamp` | Add a UTC timestamp field |

## Output fields

| Field | Unit | Source |
| --- | --- | --- |
| `tx_frequency_mhz` | MHz | `:MEASUre:TXFreq?` |
| `freq_error_hz` | Hz | `:MEASDig:ALLTx?` |
| `vector_error_peak_pct` | % | `:MEASDig:ALLTx?` |
| `vector_error_rms_pct` | % | `:MEASDig:ALLTx?` |
| `residual_carrier_pct` | % | `:MEASDig:ALLTx?` |
| `power_profile` | PASS or FAIL | `:MEASDig:ALLTx?` |
| `timing_error_symbols` | symbols | `:MEASDig:ALLTx?` |
| `burst_power_dbm` | dBm | `:MEASDig:ALLTx?` |

With `--status`, each numeric field gets a companion `<field>_status` of `settling`, `settled` or `invalid`. Command details are in `docs/2965A_66A_67_68 Programming-2.pdf`, pages 5-13 and 5-20.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success |
| 1 | A measurement failed |
| 2 | The instrument could not be opened |

## Use from another script

```python
import json
import subprocess

out = subprocess.run(
    ["python3", "tetra_meas.py", "192.168.1.50", "-a", "4"],
    capture_output=True, text=True, check=True,
).stdout
reading = json.loads(out)
assert reading["burst_power_dbm"] > 20
```

## Add a measurement

Add an entry to the `MEASUREMENTS` table in `tetra_meas.py`. A measurement is a SCPI query and a function that turns the comma-separated reply into a dict of named values.

```python
def _parse_ber(f):
    return {"ber_pct": float(f[0]), "ber_settled_pct": float(f[1])}

Measurement("ber", ":MEASDig:BER?", _parse_ber),
```

After that, `-m ber` works with no other changes. Keys ending in `_status` are used by `--wait-settled` and are hidden unless `--status` is given.

## Known limits

- `:MEASUre:TXFreq?` is documented as the analogue Tx frequency. It has not been checked in the digital TETRA modes.
- The script has not been run against real hardware yet.
