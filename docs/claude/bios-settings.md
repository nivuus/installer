# BIOS settings from Linux: `nivuus-bios`

`scripts/bios/` lists and changes the firmware setup settings of the running
host without entering the BIOS menu. Deployed as
`/usr/local/lib/nivuus/bios/` with `/usr/local/sbin/nivuus-bios` a symlink to
`nivuus_bios.py`. Tests: `scripts/tests/test_nivuus_bios.py` (in
`installer/Makefile`'s Python loop).

## Why a tool of our own (2026-11-08)

* **`uefisettings` (LinuxBoot) does not reach this board's menus.** It reads
  the HII database the firmware publishes at runtime (`HiiDB` efivar). ASUS
  publishes only a fraction of it: 92 nameless questions, Secure Boot and a few
  CPU items, **no Ai Tweaker, no Advanced**. Measured with v0.1.6.
* `setup_var` (S-trace, datasone) writes a byte at an offset with no
  validation. SCELNX (AMI) is proprietary and needs a kernel module.

So the source of truth is the **Setup module of the vendor image of the exact
running BIOS version**, decompiled by IFRExtractor-RS. Everything else is
plain Python reading `/sys/firmware/efi/efivars`.

## Getting the menu of a BIOS version

Run once per BIOS version (the tool refuses to work without the menu of the
running version, filed under `/var/lib/nivuus/bios/<board>-<version>/`):

```sh
# vendor capsule of the running version (dmidecode -s bios-version), then:
uefiextract IMAGE.CAP 899407D7-99FE-43D8-9A21-79EC328CAC21 -o setup -m body -t 10
cd setup/... && ifrextractor body.bin verbose      # -> body.bin.0.0.en-US.uefi.ifr.txt
nivuus-bios import body.bin.0.0.en-US.uefi.ifr.txt
```

`899407D7-…` is the AMI Setup module; `-t 10` keeps only its PE32 section
(without it UEFIExtract finds several sections and fails with error 5). Tools:
LongSoft UEFIExtract (NE A76) and IFRExtractor-RS (1.6.1). On the reference
host the ASUS 4005 image and both tools live in `/root/work/bios/`.

## Usage

```sh
nivuus-bios list [--filter TEXT] [--all] [--json]   # 3627 visible of 6094 on 4005
nivuus-bios show "Restore AC Power Loss"            # or by id: show 0x2bb2
nivuus-bios set  "Restore AC Power Loss" "Power On" # asks; --yes to skip
nivuus-bios restore /var/lib/nivuus/bios/backups/<stamp>
```

A change takes effect at the next boot, like a change saved in the menu.

## What it checks, and why each check exists

* **Structure from the bytes, not the indentation.** ASUS's compiler sets the
  scope bit on `QuestionRef1`; the extractor prints it as extra indentation
  inside expressions, so any indentation-based parser misreads every
  condition that uses it.
* **Conditions evaluated like the setup browser** (`suppressif`,
  `grayoutif`, `disableif`, per question and per option), with three-valued
  logic. A condition that reads a variable Linux cannot see is UNKNOWN and
  **blocks the write** — unknown is never folded into "visible".
* **Value validation**: option offered right now, checkbox words,
  numeric min/max/step; a checkbox keeps the other bits of its byte.
* **Variable size == size declared by the menu**, or the menu is not the
  running BIOS's and nothing is written.
* **Backup of the variable, one `write()`, read back**: a firmware that drops
  the write is reported, not assumed.
* Never creates a variable.

## Limits (named, not hidden)

* **Typed-in fields (IFR `String`) are refused.** ASUS voltages are typed as
  text and converted by firmware code (question flag CALLBACK) into a hidden
  numeric; writing the text raw would bypass that conversion. They are still
  *listed* with their current value.
* **Callback questions are written but flagged**: the firmware code the menu
  runs on change does not run here. Example: Restore AC Power Loss has
  `QuestionFlags 0x14`; its effect must be checked after the next boot.
* 177 values read `?` on 4005: their variables are not exposed to Linux.

## Facts it established on the reference host

* **`in10` on the nct6798 is the memory controller voltage (VDD2), not DRAM
  VDD.** BIOS 4005 gives `Memory Controller Voltage` a standard value of
  1.116 V (`in10` read 1.12 V at JEDEC 4800); it is on Auto and the board
  raises it to ~1.35 V with XMP. DRAM VDD/VDDQ are 1.25 V on all four PMICs,
  the profile value.
