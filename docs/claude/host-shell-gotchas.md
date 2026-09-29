# Host shell gotchas (Claude sessions run as root on the live server)

### Host Shell Gotchas (sessions run as root on the live server)

- The interactive zsh profile fetches the public IP at startup and ships broken `localip`/`grep`/`ip` shell functions (`FUNCNEST` errors): shell commands intermittently hang ~2 min, get killed (exit 137/143), or have their output silently eaten. **Workaround: wrap commands in `bash -c '...'`** (bash doesn't inherit the zsh functions), or read `/sys`/files directly (e.g. bridge members via `/sys/class/net/<bridge>/brif/`). **The `grep` function's precise failure mode: it does NOT prefix recursive matches with `./`** the way real `grep -r`/`grep -rn` does. Any exclusion filter written as `grep -v '^./…'` therefore matches nothing and silently passes everything through unfiltered, and any count derived from its output (`wc -l` on filtered results) comes out wrong in the same silent way — there is no error, just a clean-looking result that is not clean. This produced a false "no dead references" verification in an earlier phase and two wrong suite counts during the console-package-hote plan. **Use `command grep`, or wrap the whole pipeline in `bash -c '...'`** — the zsh function only shadows the bare `grep` invocation.
- `ot-ctl` (OTBR) output lines end with `\r` (CRLF) — strip it before string comparisons in scripts, or `case "leader"` never matches.
- **`systemctl` does NOT work from a Claude session (found 2026-08-05).** The session runs in its own **PID namespace** (`readlink /proc/self/ns/pid` ≠ `/proc/1/ns/pid`; `systemd-detect-virt` → `container-other`) while sharing the host mount namespace. systemd authenticates peers with `SO_PEERCRED`, which is meaningless across PID namespaces, so every call fails with **`Failed to connect to system scope bus via local transport: No data available`** — including `env -i` and with the sandbox disabled. **This fails silently for query subcommands** (`systemctl show -p X` just prints nothing), so a check that "returns empty" may mean *unreachable*, not *unset*. Everything else is the real host: `journalctl`, `/sys`, `/proc`, `/dev/mem`, `lspci`, and **writes to `/sys` all work**.

**WORKAROUND — drive systemd over the D-Bus system bus instead (verified 2026-08-05).** Only systemctl's *private socket* transport is broken; `dbus-daemon` authenticates by **UID**, which is namespace-independent, so `/run/dbus/system_bus_socket` works fine. Note `systemctl` cannot be coaxed onto it — as root it always prefers the private socket, and `DBUS_SYSTEM_BUS_ADDRESS` does **not** override that. Call the API directly:

```bash
M="--system --print-reply --dest=org.freedesktop.systemd1 /org/freedesktop/systemd1 org.freedesktop.systemd1.Manager"
dbus-send $M.Reload                                                    # daemon-reload
dbus-send $M.EnableUnitFiles array:string:"foo.service" boolean:false boolean:false
dbus-send $M.StartUnit  string:"foo.service" string:"replace"          # also RestartUnit/StopUnit
dbus-send $M.GetUnitFileState string:"foo.service"                     # enabled/disabled
# ActiveState/SubState: GetUnit → org.freedesktop.DBus.Properties.Get on the returned path
```

**Do NOT hand these to the user as `! systemctl …`** — the `!` prefix runs inside the same Claude session, so it hits the exact same namespace error. Only a genuine host shell (SSH/console) would work, and the D-Bus route makes that unnecessary.
