#!/usr/bin/env python3
"""Read TETRA Tx measurements from an IFR 2968 through an Agilent E5810A LAN/GPIB gateway.

The E5810A exposes the GPIB bus over VXI-11, so the instrument is addressed as
TCPIP0::<ip>::gpib0,<gpib address>::INSTR. Only pure Python is used (pyvisa +
pyvisa-py), so this runs on Windows, Linux and a Raspberry Pi.

    pip install pyvisa pyvisa-py

Command references are from docs/2965A_66A_67_68 Programming-2.pdf:
    :MEASUre:TXFreq?   Tx frequency in MHz                        (p. 5-13)
    :MEASDig:ALLTx?    all TETRA digital Tx measurements          (p. 5-20)

Add a measurement by appending a Measurement to MEASUREMENTS below.
"""
import argparse
import csv
import json
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

# Status flag attached to the digital measurements (ALLTx? and friends).
STATUS_SETTLING, STATUS_SETTLED, STATUS_INVALID = 0, 1, 2
STATUS_NAMES = {0: "settling", 1: "settled", 2: "invalid", 3: "power_settling", 4: "power_settled"}


# --------------------------------------------------------------------------
# Measurement definitions
# --------------------------------------------------------------------------
@dataclass
class Measurement:
    """One SCPI query and the parser that turns its reply into named values.

    parse() receives the list of comma separated reply fields and returns a
    dict {name: value}. Names ending in _status carry the settled flag.
    """
    name: str
    query: str
    parse: Callable[[List[str]], Dict[str, object]]
    group: str = field(default="")


def _parse_txfreq(f):
    return {"tx_frequency_mhz": float(f[0])}


def _parse_alltx(f):
    # ferr,st, peak vec,st, rms vec,st, residual carrier,st, profile, timing,st, power,st
    if len(f) != 13:
        raise ValueError("expected 13 fields from :MEASDig:ALLTx?, got %d: %r" % (len(f), f))
    out = {}
    names = [
        ("freq_error_hz", float),
        ("vector_error_peak_pct", float),
        ("vector_error_rms_pct", float),
        ("residual_carrier_pct", float),
    ]
    i = 0
    for key, cast in names:
        out[key] = cast(f[i])
        out[key + "_status"] = STATUS_NAMES.get(int(f[i + 1]), f[i + 1])
        i += 2
    out["power_profile"] = f[i].strip()
    i += 1
    out["timing_error_symbols"] = float(f[i])
    out["timing_error_symbols_status"] = STATUS_NAMES.get(int(f[i + 1]), f[i + 1])
    i += 2
    out["burst_power_dbm"] = float(f[i])
    out["burst_power_dbm_status"] = STATUS_NAMES.get(int(f[i + 1]), f[i + 1])
    return out


# Order here is the order measurements are issued. Add new entries to extend.
MEASUREMENTS: Dict[str, Measurement] = {
    m.name: m
    for m in [
        Measurement("txfreq", ":MEASUre:TXFreq?", _parse_txfreq),
        Measurement("alltx", ":MEASDig:ALLTx?", _parse_alltx),
    ]
}

# Friendly selection for --measure. Each name expands to Measurement keys.
DEFAULT_SET = ["txfreq", "alltx"]


# --------------------------------------------------------------------------
# Instrument access
# --------------------------------------------------------------------------
class IFR2968:
    def __init__(self, host, gpib_addr, interface="gpib0", timeout_s=10.0, backend="@py", sad=None):
        import pyvisa  # imported late so --help works without it

        name = "%s,%d" % (interface, gpib_addr)
        if sad is not None:
            name += ",%d" % sad
        self.resource_name = "TCPIP0::%s::%s::INSTR" % (host, name)
        self.rm = pyvisa.ResourceManager(backend)
        self.inst = self.rm.open_resource(self.resource_name)
        self.inst.timeout = int(timeout_s * 1000)
        self.inst.read_termination = "\n"
        self.inst.write_termination = "\n"

    def query(self, cmd):
        return self.inst.query(cmd).strip()

    def write(self, cmd):
        self.inst.write(cmd)

    def idn(self):
        return self.query("*IDN?")

    def clear(self):
        self.write("*CLS")

    def close(self):
        try:
            self.inst.close()
        finally:
            self.rm.close()

    def measure(self, m: Measurement):
        reply = self.query(m.query)
        fields = [x.strip() for x in reply.split(",")]
        return m.parse(fields)


def take_reading(inst, names, wait_settled=0.0, poll_s=0.5):
    """Run the selected measurements once and merge the results.

    If wait_settled > 0, a measurement is repeated until none of its *_status
    fields reads "settling" or the time limit (seconds) runs out.
    """
    data: Dict[str, object] = {}
    for n in names:
        m = MEASUREMENTS[n]
        deadline = time.monotonic() + wait_settled
        while True:
            vals = inst.measure(m)
            settling = any(v == "settling" for k, v in vals.items() if k.endswith("_status"))
            if not settling or time.monotonic() >= deadline:
                break
            time.sleep(poll_s)
        data.update(vals)
    return data


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------
def emit(rows, fmt, include_status, out=sys.stdout):
    if not include_status:
        rows = [{k: v for k, v in r.items() if not k.endswith("_status")} for r in rows]
    if fmt == "json":
        for r in rows:
            out.write(json.dumps(r) + "\n")
    elif fmt == "csv":
        if rows:
            w = csv.DictWriter(out, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    else:  # kv
        for r in rows:
            out.write(" ".join("%s=%s" % kv for kv in r.items()) + "\n")
    out.flush()


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("host", help="IP address or hostname of the E5810A")
    p.add_argument("-a", "--gpib-addr", type=int, required=True,
                   help="GPIB primary address of the IFR 2968 (SET-UP I/O: GPIB menu)")
    p.add_argument("--sad", type=int, help="GPIB secondary address, if used")
    p.add_argument("--interface", default="gpib0",
                   help="VXI-11 logical name of the E5810A GPIB interface (default gpib0)")
    p.add_argument("-t", "--timeout", type=float, default=10.0, help="I/O timeout in seconds (default 10)")
    p.add_argument("--backend", default="@py",
                   help="PyVISA backend; @py is pure Python (default), or a path to a VISA library")
    p.add_argument("-m", "--measure", nargs="+", choices=sorted(MEASUREMENTS), default=DEFAULT_SET,
                   help="measurements to run (default: %s)" % " ".join(DEFAULT_SET))
    p.add_argument("-n", "--count", type=int, default=1, help="number of readings (0 = run until Ctrl-C)")
    p.add_argument("-i", "--interval", type=float, default=0.0, help="seconds between readings")
    p.add_argument("--wait-settled", type=float, default=5.0, metavar="SEC",
                   help="re-read up to SEC seconds while a value is still settling (default 5, 0 = off)")
    p.add_argument("-f", "--format", choices=["json", "csv", "kv"], default="json",
                   help="output format (default json, one object per line)")
    p.add_argument("--status", action="store_true", help="include per-value settled/invalid flags")
    p.add_argument("--idn", action="store_true", help="print *IDN? to stderr before measuring")
    p.add_argument("--timestamp", action="store_true", help="add a UTC timestamp field")
    return p.parse_args(argv)


def main(argv=None):
    a = parse_args(argv)
    try:
        inst = IFR2968(a.host, a.gpib_addr, a.interface, a.timeout, a.backend, a.sad)
    except Exception as e:
        print("cannot open instrument: %s" % e, file=sys.stderr)
        return 2
    try:
        if a.idn:
            print(inst.idn(), file=sys.stderr)
        inst.clear()
        rows = []
        k = 0
        while a.count == 0 or k < a.count:
            row = take_reading(inst, a.measure, a.wait_settled)
            if a.timestamp:
                row = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **row}
            if a.format == "csv" and a.count != 1:
                rows.append(row)  # csv needs a stable header, written at the end
            else:
                emit([row], a.format, a.status)
            k += 1
            if a.count == 0 or k < a.count:
                time.sleep(a.interval)
        if rows:
            emit(rows, "csv", a.status)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print("measurement failed: %s" % e, file=sys.stderr)
        return 1
    finally:
        inst.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
