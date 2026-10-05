# fifine AM8 (3142:a010) — vendor HID protocol

Findings from black-box tests on the device, the MVsilicon BP10xx SDK
([leadercxn/bp1048_sdk_v0.1.12](https://github.com/leadercxn/bp1048_sdk_v0.1.12)), the FIFINE SC3
reverse-engineering notes ([dank0i/sc3](https://github.com/dank0i/sc3)), an ACPWorkbench template
of the same firmware family
([stpf99/MVSilicon-BP1048B2_linux_fx_gui](https://github.com/stpf99/MVSilicon-BP1048B2_linux_fx_gui))
and the fifine Genie 1.6.77 Windows app.

Confidence markers: **[verified]** tested on the device, **[SDK]** read from SDK source,
**[inferred]** matched by parameter layout but not confirmed.

## Device

```
Bus 001 Device 021: ID 3142:a010 MV-SILICON fifine Microphone   (bcdDevice 1.00, serial 20190808)
  if 0..2  Audio (UAC1)                              snd-usb-audio
  if 3     HID consumer control (volume/mute keys)   usbhid, EP 0x81 IN
  if 4     HID vendor, usage page 0xFF00, usage 0x55AA, no endpoints, no kernel driver
```

Interface 4 report descriptor: Input 256 bytes, Output 256 bytes, Feature 8 bytes, no report IDs.

## Transport [verified]

| Direction | USB request |
|---|---|
| host → device | `SET_REPORT` Output: `bmRequestType 0x21, bRequest 0x09, wValue 0x0200, wIndex 4`, 256 bytes |
| device → host | `GET_REPORT` Input: `bmRequestType 0xA1, bRequest 0x01, wValue 0x0100, wIndex 4`, 256 bytes |

On Windows the same goes through hidapi (`hid_write` / `hid_get_input_report`).

## ACP framing [verified]

```
request  A5 5A CTRL LEN body[LEN] 16      LEN = 0 -> read
reply    A5 5A CTRL LEN body[LEN] 16
```

- The firmware builds a reply only for reads. It copies the reply into a static buffer that
  `GET_REPORT` returns **[SDK]**. A write leaves only a 1-byte ack, so read about 10 ms after the
  request, retry a few times, and accept the reply only if `CTRL` matches.
- Unknown `CTRL` values are silently dropped (no reply). Swept `0x0F`–`0x7F`: none answer.
- Node replies: `FF` (all-parameters selector) followed by `int16` little-endian values; value 0
  is the enable flag.
- Single-parameter write: `A5 5A <node> 03 <index> <int16 LE> 16`, where `index` counts from the
  enable flag (0). No reply; verify by reading back.
- Writes go to live registers and are lost on power cycle.

## System blocks

| CTRL | Block | Reply / notes |
|---|---|---|
| `0x00` | firmware version | `42 00 07 01 02 2B 02 02 17 02 02 42 35 01` |
| `0x01` | system control | 7 fields [SDK]: mode (standby/sleep), reset, sample-rate enable, sample rate, MCLK enable, MCLK source, default set |
| `0x02` | RAM/MIPS stats | changes constantly |
| `0x03` | PGA0 | answers |
| `0x04` | ADC0 | answers |
| `0x06` | PGA1 (mic analog) | `FF 00 00 00 00 01 00 09 00`; layout differs from the template |
| `0x07` | ADC1 (mic digital) | `enable 3, mute 0, L 4096, R 4096, sample rate 7, ...` |
| `0x08` | AGC1 | answers |
| `0x09` | DAC0 (headphones) | body offsets 7/9 = L/R volume 0..4095, driven by the headphone knob **[verified]** |
| `0x05`, `0x0A`–`0x0E` | AGC0, DAC1, I2S, SPDIF, GPIO | no reply (absent) |
| `0x80` | effect list | index 0: auto-tune parameter descriptor; 1: `01 00 01 07 00 01`; 2: first 241 bytes of the effect graph (see below); 3+ and multi-byte bodies return the same chunk |
| `0xFC` | user tag | ASCII `HunXiang` (the firmware vendor's tag) |

Effect graph (index 2): header `73 0B 03 05 02 02 07 "Karaoke"`, then text XOR-ed with the
repeating key `"MVSI"`:

```
auto_tune = auto_tune0;
compander = compander0(BITS=24);
downmix_2to1 = downmix_2to10(BITS=24);
drc = drc1(BITS=24), drc0(BITS=24), drc2(BITS=24);
echo = echo0(BITS=24);
eq = eq0(BITS=24), eq1(BITS=24), eq7(BITS=24), eq4(BITS=24 ...    (truncated)
```

## Effect nodes `0x81`–`0xB1` (49 nodes)

Reading past `0xB1` gives no reply. On the SC3, reading past the last node wedged the channel
until replug, so do not sweep beyond it.

| Node | Values (enable first) | Meaning | |
|---|---|---|---|
| `0x95` | `1, mute, 600` | **mic gain control**: mute flag, gain in centi-dB (+6 dB) | [verified] |
| `0x90` | `0, -4500, 3, 5, 500` | **mic noise gate**: threshold centi-dB, ratio, attack, release | [verified] |
| `0x99` | 53 | **mic EQ**, all 10 filters unused in stock firmware | [verified] |
| `0xAD` | 53 | mic EQ (second instance) | [verified] |
| `0xAE` | 27 | mic DRC; enabling it makes speech very quiet, layout not understood | [verified] |
| `0x91` | `1, level` | silence detector; value follows the mic signal | [verified] |
| `0x84` | `0, -4500, 3, 5, 500` | noise gate, other path (no audible effect on the mic) | [verified] |
| `0x8B`, `0x8D`, `0x9B`, `0xA4`, `0xA7` | 53 | EQs, other paths | [verified] |
| `0x8C`, `0x9D` | 27 | DRCs (`0x8C` slightly muffles the mic) | [verified] |
| `0xA9` | `0, 12000, 1, 2500, 60, 65, 5000, 60` | plate reverb; inaudible, and enable=0 is not accepted | [verified] |
| `0xA0`, `0xA1` | `0, 200, 130` | voice changer (pitch %, formant %); enabling overloads the mic | [verified] |
| `0xA3` | `0, 4, 0, 2` | auto-tune (key, snap, accuracy); produces hiss on speech | [verified] |
| `0x81`, `0x83`, `0x8F`, `0x9C`, `0xAB` | `1, 0, gain` | gain controls (0 / -1 / -3 / 0 / +1 dB) | [inferred] |
| `0x9F`, `0xA2`, `0xA6`, `0xAA`, `0xAC`, `0xB0`, `0xB1` | `1, 1, 0` | gain controls, muted (likely PC/mix paths) | [inferred] |
| `0x87` | `0, 100, 35, 1` | virtual bass | [inferred] |
| `0x88` | `0, 50` | 3D | [inferred] |
| `0x89` | `0, 1000, 80, 80` | harmonic exciter | [inferred] |
| `0x93` | `0, 1` | howling suppressor | [inferred] |
| `0x8E`, `0x98` | `1` | enable-only (downmix) | [inferred] |
| `0x82`, `0x85`, `0x86`, `0x8A`, `0x92`, `0x94`, `0x96`, `0x97`, `0x9A`, `0x9E`, `0xA5`, `0xA8`, `0xAF` | | unknown | |

### EQ layout [SDK + verified]

```
[0] enable  [1] pregain  [2] calc type
then 10 filters x (enable, type, f0 Hz, Q in Q6.10, gain dB in Q8.8)
type: 0 peaking, 1 low shelf, 2 high shelf, 3 low pass, 4 high pass, 5 band pass, 6 notch
```

`am8-cli.py` uses filters 1–3 of `0x99`: high-pass (lowcut), peaking at 3 kHz (clarity),
low shelf at 150 Hz (bass).

## fifine Genie commands do not work

Genie 1.6.77 contains AM8 tables and a (fully commented-out) plugin that uses
`A5 5A FE <len> <cmd> <args> 16`, for example LightSwitch = `A5 5A FE 02 00 <mode> 16` and
state = `A5 5A FE 00 16`. This firmware has no `0xFE` handler: no reply, no effect.
In the stock MVsilicon SDK, `0xFE` is the OTA trigger, so these frames must not be sent.
fifine lists the AM8 as only partially supported by Genie.

## RGB lighting

Not reachable over USB on this firmware:

- The SDK drives keys and LEDs from the application message queue; the ACP dispatcher never
  touches them, and there is no GPIO (`0x0E`) handler **[SDK]**.
- Polling every answering block and node while cycling the LED modes shows no change; neither
  does the Feature report **[verified]**.
- A software mute changes the audio but not the mute button's LED **[verified]**.
- There is no flash or memory read over ACP, HID or vendor requests **[SDK]**, so the firmware
  cannot be dumped over USB to patch the LED logic.

Remaining options are hardware: unplug the LED connector, or dump the SPI flash with a SOIC-8 clip
and patch the firmware.

## Feature report and bootloader [SDK]

- `GET_REPORT(Feature)` normally returns `00 01 00 03 04 00 08 00` on this device.
- `SET_REPORT(Feature)` with first byte `0x55` (signature mismatch) or `0xAA` arms `pc_upgrade`.
  The next `GET_REPORT(Feature)` replies `0x55` and jumps into the flash bootloader. Any other
  first byte clears the flag.
- `CTRL 0x11` with `body[0] = 1` writes a 4 KiB-aligned SPI-flash sector (read-erase-patch-write).
