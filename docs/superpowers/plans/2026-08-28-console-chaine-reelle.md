# Ce que le lancement réel a exigé (phase 2e) — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** corriger les cinq défauts que le premier lancement réel a produits, puis revalider sur le banc jetable.

**Architecture:** aucun de ces cinq défauts n'était visible d'une suite de tests ; tous ont été mesurés le 2026-08-28 en faisant tourner la chaîne pour de vrai. Ce plan ne conçoit rien : il corrige ce qui a été constaté, et chaque tâche porte la mesure qui l'a révélé.

**Spec:** `docs/superpowers/specs/2026-08-28-console-activate-invite-design.md` (amendé section 5 bis).

## Les cinq mesures qui fondent ce plan

| # | Constaté le 2026-08-28 | Conséquence |
| --- | --- | --- |
| 1 | `/dev/nvme1n1` est en `vfio-pci` : **aucune entrée `/sys/block`**. Seul `nvme0n1`, le disque de l'hôte, en a une. | `plan_steps()` refuse dès la planification : « cannot read the size of /dev/nvme1n1 ». |
| 2 | `build.py` prépare sous `TMPDIR`. Sur cet hôte `/tmp` est un **tmpfs de 10 Go rempli à 97 %**. | `OSError: [Errno 28] No space left on device` en pleine copie de la charge utile. |
| 3 | **Aucun `chown` nulle part** dans `guest_steps.py`, `build.py`, `unattend_iso.py`. L'ISO sort en `root:root 0600`, le répertoire de travail en `drwxr-x--- root:root`. | `virsh start` échoue : « Could not open … Permission denied ». Deux corrections manuelles ont été nécessaires. |
| 4 | Le média LTSC affiche **« Press any key to boot from CD or DVD »**. La recette d'acceptation envoie `virsh send-key KEY_ENTER` en boucle ; **la chaîne ne l'envoie jamais**. | La VM est restée 42 minutes devant « BdsDxe: No bootable option or device was found », disque figé à 194 Ko. |
| 5 | `fetch_payload.py` le dit lui-même : « **agent/agent.exe must be extracted from the current Windows VM before it is wiped** ». | Sur une machine neuve il n'y a aucune VM d'où l'extraire : l'étape 40 du provisionnement n'aurait rien à déployer. |

## Global Constraints

- **La chaîne réelle reste INTERDITE pendant les tâches 1 à 4** : `build.py`, `fetch_payload.py`, `domain.py define`, `virsh define/start/undefine`, `winrm_exec.py` contre la VM réelle. La tâche 5 est la seule à lever cette interdiction, et sous conditions. Autorisés partout : `virsh dumpxml`, `virsh domstate`, `ip neigh`, `python3 domain.py xml`, l'appel direct des gardes de `domain.py`.
- **`console/` n'importe RIEN de `installer/`.**
- **Les secrets ne passent jamais par `argv`**, ni en clair dans un fichier d'empreinte.
- **UNE AUTRE SESSION CLAUDE ÉCRIT DANS CET ARBRE.** N'indexer que des **chemins nommés** — jamais `git add -A`. Ne toucher ni `docs/console-dettes.md`, ni `console/guest/assets/wallpaper.png`, ni `console/guest/provision/assets/steam-shell.ps1`, et ne modifier `CLAUDE.md` que chirurgicalement.
- **Tests** : `cd installer && make test-packages PYTHON="$PY"` avec `PY=/tmp/user/0/claude-0/-home-mallanic-Projects-Nivuus-packages-installer/bfd96116-6ef4-4bd1-8191-ca092cfaf289/scratchpad/venv/bin/python`. **33 suites au départ.** Compter `✗` **et** `FAIL`, jamais un comptage à la place du code de retour de `make`. Si `test_handle_vm_start` sort en `rc=124`, la relancer **seule**.
- **Toute épreuve de falsification se fait `__pycache__` purgé et `python -B`.**
- **Un banc factice doit implémenter TOUTE l'interface que le code lit** — erreur commise quatre fois sur la phase précédente, dont deux par le planificateur.
- **`command grep`, jamais `grep` nu.** Commentaires en anglais, commits en **français sans accents**.

---

### Task 1 : la taille du disque vient de `resolve`, pas de `/sys/block`

**Files:** `console/hooks/resolve.py`, `console/guest_steps.py`, `console/hardware.py` · Tests : `console/tests/test_console_resolve.py`, `test_console_guest_steps.py`

**Interfaces:** `resolve` émet `dedicated_nvme_size_bytes` dans son événement ; `guest_steps` le consomme en priorité et ne tombe sur `/sys/block` qu'à défaut.

**Pourquoi `resolve` et pas `activate`.** `resolve` tourne **avant toute écriture**, donc avant que le disque soit lié à `vfio-pci` : c'est le seul moment où le noyau expose encore un périphérique bloc. Après le redémarrage, la carte est prise et `/sys/block` ne connaît plus le disque. L'ancrage existe déjà — `guest_steps.py` lit `hw["dedicated_nvme_size_bytes"]`, et **rien ne le produit** (la revue finale l'avait noté comme constat mineur, sans voir qu'il était la clef).

- [ ] **Step 1: reproduire la mesure** — `lspci -nnk -d ::0108` doit montrer le NVMe dédié en `vfio-pci`, et `ls /sys/block` ne doit lister que le disque de l'hôte. Rapporte les deux. Si l'observation diffère, **arrête-toi**.
- [ ] **Step 2: le test qui échoue** — `resolve` émet la taille ; `guest_steps` la préfère à sysfs ; l'absence de la clef retombe sur sysfs ; l'absence des deux **refuse en nommant la cause**.
- [ ] **Step 3: implémenter.**
- [ ] **Step 4: prouver** — sans la clef ET avec un disque absent de sysfs, le refus doit être nommé et non une trace.
- [ ] **Step 5: agrégateur, commit.**

---

### Task 1b : le moteur transporte les faits mesurés avant le redémarrage

**Files:** `installer/packages/runner.py`, `installer/install-engine/steps/packages.py`, `installer/packages/activate_cli.py` · Tests : `scripts/tests/test_packages_runner.py`, `test_install_engine_packages.py`

**Cette tâche existe parce que la tâche 1 est juste mais inerte.** `resolve` émet désormais la taille du disque dédié — et **rien ne la transporte jusqu'à `activate`**. Vérifié dans les trois maillons :

* `runner.py::run_resolve()` ne lit de l'événement `platform` que `kernel-cmdline`, `modules` et `hugepages-mib` ; tout le reste est jeté ;
* `steps/packages.py` ne persiste que `{"version": …, "answers": …}` dans `etc/nivuus/packages.json` ;
* `activate_cli.py` reconstruit `hw` par **`hardware.detect_all()` après le redémarrage** — quand le disque est déjà lié à `vfio-pci` et invisible.

**C'est un manque du MOTEUR, pas de la console.** N'importe quel package `platform` peut avoir besoin de mesurer une chose qui n'existe plus après le reboot : un disque qui sera détaché, un périphérique qui sera capturé, un état que l'installation elle-même détruit. Conçois donc un canal **générique** — `resolve` retourne des faits, le moteur les persiste, `activate` les retrouve — et non un cas particulier pour la taille d'un NVMe.

**Deux propriétés à ne pas perdre :**

* **`resolve` reste en lecture seule.** Il retourne des faits, il n'écrit rien. C'est le moteur qui persiste.
* **Le fichier d'état est en 0600 et contient déjà des secrets** (les réponses du wizard, dont deux mots de passe et une clé produit). Les faits le rejoignent ; le mode ne s'élargit pas.

**Attention à la précédence.** Un fait persisté décrit le monde **d'avant** le redémarrage. Quand la détection d'après-reboot et le fait persisté se contredisent, lequel gagne ? Tranche explicitement et écris-le : pour la taille d'un disque devenu invisible, le fait persisté est la seule vérité disponible ; pour autre chose, ce ne serait pas forcément vrai.

- [ ] **Step 1: le test qui échoue** — un `resolve` qui émet un fait le retrouve dans l'état persisté, puis dans le `hw` que reçoit `activate`. Vérifie aussi qu'un package qui n'émet rien continue de fonctionner à l'identique.
- [ ] **Step 2: implémenter les trois maillons.**
- [ ] **Step 3: prouver** — couper chacun des trois maillons doit faire échouer le test, **en nommant lequel**. Trois épreuves, pas une : un canal se casse en trois endroits et une épreuve unique n'en couvre qu'un.
- [ ] **Step 4: vérifier que le mode du fichier d'état est resté 0600** — un test l'exige déjà, ne le contourne pas.
- [ ] **Step 5: agrégateur, commit.**

---

### Task 2 : la chaîne produit des artefacts que l'hyperviseur peut ouvrir

**Files:** `console/guest_steps.py` · Test : `console/tests/test_console_guest_steps.py`

**Deux défauts d'une même famille : ce que la chaîne écrit, personne d'autre ne peut le lire.**

**L'espace de préparation.** `build.py` prépare sous `TMPDIR`. Ici `/tmp` est un tmpfs de 10 Go rempli à 97 %, et la première construction est morte sur `No space left on device` en copiant 1,8 Go de pilotes. La chaîne doit préparer **dans son répertoire de travail**, qui vit sur le disque de données — pas dans la RAM.

**Les droits.** L'ISO sort en `root:root 0600` et le répertoire de travail en `drwxr-x--- root:root`. `qemu` tourne en `libvirt-qemu` : il ne peut ni traverser le répertoire ni lire le fichier. En production, l'ISO Windows appartient à `libvirt-qemu:libvirt-qemu`. Il a fallu deux corrections manuelles pour que `virsh start` aboutisse.

**Le mode 0600 doit être CONSERVÉ** : `build.py` le pose délibérément parce que l'ISO contient la clé produit et deux mots de passe en clair, et il le dit dans sa sortie. Changer le **propriétaire** suffit ; élargir le mode serait une régression de sécurité.

- [ ] **Step 1: le test qui échoue** — la commande de construction reçoit un espace de préparation sous le répertoire de travail ; l'ISO et le répertoire finissent lisibles par l'utilisateur du démon, **sans que le mode s'élargisse**.
- [ ] **Step 2: implémenter.** Le nom d'utilisateur ne se devine pas : lis-le du système, et **refuse en le nommant** s'il n'existe pas.
- [ ] **Step 3: prouver** — élargir le mode doit faire échouer le test ; un utilisateur inexistant doit produire un refus nommé.
- [ ] **Step 4: agrégateur, commit.**

---

### Task 3 : quelqu'un appuie sur la touche

**Files:** `console/guest_steps.py` · Test : `console/tests/test_console_guest_steps.py`

**C'est le défaut le plus lourd de la phase, et le plus simple.** Le média LTSC affiche « Press any key to boot from CD or DVD ». Sans frappe, l'invite expire, aucun autre périphérique n'est amorçable, et le firmware s'arrête sur « BdsDxe: No bootable option or device was found ». Mesuré : **42 minutes, disque figé à 194 Ko**. Après envoi des touches, Setup a démarré et écrit.

La recette d'acceptation le fait déjà, et sa forme est la bonne — **bornée, puis on s'arrête** :

```bash
for i in $(seq 1 12); do
  virsh send-key <domaine> --codeset linux KEY_ENTER >/dev/null 2>&1
  sleep 1
done
```

**Bornée, et c'est essentiel** : envoyer des touches au-delà de la fenêtre d'amorçage les enverrait à Windows Setup, où elles valideraient des écrans au hasard. Le commentaire de la recette le dit — « seulement pendant la fenêtre d'amorçage, puis s'arrêter ».

- [ ] **Step 1: le test qui échoue** — l'étape de démarrage envoie des touches après le démarrage, un nombre borné de fois, et **jamais si le domaine tournait déjà** (sinon on frapperait dans une session vivante).
- [ ] **Step 2: implémenter.** L'échec d'un `send-key` ne doit pas faire échouer le démarrage : c'est une aide à l'amorçage, pas une condition.
- [ ] **Step 3: prouver** — retirer l'envoi fait échouer le test ; le rendre non borné aussi.
- [ ] **Step 4: agrégateur, commit.**

---

### Task 4 : `agent.exe` entre dans le package

**Files:** `console/guest/payload/agent/agent.exe` (nouveau), `console/guest/fetch_payload.py`, `console/README.md` · Test : `console/tests/test_console_host_files.py` ou la suite du payload

**`fetch_payload.py` le dit lui-même** : « Not fetched, and never fetchable: agent/agent.exe must be extracted from the current Windows VM before it is wiped ». Sur une machine neuve il n'existe aucune VM d'où l'extraire, donc l'étape 40 du provisionnement n'a rien à déployer — et le défaut ne se voit qu'à l'installation.

**Décision de l'utilisateur, prise en connaissance de cause :** le binaire entre dans le package. Il pèse 11 Mo et sera figé dans l'historique git.

**Écris la réserve dans le dépôt, ne la tais pas.** Ce binaire est un artefact **compilé** qui appartient logiquement à `nivuus/desk` ; aucun dépôt source n'est présent sur cette machine. Le committer ici duplique sa propriété et il dérivera à chaque version de l'agent. C'est un compromis assumé pour rendre la console installable ; il doit être écrit là où quelqu'un le lira — dans `console/README.md` et à côté du fichier.

- [ ] **Step 1: mesurer** — taille et somme de contrôle du binaire source, à consigner dans le commit.
- [ ] **Step 2: le test qui échoue** — le package porte l'agent, et `fetch_payload` cesse d'annoncer qu'il n'est jamais récupérable.
- [ ] **Step 3: implémenter** — copier le binaire, adapter `fetch_payload.py` pour le prendre du package, écrire la réserve.
- [ ] **Step 4: prouver** — retirer le binaire doit faire échouer le test en le nommant.
- [ ] **Step 5: agrégateur, commit.** Le commit doit porter la somme de contrôle du binaire.

---

### Task 5 : revalider sur le banc, pour de vrai

**Ce n'est pas une tâche de code.** Elle relance la chaîne comme le 2026-08-28, avec les quatre correctifs, et vérifie qu'elle installe.

**Ce qui a été appris de la première tentative, et qu'il ne faut pas refaire :**

- **la taille de partition doit correspondre au disque du banc.** La première tentative a dérivé 809 Gio d'un disque hypothétique de 1 To puis l'a servie à un disque de 120 Go : Setup s'est arrêté sur « Select location to install Windows 11 ». C'était une erreur de banc, pas de code ;
- **le GPU doit être libre** : `assert_gpu_free()` refuse tant que `nvidia-persistenced` ou un `tdarr-ffmpeg` tient `/dev/nvidia*`. Les arrêter à la main — le banc, contrairement à la production, n'a pas de hook pour le faire ;
- **la console de production doit être hibernée** (`winvm "shutdown /h /f"`, la session est préservée) ;
- **tout se rend à la fin** : démonter le banc, rendre le GPU, redémarrer ollama, les deux nœuds Tdarr et `nvidia-persistenced`, supprimer les artefacts.

**Cette tâche exige l'accord explicite de l'utilisateur avant de démarrer quoi que ce soit** : elle arrête sa console et occupe sa carte graphique une à deux heures. Ne la lance pas de ta propre initiative.

- [ ] **Step 1: demander l'accord**, en disant ce qui sera arrêté et pour combien de temps.
- [ ] **Step 2: hiberner, libérer le GPU, vérifier qu'il est revenu à `nvidia`.**
- [ ] **Step 3: construire, définir, démarrer, envoyer les touches, attendre le témoin.**
- [ ] **Step 4: rapporter ce qui s'est passé, sans l'embellir** — y compris les échecs, qui sont le produit attendu de cette tâche.
- [ ] **Step 5: tout rendre**, et le prouver commande par commande.
