# am8 — fifine AM8 control for Linux

Command-line control of the **fifine AmpliGame AM8** USB microphone (`3142:a010`, MVsilicon chip)
on Linux: mute, digital gain, noise gate and a mic EQ, applied automatically on plug-in.

It talks to the microphone's vendor HID interface (interface 4) with MVsilicon's ACP protocol,
the same channel the vendor's tuning tools use. No kernel driver is needed. Protocol details and
research notes are in [PROTOCOL.md](PROTOCOL.md).

> The RGB lighting **cannot** be controlled over USB on this firmware. See
> [PROTOCOL.md](PROTOCOL.md#rgb-lighting).

## Requirements

- Python 3 with `pyusb` (`apt install python3-usb` or `pip install pyusb`)
- libusb 1.0

## Usage

```
am8-cli.py status
am8-cli.py mute on|off|toggle
am8-cli.py gain DB                    digital mic gain, -90..+12 dB (stock +6)
am8-cli.py noise off|low|mid|high     noise gate (low = stock: -45 dB, ratio 3)
am8-cli.py noise THRESHOLD_DB RATIO   custom gate, e.g. noise -35 4
am8-cli.py lowcut off|on [HZ]         high-pass filter, default 100 Hz
am8-cli.py clarity off|DB             presence boost at 3 kHz, 0..+9 dB
am8-cli.py bass off|DB                low shelf at 150 Hz, -12..+9 dB
am8-cli.py monitor PERCENT            headphone volume (the hardware knob overrides it)
am8-cli.py apply [FILE]               run every line of FILE (default ~/.config/am8.conf)
```

Add `--dry` to any command to print the USB frames without sending them. Every write is
verified by reading the value back (`ok` / `NOT applied`).

Example:

```
$ python3 am8-cli.py status
mute    : off
gain    : +6.00 dB
noise   : on  (threshold -45.0 dB, ratio 3, attack 5, release 500)
monitor : 100%
lowcut  : on, 150 Hz
clarity : +4.0 dB at 3000 Hz
bass    : +3.0 dB at 150 Hz
```

### Notes

- Settings live in the microphone's RAM and are lost when it is unplugged. Use `apply` and the
  systemd unit below to restore them on every plug-in.
- The mic gain knob is analog and invisible to the firmware; `gain` acts on top of it.
- Software `mute` does not update the mute button's internal state or its LED. After a software
  mute, the button needs two presses (red, then green) to unmute.

## Installation

```

cp am8-cli.py ~/.local/bin/am8-cli; chmod +x ~/.local/bin/am8-cli

# access without sudo, and a hook that applies defaults on plug-in
sudo cp 70-fifine-am8.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger --attr-match=idVendor=3142

# defaults
cp am8.conf ~/.config/am8.conf

# apply defaults on every plug-in
mkdir -p ~/.config/systemd/user
cp am8-defaults.service ~/.config/systemd/user/
systemctl --user daemon-reload
```

The unit runs `~/.local/bin/am8-cli`; edit `ExecStart` if the script lives elsewhere.
Check it with `journalctl --user -u am8-defaults`.

`am8.conf` holds one `am8-cli.py` command per line:

```
lowcut on 150
bass 3
clarity 4
noise low
```

The udev rule must sort before `73-seat-late.rules` (which turns the `uaccess` tag into an ACL),
hence the `70-` prefix. It also sets group `plugdev`.

## Safety

The ACP channel also carries commands that can brick the chip. `am8-cli.py` refuses to send them:

| CTRL | Why |
|---|---|
| `0x11` | writes SPI-flash sectors |
| `0xFE` | OTA trigger on stock MVsilicon firmware (reboots into the flash bootloader) |
| `0xFB`, `0xFD`, `0xFF` | out-of-bounds handling / encryption lock |
| `0xA0`, `0xA1`, `0xA3` | voice changer / auto-tune; enabling them overloads the mic and makes it re-enumerate |

Never write the 8-byte Feature report: `SET_REPORT(Feature)` with `0x55`/`0xAA` arms the upgrade
flag, and the next `GET_REPORT(Feature)` jumps into the bootloader.

If anything goes wrong, unplug and replug the microphone; all settings return to stock.
