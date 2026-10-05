#!/usr/bin/env python3
"""fifine AM8 (3142:a010) control over MVsilicon ACP (USB HID interface 4).

  status
  mute on|off|toggle
  gain DB                      digital mic gain, -90..+12 dB (stock +6)
  noise off|low|mid|high       noise gate on the mic path
  noise THRESHOLD_DB RATIO     custom gate, e.g. noise -35 4
  monitor PERCENT              headphone volume (the knob overrides it)
  lowcut off|on [HZ]           high-pass on the mic, default 100 Hz (cuts rumble/hum)
  clarity off|DB               presence boost at 3 kHz, 0..+9 dB
  bass off|DB                  low shelf at 150 Hz, -12..+9 dB
  apply [FILE]                 run every line of FILE (default ~/.config/am8.conf)

Add --dry to print the frames without sending. Settings live in RAM and reset on replug.
Run as root, or install 70-fifine-am8.rules for access without sudo.
"""
import argparse
import struct
import sys
import time

VID, PID, IFACE = 0x3142, 0xA010, 4
REPORT_LEN = 256

GAIN_NODE = 0x95      # Gain Control: [enable, mute, gain centi-dB]
NOISE_NODE = 0x90     # Noise Suppressor/Expander: [enable, threshold centi-dB, ratio, attack, release]
DAC0 = 0x09           # body offsets 7/9: L/R volume 0..4095
EQ_NODE = 0x99        # mic-path EQ, all 10 filters unused in stock firmware
# EQ params: [0]=enable [1]=pregain [2]=calc type, then 10 x (enable, type, f0, Q Q6.10, gain Q8.8 dB).
EQ_PEAK, EQ_LOWSHELF, EQ_HIGHPASS = 0, 1, 4
EQ_BANDS = {"lowcut": 1, "clarity": 2, "bass": 3}

NOISE_PRESETS = {"low": (-45.0, 3), "mid": (-38.0, 4), "high": (-32.0, 6)}

# Codes that can reflash or wedge the chip, and nodes whose writes misbehave
# (0xA0/0xA1 voice changer re-enumerates the mic); never sent.
FORBIDDEN = {0x11, 0xFB, 0xFD, 0xFE, 0xFF, 0xA0, 0xA1, 0xA3}


def hexs(b):
    return " ".join("%02X" % x for x in b)


class Am8:
    def __init__(self, dry):
        self.dry = dry
        self.dev = None
        if dry:
            return
        import usb.core
        self.dev = usb.core.find(idVendor=VID, idProduct=PID)
        if self.dev is None:
            sys.exit("AM8 not found")
        try:
            if self.dev.is_kernel_driver_active(IFACE):
                self.dev.detach_kernel_driver(IFACE)
        except Exception:
            pass

    def _out(self, frame):
        if self.dry:
            print(">> %s" % hexs(frame))
            return
        self.dev.ctrl_transfer(0x21, 0x09, 0x0200, IFACE, frame + bytes(REPORT_LEN - len(frame)), timeout=1000)

    def _frame(self, ctrl, body=b""):
        if ctrl in FORBIDDEN:
            raise SystemExit("refusing CTRL 0x%02X" % ctrl)
        return bytes([0xA5, 0x5A, ctrl, len(body)]) + bytes(body) + b"\x16"

    def read(self, ctrl):
        """Reply body (after LEN), or None. GET_REPORT re-serves stale data, so CTRL must match."""
        frame = self._frame(ctrl)
        if self.dry:
            self._out(frame)
            return None
        for _ in range(5):
            self._out(frame)
            time.sleep(0.010)
            try:
                r = bytes(self.dev.ctrl_transfer(0xA1, 0x01, 0x0100, IFACE, REPORT_LEN, timeout=1000))
            except Exception:
                continue
            if r[:2] == b"\xA5\x5A" and r[2] == ctrl:
                return r[4: 4 + r[3]]
        return None

    def params(self, node):
        b = self.read(node)
        if not b or b[0] != 0xFF:
            return None
        rest = b[1:]
        return list(struct.unpack("<%dh" % (len(rest) // 2), rest[: len(rest) // 2 * 2]))

    def set(self, node, idx, val):
        # Single-parameter write; the firmware does not reply, so verify by reading back.
        self._out(self._frame(node, bytes([idx]) + struct.pack("<h", int(val))))
        time.sleep(0.010)

    def write_body(self, ctrl, body):
        self._out(self._frame(ctrl, body))
        time.sleep(0.010)


def status(am):
    g = am.params(GAIN_NODE)
    n = am.params(NOISE_NODE)
    d = am.read(DAC0)
    if g:
        print("mute    : %s" % ("ON" if g[1] else "off"))
        print("gain    : %+.2f dB" % (g[2] / 100))
    if n:
        print("noise   : %s  (threshold %.1f dB, ratio %d, attack %d, release %d)"
              % ("on" if n[0] else "off", n[1] / 100, n[2], n[3], n[4]))
    if d:
        vol = struct.unpack_from("<H", d, 7)[0]
        print("monitor : %d%%" % round(vol * 100 / 4095))
    e = am.params(EQ_NODE)
    if e:
        for name, k in EQ_BANDS.items():
            b = eq_band(k)
            en, ftype, f0, _, gain = e[b:b + 5]
            if not (e[0] and en):
                print("%-8s: off" % name)
            elif ftype == EQ_HIGHPASS:
                print("%-8s: on, %d Hz" % (name, f0))
            else:
                print("%-8s: %+.1f dB at %d Hz" % (name, gain / 256, f0))


def number(text, usage, cast=float):
    try:
        return cast(text)
    except (TypeError, ValueError):
        sys.exit(usage)


def eq_band(k):
    return 3 + (k - 1) * 5


def set_eq_band(am, name, enable, ftype=0, f0=1000, q=1.0, gain_db=0.0):
    base = eq_band(EQ_BANDS[name])
    vals = [(base + 1, ftype), (base + 2, f0), (base + 3, int(round(q * 1024))),
            (base + 4, int(round(gain_db * 256))), (base, 1 if enable else 0)]
    for idx, v in vals:
        am.set(EQ_NODE, idx, v)
    am.set(EQ_NODE, 0, 1)
    if am.dry:
        return
    p = am.params(EQ_NODE)
    ok = p is not None and all(p[i] == v for i, v in vals) and p[0] == 1
    print("%s: %s" % (name, "ok" if ok else "NOT applied (read back %s)" % (p and p[base:base + 5],)))


def check(am, node, idx, want, label):
    if am.dry:
        return
    p = am.params(node)
    ok = p is not None and len(p) > idx and p[idx] == want
    print("%s: %s" % (label, "ok" if ok else "NOT applied (read back %s)" % (p,)))


def apply(path, dry):
    import os
    import shlex
    import subprocess
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        sys.exit("no config: %s" % path)
    # The mic may still be initialising right after it enumerates.
    if not dry:
        for _ in range(20):
            try:
                if Am8(False).read(0x00) is not None:
                    break
            except SystemExit:
                pass
            time.sleep(0.5)
    rc = 0
    for line in open(path):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        args = shlex.split(line)
        if args[0] == "apply":
            continue
        print("$ am8-cli.py %s" % line, flush=True)
        rc |= subprocess.call([sys.executable, os.path.abspath(__file__)] + args + (["--dry"] if dry else []))
    sys.exit(rc)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "apply":
        rest = [x for x in sys.argv[2:] if x != "--dry"]
        apply(rest[0] if rest else "~/.config/am8.conf", "--dry" in sys.argv)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["status", "mute", "gain", "noise", "monitor",
                                    "lowcut", "clarity", "bass"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--dry", action="store_true", help="print frames, send nothing")
    a = ap.parse_args()
    am = Am8(a.dry)

    if a.cmd == "status":
        status(am)

    elif a.cmd == "mute":
        if a.args not in (["on"], ["off"], ["toggle"]):
            sys.exit("mute on|off|toggle")
        if a.args[0] == "toggle":
            g = am.params(GAIN_NODE)
            if g is None:
                sys.exit("cannot read mute state")
            val = 0 if g[1] else 1
        else:
            val = 1 if a.args[0] == "on" else 0
        am.set(GAIN_NODE, 1, val)
        check(am, GAIN_NODE, 1, val, "mute %s" % ("on" if val else "off"))

    elif a.cmd == "gain":
        db = number(a.args[0] if a.args else None, "gain DB")
        if not -90 <= db <= 12:
            sys.exit("gain must be -90..+12 dB")
        val = int(round(db * 100))
        am.set(GAIN_NODE, 2, val)
        check(am, GAIN_NODE, 2, val, "gain %+.2f dB" % db)

    elif a.cmd == "noise":
        if a.args == ["off"]:
            am.set(NOISE_NODE, 0, 0)
            check(am, NOISE_NODE, 0, 0, "noise off")
        else:
            if len(a.args) == 1 and a.args[0] in NOISE_PRESETS:
                thr, ratio = NOISE_PRESETS[a.args[0]]
            elif len(a.args) == 2:
                usage = "noise THRESHOLD_DB RATIO"
                thr, ratio = number(a.args[0], usage), number(a.args[1], usage, int)
            else:
                sys.exit("noise off|low|mid|high  or  noise THRESHOLD_DB RATIO")
            if not -90 <= thr <= 0 or not 1 <= ratio <= 100:
                sys.exit("threshold -90..0 dB, ratio 1..100")
            am.set(NOISE_NODE, 1, int(round(thr * 100)))
            am.set(NOISE_NODE, 2, ratio)
            am.set(NOISE_NODE, 0, 1)
            check(am, NOISE_NODE, 1, int(round(thr * 100)), "noise threshold %.1f dB" % thr)
            check(am, NOISE_NODE, 2, ratio, "noise ratio %d" % ratio)
            check(am, NOISE_NODE, 0, 1, "noise on")

    elif a.cmd == "lowcut":
        if not a.args or a.args[0] not in ("on", "off"):
            sys.exit("lowcut off|on [HZ]")
        hz = number(a.args[1], "lowcut on HZ", int) if len(a.args) > 1 else 100
        if not 20 <= hz <= 500:
            sys.exit("lowcut frequency 20..500 Hz")
        set_eq_band(am, "lowcut", a.args[0] == "on", EQ_HIGHPASS, hz, 0.707)

    elif a.cmd in ("clarity", "bass"):
        lim = (0, 9) if a.cmd == "clarity" else (-12, 9)
        if not a.args:
            sys.exit("%s off|DB" % a.cmd)
        if a.args[0] == "off":
            set_eq_band(am, a.cmd, False)
        else:
            db = number(a.args[0], "%s off|DB" % a.cmd)
            if not lim[0] <= db <= lim[1]:
                sys.exit("%s %d..%+d dB" % (a.cmd, lim[0], lim[1]))
            if a.cmd == "clarity":
                set_eq_band(am, "clarity", True, EQ_PEAK, 3000, 1.0, db)
            else:
                set_eq_band(am, "bass", True, EQ_LOWSHELF, 150, 0.707, db)

    elif a.cmd == "monitor":
        pct = number(a.args[0] if a.args else None, "monitor PERCENT")
        if not 0 <= pct <= 100:
            sys.exit("monitor 0..100")
        d = am.read(DAC0)
        if d is None:
            if not a.dry:
                sys.exit("DAC0 did not answer")
            return
        if d[0] != 0xFF or len(d) < 11:
            sys.exit("unexpected DAC0 reply")
        vol = round(pct * 4095 / 100)
        body = bytearray(d)
        struct.pack_into("<HH", body, 7, vol, vol)
        am.write_body(DAC0, bytes(body))
        if not a.dry:
            d2 = am.read(DAC0)
            ok = d2 is not None and struct.unpack_from("<H", d2, 7)[0] == vol
            print("monitor %d%%: %s" % (pct, "ok" if ok else "NOT applied"))


if __name__ == "__main__":
    main()
