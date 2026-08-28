# Câblage hôte de la console (phase 2b) — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** rendre le cycle de vie de la VM fonctionnel depuis une simple installation — le dispatcher libvirt, la bascule GPU, les forward-ports et le réveil à la demande — sans toucher à `windows-guest/`.

**Architecture:** `console/hooks/install.py` pose aujourd'hui sept choses et le dispatcher libvirt n'en fait pas partie, si bien que les deux wrappers CPU qu'il écrit ne s'exécutent jamais. Cette phase complète le placement, rapatrie le seul script du cycle qui n'était versionné nulle part (`vm-idle-shutdown.sh`), écrit les unités systemd de réveil qui n'existaient qu'en production, et confie l'**armement** de ces unités à `activate` plutôt qu'à `install` — poser un fichier et l'activer sont deux phases différentes du contrat, et armer un socket WAN pour une VM qui n'existe pas encore serait une surface d'attaque gratuite.

**Tech Stack:** Python 3.11 (stdlib seule), bash, unités systemd, hooks libvirt.

**Spec:** `docs/superpowers/specs/2026-08-27-decoupage-installer-console-design.md`

## Global Constraints

- **`console/` n'importe rien de `installer/`.** C'est l'autonomie qui lui permet de tourner sur la cible et sur une Debian qui n'a jamais vu cet installateur. Un littéral dupliqué est préférable à un import.
- **Tout ce qu'un hook libvirt exécute vit sous `/etc/libvirt/hooks/`** ou dans un répertoire `PUx` (`/bin`, `/sbin`, `/usr/bin`, `/usr/sbin`). Jamais `/usr/local/sbin`. Le profil AppArmor de libvirtd accorde `/etc/libvirt/hooks/** rmix` ; une copie ailleurs meurt au démarrage de la VM avec un `bad interpreter: Permission denied` trompeur et **aucune** ligne DENIED dans dmesg.
- **L'activation d'une unité se fait par lien symbolique**, jamais par `systemctl enable`. `systemctl` échoue silencieusement en environnement contraint (namespace PID) ; un lien existe ou lève.
- **Un lien d'activation doit pointer vers un fichier qui existe.** Neuf entrées `winvm-proxy-*.socket` traînent en production dans `sockets.target.wants/` en tant que **fichiers ordinaires** — systemd les ignore avec `is not a symlink, ignoring`. Créer un lien pendant, ou un fichier ordinaire, revient à ne rien activer.
- **Commentaires de code en anglais.** Messages destinés à l'opérateur et messages de commit en **français sans accents** (convention de la phase 2a).
- **`command grep`, jamais `grep` nu.** Le profil zsh de cet hôte livre une fonction `grep` cassée qui ne préfixe pas les correspondances récursives par `./` : tout filtre `grep -v '^./…'` laisse alors tout passer, silencieusement. Elle a produit deux comptages faux pendant la phase 2a.
- **La suite complète doit rester verte** : `cd installer && make test-packages PYTHON="$PY"`, où `$PY` est un interpréteur disposant de `pydantic` et `jinja2`. Le `python3` système de cet hôte ne les a pas, et l'agrégateur s'arrête au **premier** échec — un `python3` nu affiche donc 8 suites vertes et masque les 18 autres. Poser `PY` une fois en début de session : `PY=/tmp/user/0/claude-0/-home-mallanic-Projects-Nivuus-packages-installer/bfd96116-6ef4-4bd1-8191-ca092cfaf289/scratchpad/venv/bin/python`, ou tout venv équivalent. **26 suites aujourd'hui** ; toute suite ajoutée par ce plan entre dans la cible `test-packages` du `Makefile`, sans quoi elle n'est jamais exécutée.
- **Un fichier au-delà de ~200 lignes est un signal, pas une interdiction** (18 fichiers `.py` suivis le dépassent déjà).

## Ce que cette phase ne fait PAS

`activate` ne construit toujours pas la VM. Le média Windows, `virsh define` et le provisionnement sont la phase 2c. À la fin de ce plan, la console sait gérer une VM `Windows` **si elle existe** ; elle ne sait pas encore la créer.

## Structure des fichiers

| Fichier | Responsabilité | Sort |
| --- | --- | --- |
| `console/host/vm-idle-shutdown.sh` | Rendort la VM inactive, ré-arme les sockets de réveil, ré-assère `release` du partitionnement CPU | **Créé** — rapatrié depuis `/usr/local/sbin/` où il n'existait que déployé |
| `console/host/systemd/vm-idle-shutdown.{service,timer}` | Le déclencheur périodique (15 min au boot, puis 10 min) | **Créés** — rapatriés de production |
| `console/host/systemd/vm-trigger-{47984,47989}.{socket,service}` | Réveil à la demande, socket-activation | **Créés** — n'existaient qu'en production |
| `console/host/systemd/vm-trigger-no-start-limit.conf` | Drop-in `StartLimitIntervalSec=0`, partagé par les deux services | **Créé** |
| `console/host/libvirt/hooks/qemu.d/Windows/**/0*hugepages*.sh` | Rien : trois talons de deux lignes | **Supprimés** |
| `console/hooks/install.py` | Placement sur la cible | **Étendu** — dispatcher, hooks GPU, paire `rules.sh`, script d'inactivité, sept unités |
| `console/hooks/activate.py` | Armement au premier démarrage | **Réécrit** — liens symboliques idempotents, plus le talon actuel |
| `scripts/tests/test_console_host_files.py` | Le dépôt porte bien le cycle complet, et plus de code mort | **Créé** |
| `scripts/tests/test_console_wake_units.py` | Les unités disent ce que le gate attend | **Créé** |
| `scripts/tests/test_console_install.py` | Artefacts sous une racine jetable | **Étendu** |
| `scripts/tests/test_console_activate.py` | Liens d'activation, idempotence, refus des liens pendants | **Créé** |

---

### Task 1 : rapatrier le script d'inactivité, supprimer les talons morts

**Files:**
- Create: `console/host/vm-idle-shutdown.sh` (copie de `/usr/local/sbin/vm-idle-shutdown.sh`)
- Create: `console/host/systemd/vm-idle-shutdown.service`
- Create: `console/host/systemd/vm-idle-shutdown.timer`
- Delete: `console/host/libvirt/hooks/qemu.d/Windows/started/begin/00-set-hugepages.sh`
- Delete: `console/host/libvirt/hooks/qemu.d/Windows/stopped/end/00-hugepages-fix.sh`
- Delete: `console/host/libvirt/hooks/qemu.d/Windows/stopped/end/hugepages-reset.sh`
- Test: `scripts/tests/test_console_host_files.py`

**Interfaces:**
- Consumes: rien.
- Produces: `console/host/vm-idle-shutdown.sh` et `console/host/systemd/` — la tâche 3 les place.

**Pourquoi les talons partent.** Les trois fichiers contiennent exactement ceci et rien d'autre :

```bash
#!/bin/bash
# Hugepages hook removed (static sysctl configuration used)
```

Les déployer ne ferait rien, et les documenter comme « à câbler en 2b » a déjà induit le README en erreur une fois. Le pool de hugepages est fixé par `vm.nr_hugepages` dans `/etc/sysctl.d/50-virsh.conf`, que le moteur écrit depuis le `hugepages-mib` du manifeste.

- [ ] **Step 1: écrire le test qui échoue**

Créer `scripts/tests/test_console_host_files.py` :

```python
#!/usr/bin/env python3
"""The repository carries the whole VM lifecycle, and carries no dead code.

Two failures this guards against, both already observed in this project:
a script that exists only as a deployed file on one host and is lost the
day that host is reinstalled (handle-vm-start.sh, until 2026-08-24), and
placeholder hooks kept around long enough that documentation starts
promising them (the three hugepage stubs).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CONSOLE = os.path.join(ROOT, "console")

failures = []


def check(label, condition):
    if not condition:
        failures.append(label)


# The idle half of wake-on-demand must be versioned, not merely deployed.
idle = os.path.join(CONSOLE, "host", "vm-idle-shutdown.sh")
check("vm-idle-shutdown.sh is versioned", os.path.isfile(idle))
if os.path.isfile(idle):
    head = open(idle).readline()
    check("vm-idle-shutdown.sh starts with a shebang", head.startswith("#!"))

for unit in ("vm-idle-shutdown.service", "vm-idle-shutdown.timer"):
    check(f"{unit} is versioned",
          os.path.isfile(os.path.join(CONSOLE, "host", "systemd", unit)))

# No placeholder hooks: a two-line no-op is worse than an absent file,
# because documentation reads the filename and promises behaviour.
hooks_dir = os.path.join(CONSOLE, "host", "libvirt", "hooks", "qemu.d")
for dirpath, _dirnames, filenames in os.walk(hooks_dir):
    for name in filenames:
        path = os.path.join(dirpath, name)
        body = [l for l in open(path).read().splitlines()
                if l.strip() and not l.strip().startswith("#")
                and not l.startswith("#!")]
        rel = os.path.relpath(path, ROOT)
        check(f"{rel} does something", bool(body))

if failures:
    for item in failures:
        print(f"FAIL - {item}")
    sys.exit(1)
print("OK - the repository carries the full lifecycle and no placeholder hooks")
```

- [ ] **Step 2: le lancer pour vérifier qu'il échoue**

Run: `python3 scripts/tests/test_console_host_files.py`
Expected: FAIL — `vm-idle-shutdown.sh is versioned`, les deux unités, et les trois talons signalés comme ne faisant rien.

- [ ] **Step 3: rapatrier et supprimer**

```bash
mkdir -p console/host/systemd
cp /usr/local/sbin/vm-idle-shutdown.sh console/host/vm-idle-shutdown.sh
chmod 755 console/host/vm-idle-shutdown.sh
cp /etc/systemd/system/vm-idle-shutdown.service console/host/systemd/
cp /etc/systemd/system/vm-idle-shutdown.timer   console/host/systemd/
chmod 644 console/host/systemd/vm-idle-shutdown.service \
          console/host/systemd/vm-idle-shutdown.timer
git rm console/host/libvirt/hooks/qemu.d/Windows/started/begin/00-set-hugepages.sh \
       console/host/libvirt/hooks/qemu.d/Windows/stopped/end/00-hugepages-fix.sh \
       console/host/libvirt/hooks/qemu.d/Windows/stopped/end/hugepages-reset.sh
```

Les deux unités rapatriées doivent contenir exactement ceci — les recopier telles quelles depuis l'hôte, sans reformater :

```ini
# vm-idle-shutdown.service
[Unit]
Description=Shut down Windows VM after sustained inactivity
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/vm-idle-shutdown.sh
```

```ini
# vm-idle-shutdown.timer
[Unit]
Description=Periodic Windows VM idle check (energy saving)
[Timer]
OnBootSec=15min
OnUnitActiveSec=10min
[Install]
WantedBy=timers.target
```

- [ ] **Step 4: relancer le test**

Run: `python3 scripts/tests/test_console_host_files.py`
Expected: PASS — `OK - the repository carries the full lifecycle and no placeholder hooks`

- [ ] **Step 5: brancher la suite sur l'agrégateur**

Dans `installer/Makefile`, cible `test-packages`, ajouter `test_console_host_files` à la liste des suites, **en respectant l'ordre et le format existants** (chaque suite est précédée d'un `--- <nom>`). Vérifier ensuite que le compte passe de 26 à 27 :

Run: `cd installer && make test-packages PYTHON="$PY" 2>&1 | command grep -c '^--- test_'`
Expected: `27`

- [ ] **Step 6: commit**

```bash
git add -A console/host scripts/tests/test_console_host_files.py installer/Makefile
git commit -m "feat(console): le depot porte enfin la moitie qui rendort la VM

vm-idle-shutdown.sh n existait que deploye sur un hote, comme
handle-vm-start.sh avant le 24/08. Les trois talons hugepages, qui ne
font rien depuis que le pool est fixe par sysctl, disparaissent."
```

---

### Task 2 : écrire les unités de réveil

**Files:**
- Create: `console/host/systemd/vm-trigger-47984.socket`
- Create: `console/host/systemd/vm-trigger-47984.service`
- Create: `console/host/systemd/vm-trigger-47989.socket`
- Create: `console/host/systemd/vm-trigger-47989.service`
- Create: `console/host/systemd/vm-trigger-no-start-limit.conf`
- Test: `scripts/tests/test_console_wake_units.py`

**Interfaces:**
- Consumes: `console/host/vm-wake-gate.py`, qui lit son port dans `sys.argv[1]`.
- Produces: cinq fichiers dans `console/host/systemd/` — la tâche 3 les place, la tâche 4 les arme.

**Ce que le drop-in empêche.** `Type=oneshot` plus un déclenchement répété tue le socket via `service-start-limit-hit` — systemd compte les **démarrages**, pas les échecs, et cinq sondes Moonlight en dix secondes pendant le boot de la VM suffisent. C'est arrivé deux fois (13 et 17 juillet 2026). `StartLimitIntervalSec=0` est la correction permanente.

**Pourquoi 47984 garde une unité alors qu'il ne réveille plus rien.** Le gate refuse désormais tout réveil sur 47984 : son ancien test (« le client parle TLS ») correspondait à chaque scanner de masse d'internet, et sur trente jours **tous** les réveils qu'il a produits étaient des faux positifs. Mais l'unité reste, parce que le gate y **journalise** les sondes — c'est ce journal qui a permis de le mesurer. La décision vit dans `vm-wake-gate.py`, pas dans l'unité.

- [ ] **Step 1: écrire le test qui échoue**

Créer `scripts/tests/test_console_wake_units.py` :

```python
#!/usr/bin/env python3
"""The wake units must agree with the gate they trigger.

A unit whose ExecStart names a different port than its filename, or that
lost its no-start-limit drop-in, fails in a way nothing reports: systemd
disables the socket after five starts and wake-on-demand simply stops.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
UNITS = os.path.join(ROOT, "console", "host", "systemd")

failures = []


def check(label, condition):
    if not condition:
        failures.append(label)


for port in ("47984", "47989"):
    sock = os.path.join(UNITS, f"vm-trigger-{port}.socket")
    svc = os.path.join(UNITS, f"vm-trigger-{port}.service")
    check(f"vm-trigger-{port}.socket exists", os.path.isfile(sock))
    check(f"vm-trigger-{port}.service exists", os.path.isfile(svc))
    if not (os.path.isfile(sock) and os.path.isfile(svc)):
        continue
    stext, vtext = open(sock).read(), open(svc).read()

    check(f"{port}: listens on every interface",
          f"ListenStream=0.0.0.0:{port}" in stext)
    # Accept=false: ONE service instance handles the listening socket and
    # reads the first bytes itself. Accept=true would spawn a per-connection
    # instance and the gate could not refuse before the VM starts.
    check(f"{port}: Accept=false", "Accept=false" in stext)
    check(f"{port}: enabled into sockets.target",
          "WantedBy=sockets.target" in stext)

    # The port in ExecStart is the gate's only argument; a mismatch with the
    # filename makes the 47984 probe log claim to be the 47989 wake path.
    check(f"{port}: ExecStart carries this very port",
          f"ExecStart=/usr/local/sbin/vm-wake-gate.py {port}" in vtext)
    check(f"{port}: ordered after its socket",
          f"After=vm-trigger-{port}.socket" in vtext)
    check(f"{port}: oneshot", "Type=oneshot" in vtext)
    # The gate waits on the VM's IP for up to 180 s; a shorter deadline kills
    # a wake that was working.
    check(f"{port}: start deadline leaves room for a VM boot",
          "TimeoutStartSec=300" in vtext)

dropin = os.path.join(UNITS, "vm-trigger-no-start-limit.conf")
check("the no-start-limit drop-in exists", os.path.isfile(dropin))
if os.path.isfile(dropin):
    check("the drop-in disables the start limit",
          "StartLimitIntervalSec=0" in open(dropin).read())

if failures:
    for item in failures:
        print(f"FAIL - {item}")
    sys.exit(1)
print("OK - the wake units agree with the gate and cannot hit the start limit")
```

- [ ] **Step 2: le lancer pour vérifier qu'il échoue**

Run: `python3 scripts/tests/test_console_wake_units.py`
Expected: FAIL — les cinq fichiers manquent.

- [ ] **Step 3: écrire les cinq fichiers**

`console/host/systemd/vm-trigger-47984.socket` :

```ini
[Unit]
Description=Socket listener to trigger VM start for port 47984

[Socket]
ListenStream=0.0.0.0:47984
Accept=false
TriggerLimitIntervalSec=2
TriggerLimitBurst=200

[Install]
WantedBy=sockets.target
```

`console/host/systemd/vm-trigger-47984.service` :

```ini
[Unit]
Description=Gated VM wake for port 47984 (probe logging only, never wakes)
Requires=vm-trigger-47984.socket
After=vm-trigger-47984.socket

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/vm-wake-gate.py 47984
TimeoutStartSec=300
```

`console/host/systemd/vm-trigger-47989.socket` : identique au 47984 en remplaçant les deux occurrences du port.

`console/host/systemd/vm-trigger-47989.service` :

```ini
[Unit]
Description=Gated VM wake for port 47989 (only Moonlight-speaking clients)
Requires=vm-trigger-47989.socket
After=vm-trigger-47989.socket

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/vm-wake-gate.py 47989
TimeoutStartSec=300
```

`console/host/systemd/vm-trigger-no-start-limit.conf` :

```ini
[Unit]
# Type=oneshot plus repeated triggering trips systemd's start limit, which
# counts STARTS rather than failures: five Moonlight polls in ten seconds
# during a VM boot window are enough to disable the socket. Observed twice
# (2026-07-13, 2026-07-17). Zero disables the limit entirely.
StartLimitIntervalSec=0
```

- [ ] **Step 4: relancer le test**

Run: `python3 scripts/tests/test_console_wake_units.py`
Expected: PASS

- [ ] **Step 5: brancher la suite et vérifier le compte**

Ajouter `test_console_wake_units` à `test-packages` dans `installer/Makefile`.

Run: `cd installer && make test-packages PYTHON="$PY" 2>&1 | command grep -c '^--- test_'`
Expected: `28`

- [ ] **Step 6: commit**

```bash
git add console/host/systemd scripts/tests/test_console_wake_units.py installer/Makefile
git commit -m "feat(console): les unites de reveil entrent dans le depot

Elles n existaient qu en production. Le drop-in qui desarme la limite de
demarrage voyage avec elles: sans lui, cinq sondes Moonlight en dix
secondes suffisent a couper le socket."
```

---

### Task 3 : `install` pose le cycle complet

**Files:**
- Modify: `console/hooks/install.py`
- Test: `scripts/tests/test_console_install.py`

**Interfaces:**
- Consumes: tout ce que les tâches 1 et 2 ont créé sous `console/host/`.
- Produces: une cible portant le dispatcher, les hooks, les scripts et les unités — **sans aucun lien d'activation**. La tâche 4 les arme.

**Pourquoi `install` n'active rien.** Le contrat sépare « écrire dans une racine » de « agir sur un système vivant », et ici la séparation a une conséquence concrète : les sockets de réveil écoutent sur `0.0.0.0`, et le hook libvirt `stopped/end/rules.sh` **retire** les forward-ports quand la VM s'arrête — donc précisément quand le chemin de réveil est armé, le DNAT a disparu et internet entier atteint `0.0.0.0:47989` sur `ppp0`. Armer ce socket pour une VM qui n'existe pas encore serait une surface exposée sans contrepartie.

- [ ] **Step 1: écrire les assertions qui échouent**

Dans `scripts/tests/test_console_install.py`, le premier bloc `with tempfile.TemporaryDirectory()` pilote déjà le hook réel et vérifie les placements de la phase 2a. Y ajouter les vérifications suivantes, **avant** la lecture de `retro.json`. Noter la signature : `check(label, got, want)` prend **trois** arguments dans ce fichier, et `root` y est un `pathlib.Path`.

```python
    # The dispatcher is the load-bearing one: without it libvirt runs no
    # hook at all, so the two CPU wrappers install DOES write are never
    # executed. It was missing for the whole of phase 2a.
    executables = [
        "etc/libvirt/hooks/qemu",
        "etc/libvirt/hooks/qemu.d/Windows/prepare/begin/bind-vfio-gpu.sh",
        "etc/libvirt/hooks/qemu.d/Windows/release/end/rebind-host-gpu.sh",
        "etc/libvirt/hooks/qemu.d/Windows/started/begin/rules.sh",
        "etc/libvirt/hooks/qemu.d/Windows/stopped/end/rules.sh",
        "usr/local/sbin/vm-idle-shutdown.sh",
    ]
    for rel in executables:
        check(f"{rel} depose", (root / rel).is_file(), True)
        check(f"{rel} executable", os.access(root / rel, os.X_OK), True)

    # Units are data, not programs. Mode is not asserted - only presence -
    # because a unit with the execute bit still works; what must not happen
    # is a unit missing while the package claims the cycle is deployed.
    units = [
        "etc/systemd/system/vm-trigger-47984.socket",
        "etc/systemd/system/vm-trigger-47984.service",
        "etc/systemd/system/vm-trigger-47989.socket",
        "etc/systemd/system/vm-trigger-47989.service",
        "etc/systemd/system/vm-idle-shutdown.service",
        "etc/systemd/system/vm-idle-shutdown.timer",
        "etc/systemd/system/vm-trigger-47984.service.d/no-start-limit.conf",
        "etc/systemd/system/vm-trigger-47989.service.d/no-start-limit.conf",
    ]
    for rel in units:
        check(f"{rel} depose", (root / rel).is_file(), True)

    # The drop-in must reach BOTH services: systemd reads it from each
    # unit's own .d/ directory, so one copy enables the limit on the other.
    for port in ("47984", "47989"):
        dropin = root / ("etc/systemd/system/vm-trigger-"
                         f"{port}.service.d/no-start-limit.conf")
        check(f"le drop-in {port} desarme la limite de demarrage",
              "StartLimitIntervalSec=0" in dropin.read_text(), True)

    # install WRITES; activate ARMS. A wake socket armed here would listen
    # on 0.0.0.0 for a VM that does not exist yet - and the stopped/end
    # rules.sh hook removes the forward-ports precisely then, so the DNAT
    # that would otherwise shadow it is gone.
    for wants in ("sockets.target.wants", "timers.target.wants"):
        check(f"install ne cree aucun lien dans {wants}",
              (root / "etc/systemd/system" / wants).exists(), False)
```

- [ ] **Step 2: le lancer pour vérifier qu'il échoue**

Run: `python3 scripts/tests/test_console_install.py`
Expected: FAIL sur le dispatcher, les hooks GPU, la paire `rules.sh`, `vm-idle-shutdown.sh` et les sept unités. Les placements existants passent déjà.

- [ ] **Step 3: étendre le hook**

Dans `console/hooks/install.py`, remplacer les placements ponctuels par deux tables, juste après les définitions de `CONFINE_WRAPPER` / `RELEASE_WRAPPER` :

```python
HOOK_BASE = f"etc/libvirt/hooks/qemu.d/{VM_NAME}"

# (source under console/, destination under the target root).
# Every entry lands executable; see place()'s default mode.
HOOK_FILES = [
    ("host/libvirt/hooks/qemu", "etc/libvirt/hooks/qemu"),
    # THE APPARMOR TRAP (see module docstring): this one path is not a
    # matter of taste.
    ("host/vm-cpu-partition.sh", "etc/libvirt/hooks/vm-cpu-partition.sh"),
    (f"host/libvirt/hooks/qemu.d/{VM_NAME}/prepare/begin/bind-vfio-gpu.sh",
     f"{HOOK_BASE}/prepare/begin/bind-vfio-gpu.sh"),
    (f"host/libvirt/hooks/qemu.d/{VM_NAME}/release/end/rebind-host-gpu.sh",
     f"{HOOK_BASE}/release/end/rebind-host-gpu.sh"),
    (f"host/libvirt/hooks/qemu.d/{VM_NAME}/started/begin/rules.sh",
     f"{HOOK_BASE}/started/begin/rules.sh"),
    (f"host/libvirt/hooks/qemu.d/{VM_NAME}/stopped/end/rules.sh",
     f"{HOOK_BASE}/stopped/end/rules.sh"),
]

HOST_SCRIPTS = [
    ("host/vm-wake-gate.py", "usr/local/sbin/vm-wake-gate.py"),
    ("host/handle-vm-start.sh", "usr/local/sbin/handle-vm-start.sh"),
    ("host/vm-idle-shutdown.sh", "usr/local/sbin/vm-idle-shutdown.sh"),
    ("host/winvm", "usr/local/bin/winvm"),
]

# Units are DATA, not programs: mode 0644. A unit file with the execute bit
# still works, but the difference is how systemd's own packages ship them.
UNITS = [
    "vm-trigger-47984.socket", "vm-trigger-47984.service",
    "vm-trigger-47989.socket", "vm-trigger-47989.service",
    "vm-idle-shutdown.service", "vm-idle-shutdown.timer",
]

# The same drop-in serves both wake services; systemd reads it from each
# unit's own .d/ directory, so it is copied twice under its canonical name.
DROPIN_SRC = "host/systemd/vm-trigger-no-start-limit.conf"
DROPIN_TARGETS = [
    "etc/systemd/system/vm-trigger-47984.service.d/no-start-limit.conf",
    "etc/systemd/system/vm-trigger-47989.service.d/no-start-limit.conf",
]
```

Puis, dans `main()`, remplacer le bloc `pct 20` / `pct 50` par :

```python
    emit({"event": "progress", "pct": 20,
          "msg": "Deploiement des hooks libvirt"})
    for src, dest in HOOK_FILES:
        place(os.path.join(HERE, src), under(dest))
    write(under(f"{HOOK_BASE}/prepare/begin/10-cpu-confine.sh"),
          CONFINE_WRAPPER, mode=0o755)
    write(under(f"{HOOK_BASE}/release/end/10-cpu-release.sh"),
          RELEASE_WRAPPER, mode=0o755)

    emit({"event": "progress", "pct": 50, "msg": "Deploiement des scripts hote"})
    for src, dest in HOST_SCRIPTS:
        place(os.path.join(HERE, src), under(dest))

    # Placed, deliberately NOT enabled: arming a 0.0.0.0 wake socket for a
    # VM that does not exist yet would be exposure with no counterpart. The
    # activate phase arms them, once there is something to wake.
    emit({"event": "progress", "pct": 65, "msg": "Unites systemd posees"})
    for unit in UNITS:
        place(os.path.join(HERE, "host", "systemd", unit),
              under(f"etc/systemd/system/{unit}"), mode=0o644)
    for dest in DROPIN_TARGETS:
        place(os.path.join(HERE, DROPIN_SRC), under(dest), mode=0o644)
```

Le placement de `retro.json` (`pct 80`) et l'`emit` final restent inchangés.

- [ ] **Step 4: relancer le test**

Run: `python3 scripts/tests/test_console_install.py`
Expected: PASS

- [ ] **Step 5: agrégateur**

Run: `cd installer && make test-packages PYTHON="$PY"`
Expected: 28 suites, toutes vertes, exit 0.

- [ ] **Step 6: commit**

```bash
git add console/hooks/install.py scripts/tests/test_console_install.py
git commit -m "feat(console): install pose le dispatcher, sans quoi rien ne s executait

Les deux wrappers CPU etaient ecrits depuis la phase 2a mais libvirt n avait
aucun dispatcher pour les appeler. Les hooks GPU, la paire rules.sh, le
script d inactivite et les sept unites suivent. Les unites sont POSEES et
non armees: activate s en charge."
```

---

### Task 4 : `activate` arme les unités

**Files:**
- Modify: `console/hooks/activate.py` (le talon actuel disparaît)
- Test: `scripts/tests/test_console_activate.py`

**Interfaces:**
- Consumes: les unités que la tâche 3 a posées sous `etc/systemd/system/`.
- Produces: rien pour une tâche ultérieure de ce plan. La phase 2c y greffera la construction de la VM.

**Le hook accepte `--root`, que le runner ne passe jamais.** `run_activate()` appelle `run_hook(..., root=None)`, donc en production le hook écrit sous `/`. Un `--root` optionnel par défaut à `/` ne change rien à ce chemin et rend la phase testable contre une racine jetable — c'est exactement ce que fait déjà `install.py`.

**L'armement doit être idempotent.** Une activation interrompue rejoue au démarrage suivant : le fichier témoin (`/var/lib/nivuus/packages/console.activated`) n'est écrit qu'en cas de succès. Un `os.symlink` sur un lien existant lève `FileExistsError` ; le hook doit traiter « déjà armé » comme un succès.

- [ ] **Step 1: écrire le test qui échoue**

Créer `scripts/tests/test_console_activate.py` :

```python
#!/usr/bin/env python3
"""Arming must be a symlink, must be idempotent, and must never dangle.

Nine winvm-proxy-*.socket entries sit in this host's sockets.target.wants/
as REGULAR FILES; systemd ignores them with "is not a symlink, ignoring".
That is the failure this asserts against: a unit that looks enabled and
is not.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
HOOK = os.path.join(ROOT, "console", "hooks", "activate.py")

CTX = json.dumps({
    "package": {"name": "console", "version": "1.0.0", "root": "console"},
    "hw": {}, "answers": {"dedicated_nvme": "/dev/nvme1n1", "retro": False},
})

LINKS = {
    "etc/systemd/system/sockets.target.wants/vm-trigger-47984.socket":
        "/etc/systemd/system/vm-trigger-47984.socket",
    "etc/systemd/system/sockets.target.wants/vm-trigger-47989.socket":
        "/etc/systemd/system/vm-trigger-47989.socket",
    "etc/systemd/system/timers.target.wants/vm-idle-shutdown.timer":
        "/etc/systemd/system/vm-idle-shutdown.timer",
}

failures = []


def check(label, condition):
    if not condition:
        failures.append(label)


def run(root):
    return subprocess.run(
        [sys.executable, HOOK, "--phase", "activate", "--root", root],
        input=CTX, capture_output=True, text=True, cwd=ROOT)


# A target where install has run: the unit files are present.
with tempfile.TemporaryDirectory() as root:
    units = os.path.join(root, "etc/systemd/system")
    os.makedirs(units)
    for name in ("vm-trigger-47984.socket", "vm-trigger-47989.socket",
                 "vm-idle-shutdown.timer"):
        open(os.path.join(units, name), "w").write("[Unit]\n")

    proc = run(root)
    check(f"activate succeeds (rc={proc.returncode})", proc.returncode == 0)
    for rel, target in LINKS.items():
        path = os.path.join(root, rel)
        check(f"{rel} is a symlink", os.path.islink(path))
        if os.path.islink(path):
            check(f"{rel} points at {target}", os.readlink(path) == target)

    # Idempotent: an interrupted activation retries at the next boot, and
    # the stamp file is written only on success.
    again = run(root)
    check(f"activate is idempotent (rc={again.returncode})",
          again.returncode == 0)

# A target where a unit is missing: refuse rather than dangle.
with tempfile.TemporaryDirectory() as root:
    os.makedirs(os.path.join(root, "etc/systemd/system"))
    proc = run(root)
    check("a missing unit is refused, not linked", proc.returncode != 0)
    check("the refusal names the missing unit",
          "vm-trigger-47984.socket" in (proc.stderr or ""))
    dangling = os.path.join(
        root, "etc/systemd/system/sockets.target.wants/vm-trigger-47984.socket")
    check("no dangling link is left behind", not os.path.lexists(dangling))

if failures:
    for item in failures:
        print(f"FAIL - {item}")
    sys.exit(1)
print("OK - arming is a real symlink, idempotent, and never dangles")
```

- [ ] **Step 2: le lancer pour vérifier qu'il échoue**

Run: `python3 scripts/tests/test_console_activate.py`
Expected: FAIL — le talon actuel sort 0 sans rien créer, donc toutes les assertions de lien échouent.

- [ ] **Step 3: réécrire le hook**

Remplacer entièrement `console/hooks/activate.py` :

```python
#!/usr/bin/env python3
"""Activate phase for the console package: arm what install only placed.

Enablement is a SYMLINK, never `systemctl enable`. systemctl fails
silently in constrained environments - a query subcommand simply prints
nothing - so an enable that "returned" tells you nothing. A symlink either
exists or raises. This is also exactly what systemctl does for a unit
carrying WantedBy=sockets.target.

Nine winvm-proxy-*.socket entries sit in this host's sockets.target.wants/
as REGULAR FILES, which systemd ignores with "is not a symlink, ignoring".
Every link here is verified to point at an existing unit before it is
created, so a unit that looks enabled always is.

The VM itself is still built by hand (windows-guest/build.py then
domain.py); wiring that in is phase 2c.
"""
import argparse
import json
import os
import sys

# unit file (under /etc/systemd/system) -> the .wants directory that enables it
WANTS = {
    "vm-trigger-47984.socket": "sockets.target.wants",
    "vm-trigger-47989.socket": "sockets.target.wants",
    "vm-idle-shutdown.timer": "timers.target.wants",
}

UNIT_DIR = "etc/systemd/system"


def emit(event: dict) -> None:
    print(json.dumps(event), flush=True)


def arm(root: str, unit: str, wants: str) -> None:
    """Link one unit into its .wants directory. Idempotent.

    Raises FileNotFoundError if the unit is absent: a dangling link is
    worse than no link, because it reads as enabled.
    """
    unit_path = os.path.join(root, UNIT_DIR, unit)
    if not os.path.isfile(unit_path):
        raise FileNotFoundError(unit_path)

    wants_dir = os.path.join(root, UNIT_DIR, wants)
    os.makedirs(wants_dir, exist_ok=True)
    link = os.path.join(wants_dir, unit)

    # The link target is an ABSOLUTE path in the running system's namespace,
    # not in the throwaway root: systemd resolves it after the reboot, when
    # this root IS /.
    target = f"/{UNIT_DIR}/{unit}"
    if os.path.islink(link) and os.readlink(link) == target:
        return
    if os.path.lexists(link):
        os.remove(link)      # a regular file here is the bug, not a state
    os.symlink(target, link)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True)
    parser.add_argument("--root", default="/")
    args = parser.parse_args()
    json.load(sys.stdin)
    root = args.root.rstrip("/") or "/"

    emit({"event": "progress", "pct": 30,
          "msg": "Armement des unites de reveil et du minuteur d inactivite"})
    for unit, wants in WANTS.items():
        try:
            arm(root, unit, wants)
        except FileNotFoundError as exc:
            print(f"console activate: unite absente, rien arme : {exc}",
                  file=sys.stderr)
            return 1

    emit({"event": "progress", "pct": 100,
          "msg": "console : cycle de vie arme ; l invite Windows se construit "
                 "encore a la main (windows-guest/build.py)"})
    emit({"event": "done"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: relancer le test**

Run: `python3 scripts/tests/test_console_activate.py`
Expected: PASS

- [ ] **Step 5: brancher la suite et vérifier l'ensemble**

Ajouter `test_console_activate` à `test-packages` dans `installer/Makefile`.

Run: `cd installer && make test-packages PYTHON="$PY" 2>&1 | command grep -c '^--- test_'`
Expected: `29`, toutes vertes, exit 0.

- [ ] **Step 6: commit**

```bash
git add console/hooks/activate.py scripts/tests/test_console_activate.py installer/Makefile
git commit -m "feat(console): activate arme le cycle, par lien symbolique

Pas de systemctl: il echoue silencieusement en environnement contraint,
et une sous-commande de requete se contente de ne rien afficher. Un lien
existe ou leve. Un lien pendant est refuse plutot que cree: neuf entrees
winvm-proxy sont des fichiers ordinaires sur cet hote et systemd les
ignore sans le dire."
```

---

### Task 5 : la documentation cesse de décrire l'état d'avant

**Files:**
- Modify: `console/README.md` (section « What `install` does NOT deploy yet »)
- Modify: `CLAUDE.md` (le paragraphe « `console/hooks/install.py` places seven things and no more »)
- Modify: `docs/superpowers/specs/2026-08-27-decoupage-installer-console-design.md` (le hors-périmètre nº 1)

**Interfaces:** aucune. Tâche documentaire, dernière volontairement — elle décrit ce que les quatre précédentes ont réellement produit, vérifié et non supposé.

- [ ] **Step 1: mesurer avant d'écrire**

```bash
# 1. le nombre de suites, mesure et non recopie
cd installer && make test-packages PYTHON="$PY" 2>&1 | command grep -c '^--- test_'; cd ..

# 2. ce que le depot porte reellement
find console -name '*.socket' -o -name '*.service' -o -name '*.timer' | sort

# 3. ce que install pose reellement, liste depuis une racine jetable
python3 - <<'PY'
import json, os, subprocess, sys, tempfile
CTX = json.dumps({"package": {"name": "console", "version": "1.0.0",
                              "root": "console"},
                  "hw": {"gpus": [{"slot": "01:00.0", "discrete": True}]},
                  "answers": {"dedicated_nvme": "/dev/nvme1n1", "retro": False,
                              "admin_password": "hunter2hunter2"}})
with tempfile.TemporaryDirectory() as root:
    proc = subprocess.run([sys.executable, "hooks/install.py", "--phase",
                           "install", "--root", root],
                          input=CTX, capture_output=True, text=True,
                          cwd="console")
    print("rc =", proc.returncode, (proc.stderr or "").strip()[:120])
    for base, _dirs, files in sorted(os.walk(root)):
        for name in sorted(files):
            print("  " + os.path.relpath(os.path.join(base, name), root))
PY
```

Aucune phrase de cette tâche ne doit être écrite sans que la commande correspondante ait été lancée. Trois comptages faux ont survécu à la phase 2a précisément parce qu'ils avaient été recopiés au lieu d'être mesurés.

- [ ] **Step 2: `console/README.md`**

La section « What `install` does NOT deploy yet (phase 2b) » devient « (phase 2c) » et ne garde **que** ce qui reste vrai : l'invité Windows. Les deux listes actuelles — « fichiers présents mais non posés » et « fichiers qui n'existent pas encore » — disparaissent entièrement, les deux ayant été traitées. Ajouter à la table de placement les lignes des unités et de `vm-idle-shutdown.sh`, et une phrase disant que les unités sont posées par `install` puis armées par `activate`.

- [ ] **Step 3: `CLAUDE.md`**

Le paragraphe commençant par « **`console/hooks/install.py` places seven things and no more** » est faux à partir de la tâche 3. Le remplacer par un paragraphe qui dit ce qui est posé, ce qui est armé et par quelle phase, et qui conserve la seule information durable de l'ancien : la console n'est pas fonctionnelle depuis une installation seule tant que l'invité n'est pas construit. Mentionner aussi les deux trouvailles de ce plan : `vm-idle-shutdown.sh` n'était versionné nulle part, et les trois hooks hugepages étaient des talons vides.

- [ ] **Step 4: le spec**

Le hors-périmètre nº 1 affirme qu'« **il n'existe aucun chemin officiel** » pour l'ISO IoT Enterprise LTSC. C'est faux : `https://go.microsoft.com/fwlink/?linkid=2270353` redirige vers
`26100.1742.240906-0331.ge_release_svc_refresh_CLIENT_IOT_LTSC_EVAL_x64FRE_en-us.iso`,
soit bien IoT LTSC 2024 sur la base 24H2 mesurée pour le HDR. Corriger l'affirmation en conservant la réserve qui compte : c'est le média **d'évaluation** (90 jours), là où l'invité prouvé en production venait de l'ISO volume et s'activait en `IoTEnterpriseS / VOLUME_MAK / Licensed`. Une édition Evaluation de Windows client ne se convertit historiquement pas par `slmgr /ipk` — `DISM /Set-Edition` ne vaut que pour Server. Écrire que la question « cette clé MAK licencie-t-elle ce média » doit être **mesurée** en phase 2c, pas supposée.

- [ ] **Step 5: relancer la suite complète**

Run: `cd installer && make test-packages PYTHON="$PY"`
Expected: 29 suites, exit 0.

- [ ] **Step 6: commit**

```bash
git add console/README.md CLAUDE.md docs/superpowers/specs/2026-08-27-decoupage-installer-console-design.md
git commit -m "docs: la console est cablee cote hote, et le spec se corrige sur l ISO

Le README et CLAUDE.md decrivaient un install qui posait sept choses.
Le spec affirmait qu aucun lien officiel n existait pour l ISO IoT LTSC:
le fwlink 2270353 en sert un, mais en edition Evaluation, ce qui est une
reserve differente et qui reste a mesurer."
```
