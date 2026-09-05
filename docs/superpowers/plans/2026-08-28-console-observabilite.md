# L'observabilité, reconçue (phase 2d bis) — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** remplacer une surveillance fondée sur deux prémisses fausses par une qui repose sur des faits mesurés, et fermer les trous que la revue finale a mis au jour sur le chemin réel d'une installation.

**Architecture:** la moitié « construction » de la phase 2d est solide et ne bouge pas. Ce plan refait la moitié « observabilité » : l'IP vient de la table de voisinage de l'hôte, le témoin est un fichier lu par-dessus WinRM et **vérifié en version**, l'ISO Windows survit au redémarrage, et la redéfinition finale cesse de boucler en silence.

**Spec:** `docs/superpowers/specs/2026-08-28-console-activate-invite-design.md`, section **5 bis** (amendement du 2026-08-28, qui révoque la décision 5).

## Global Constraints

- **INTERDIT : lancer la chaîne réelle.** Jamais `build.py`, `fetch_payload.py`, `domain.py define`, `virsh define`, `virsh start`, `virsh undefine`. Cela construirait une ISO d'un gigaoctet et **démarrerait ou redéfinirait la VM de production de cette machine**, ce qui détache le GPU de l'hôte et arrête ollama, `nvidia-persistenced` et Tdarr. Sont **autorisés** parce que sans effet de bord : `python3 domain.py xml`, l'appel direct des fonctions de garde de `domain.py`, `virsh dumpxml`, `virsh domstate`, `virsh net-list`, `ip neigh`.
- **UNE AUTRE SESSION CLAUDE ÉCRIT DANS CET ARBRE.** N'indexer que des **chemins nommés** — jamais `git add -A`, jamais `git add .`. Ne toucher ni `CLAUDE.md` (sauf les paragraphes de cette phase, chirurgicalement), ni `docs/console-dettes.md`, ni `console/guest/assets/wallpaper.png`, ni `console/guest/provision/assets/steam-shell.ps1`.
- **`console/` n'importe RIEN de `installer/`.**
- **Les secrets ne passent jamais par `argv`.** `winrm_exec.py` lit son mot de passe dans un fichier pour cette raison ; ne pas contourner.
- **Un commentaire n'est pas une autorité.** C'est l'erreur qui a causé ce plan : un en-tête périmé de deux jours a été cité comme preuve. Quand deux fichiers se contredisent, mesurer.
- **Commentaires de code en anglais**, messages de commit en **français sans accents**.
- **`command grep`, jamais `grep` nu** : la fonction du profil zsh ne préfixe pas les correspondances récursives par `./`.
- **Tests** : `cd installer && make test-packages PYTHON="$PY"` avec `PY=/tmp/user/0/claude-0/-home-mallanic-Projects-Nivuus-packages-installer/bfd96116-6ef4-4bd1-8191-ca092cfaf289/scratchpad/venv/bin/python`. **33 suites au départ.** Compter `✗` **et** `FAIL`, et ne jamais substituer un comptage au code de retour de `make`. Si `test_handle_vm_start` sort en `rc=124`, la relancer **seule** : l'autre session sature la machine ; seule elle passe 10/10 en 20 s.
- **Toute épreuve de falsification se fait `__pycache__` purgé et `python -B`** : le cache valide un `.pyc` sur mtime-à-la-seconde **et** taille, donc une mutation de taille identique faite et défaite dans la même seconde laisse réutiliser le bytecode muté.
- **Un banc factice doit implémenter TOUTE l'interface que le code sous test lit**, sinon c'est le banc qu'on mesure.

---

### Task 1 : l'IP vient de la table de voisinage

**Files:**
- Modify: `console/host/guest-ready-watch.py`
- Test: `console/tests/test_console_guest_ready.py`

**Interfaces:**
- Produit : `find_guest_ip()` qui rend une IPv4 ou `None`, sans dépendre d'un réseau libvirt. La tâche 2 la consomme.

**La méthode actuelle ne peut pas aboutir, et c'est mesuré.** Sur la VM de production **en marche** : `virsh domifaddr --source agent` échoue (aucun agent invité, le domaine ne déclare aucun `<channel>`), `--source lease` et `--source arp` rendent des tables vides, et `virsh net-list --all` ne déclare **aucun** réseau libvirt — le domaine est sur un pont externe. La méthode reprise de `handle-vm-start.sh` porte le même défaut ; il ne s'y voit pas parce que le hook `started/begin/rules.sh` pose les redirections de toute façon.

**Ce qui marche, mesuré le même jour :** le domaine déclare son MAC et son pont ; la table de voisinage de l'hôte associe les deux.

```
$ virsh dumpxml Windows | grep -E "mac address|source bridge"
        <mac address='52:54:00:48:e0:3e'/>
        <source bridge='internalBridge'/>
$ ip neigh show dev internalBridge
192.168.3.2 dev internalBridge lladdr 52:54:00:48:e0:3e REACHABLE
```

- [ ] **Step 1: reproduire la mesure**

Lance les deux commandes ci-dessus sur cette machine et rapporte leur sortie. Si elles diffèrent de ce qui est écrit, **arrête-toi et dis-le** : le plan reposerait alors sur un fait périmé, exactement l'erreur qu'il corrige.

- [ ] **Step 2: écrire le test qui échoue**

Dans `console/tests/test_console_guest_ready.py`, avec `virsh` et la lecture de la table simulés :

```python
# The neighbour table is the only source that works on this topology: the
# domain sits on an EXTERNAL bridge, so libvirt has no lease to hand out and
# no guest agent answers. Measured on the running production VM.
XML = "<domain><devices><interface type='bridge'>" \
      "<mac address='52:54:00:48:e0:3e'/>" \
      "<source bridge='internalBridge'/></interface></devices></domain>"
NEIGH = ("192.168.3.2 dev internalBridge lladdr 52:54:00:48:e0:3e REACHABLE\n"
         "fe80::426f:90c7:b3a2:c6b dev internalBridge lladdr "
         "52:54:00:48:e0:3e STALE\n")
check("l IPv4 est trouvee par le MAC du domaine",
      watch.find_guest_ip(dumpxml=lambda: XML, neigh=lambda br: NEIGH),
      "192.168.3.2")

# An IPv6 entry carries the same MAC. Returning it would send the WinRM probe
# to an address the guest does not listen on.
check("l IPv6 n est jamais rendue a la place",
      ":" in (watch.find_guest_ip(dumpxml=lambda: XML,
                                  neigh=lambda br: NEIGH) or ""), False)

# A MAC that is not in the table means the guest has not spoken yet - which is
# a state to report, not an error to raise.
check("un invite muet rend None",
      watch.find_guest_ip(dumpxml=lambda: XML, neigh=lambda br: ""), None)

# The MAC comparison must not depend on case: `ip neigh` and libvirt do not
# agree on it across versions.
check("la comparaison de MAC ignore la casse",
      watch.find_guest_ip(dumpxml=lambda: XML,
                          neigh=lambda br: NEIGH.upper()), "192.168.3.2")
```

- [ ] **Step 3: implémenter, puis mesurer en vrai**

Après l'implémentation, appelle `find_guest_ip()` **sans simulation** sur cette machine et compare à `192.168.3.2`. Rapporte le résultat : c'est la seule preuve que le format réel est bien analysé.

- [ ] **Step 4: agrégateur et commit** — 33 suites, exit 0.

---

### Task 2 : le témoin est un fichier, lu par-dessus WinRM, vérifié en version

**Files:**
- Modify: `console/host/guest-ready-watch.py`, `console/nivuus-package.yaml`
- Test: `console/tests/test_console_guest_ready.py`

**Interfaces:**
- Consomme : `find_guest_ip()` de la tâche 1.
- Produit : une classification dont l'état `READY` signifie « le témoin de CETTE installation est là ».

**Le port 5985 n'est plus le témoin, et ne l'est plus depuis le 2026-08-26.** `console/guest/provision/00-bootstrap.ps1` l'ouvre à l'étape **00**, délibérément : le port fermé était un proxy qui « failed exactly where it mattered » — quand une étape échoue, `99-marker.ps1` n'est jamais atteint, la règle reste fermée, et la seule porte d'entrée disparaît quand il faudrait regarder. **« The marker file IS the truth about readiness; the port never was. »** Seul l'en-tête de `99-marker.ps1` dit encore le contraire : il est périmé.

**L'outil existe et est éprouvé.** `console/guest/winrm_exec.py` lit son mot de passe dans un **fichier, jamais depuis `argv`**, et prend `GUEST_IP`, `GUEST_USER`, `GUEST_PASS_FILE` dans l'environnement. `testdomain.py::_marker_present()` s'en sert déjà exactement pour cela — **va le lire et reprends sa forme**.

**La vérification de version n'est pas un raffinement.** `testdomain.py` le dit : « a rebuild boots a disk that already holds the PREVIOUS run's marker, so its mere presence proves nothing ». Le témoin doit porter `provision_version=<PROVISION_VERSION courant>`, défini dans `console/guest/payload.py` (`B3` à ce jour — **lis la valeur, ne la recopie pas d'ici**).

- [ ] **Step 1: écrire le test qui échoue**

Couvre au minimum : témoin absent → pas prêt ; témoin présent mais d'une **version antérieure** → pas prêt ; témoin de la version courante → prêt ; WinRM injoignable → pas prêt, et distinct d'un témoin absent. Le seuil de délai reste une constante nommée à laquelle le test se réfère.

```python
# A stale marker is the dangerous case: a rebuild boots a disk that still
# carries the PREVIOUS run's marker. Presence alone would declare a console
# ready before its installation had begun.
check("un temoin d une version anterieure ne suffit pas",
      watch.marker_says_ready("provision_version=B2\n", expected="B3"), False)
check("le temoin de la version courante suffit",
      watch.marker_says_ready("provision_version=B3\n", expected="B3"), True)
```

- [ ] **Step 2: implémenter et déclarer la dépendance**

Ajoute `python3-winrm` au `apt:` de `console/nivuus-package.yaml`, avec un commentaire disant ce qui en dépend — c'est le **quatrième** trou de dépendance de cette série, après `firewalld`, `python3-jinja2` et `xorriso`. Vérifie le nom du paquet Debian (`apt-cache show python3-winrm`) avant de l'écrire.

Le mot de passe est celui que l'étape `secrets` écrit déjà : passe son chemin par `GUEST_PASS_FILE`, ne le lis pas toi-même.

- [ ] **Step 3: prouver que la version compte**

Fais rendre un témoin de version antérieure et vérifie que l'état n'est **pas** `READY`. Bytecode purgé, `python -B`.

- [ ] **Step 4: agrégateur et commit** — 33 suites, exit 0.

---

### Task 3 : l'ISO Windows survit au redémarrage

**Files:**
- Modify: `console/hooks/install.py`, `console/guest_steps.py`
- Test: `console/tests/test_console_install.py`, `console/tests/test_console_guest_steps.py`

**Interfaces:** aucune nouvelle. La tâche ferme un trou du chemin nominal.

**Le chemin nominal est aujourd'hui inatteignable, et personne ne s'en apercevrait avant une vraie installation.** L'opérateur donne un chemin d'ISO au wizard, qui tourne depuis le **support live**. Après le redémarrage, ce support a disparu : `activate` cherche un fichier qui n'existe plus, et refuse. Le seul chemin qui marche aujourd'hui est une machine où l'ISO se trouve déjà sur un disque permanent — c'est le cas de l'hôte de référence, ce qui a masqué le trou.

**La phase `install` est le seul moment où les deux racines coexistent** : elle voit le support live **et** la cible. C'est donc elle qui doit copier le média.

- [ ] **Step 1: écrire le test qui échoue**

Vérifie que `install` copie le média sous la racine cible, et que le chemin que `guest_steps` construira ensuite désigne cette copie et non l'original.

- [ ] **Step 2: implémenter, en refusant proprement**

Le fichier pèse environ 5 Go : **vérifie la place disponible avant de copier**, et refuse en nommant la cause plutôt que de remplir le disque à moitié. Le spec l'exigeait déjà et rien ne le faisait. Si le média est absent ou illisible au moment de l'install, refuse **là**, tant que l'opérateur est devant son écran — pas après le redémarrage.

- [ ] **Step 3: prouver le refus**

Simule une cible sans place et vérifie que le refus nomme la cause. Vérifie aussi qu'une copie déjà présente et complète n'est pas refaite.

- [ ] **Step 4: agrégateur et commit** — 33 suites, exit 0.

---

### Task 4 : la boucle silencieuse, les états inutiles, et la documentation

**Files:**
- Modify: `console/guest_steps.py`, `console/host/guest-ready-watch.py`, `console/README.md`, `CLAUDE.md`
- Test: les suites correspondantes

**Trois défauts qui n'apparaissent qu'à l'exécution, tous mesurés par la revue finale :**

1. **Boucle d'échec permanente et muette.** Une fois le domaine redéfini sans médias, `define.already_done()` rend `False` (il cherche les deux ISO), `define --replace` sans `--keyed-varstore` se heurte au garde varstore, et le témoin d'activation n'est jamais écrit : la phase rejoue à chaque démarrage, pour toujours. Le remède documenté (`undefine --nvram`) **réinstallerait Windows par-dessus une console qui marche**. Le prédicat doit reconnaître le domaine de régime comme un état **terminal légitime**, pas comme un travail à refaire.

2. **`define` ne compare que deux sous-chaînes d'ISO.** Changer `dedicated_nvme` reconstruit l'ISO mais **saute** le domaine, qui garde l'ancien NVMe. Le prédicat doit couvrir ce qui identifie réellement le domaine.

3. **Une VM au repos est l'état nominal, pas une anomalie.** Le minuteur journalise « installation non demarree » toutes les deux minutes, indéfiniment, sur une console éteinte qui va parfaitement bien. Il doit se taire quand il n'y a rien à dire.

- [ ] **Step 1: écrire les tests qui échouent**, un par défaut.

- [ ] **Step 2: implémenter.**

- [ ] **Step 3: prouver les trois**, bytecode purgé.

- [ ] **Step 4: la documentation dit ce qui est vrai**

**Aucune phrase sans que la commande correspondante ait été lancée.** Onze affirmations de ce projet ont déjà été corrigées par mesure.

Corrige `console/README.md` et **chirurgicalement** les paragraphes de `CLAUDE.md` qui parlent de cette phase — sans jamais réécrire le fichier, qu'une autre session modifie aussi. En particulier, `CLAUDE.md` affirme encore que l'hôte traite un port 5985 joignable comme la preuve du provisionnement : **c'est faux depuis le 2026-08-26**, et cette phrase a causé tout ce plan.

Écris aussi ce qui reste vrai et inconfortable : **rien de cette chaîne n'a jamais tourné en vrai**, l'interdiction n'ayant jamais été levée ; `retro: true` ne peut pas aboutir tant que `RETRO_SRC` vaut `/opt/retro` sur une cible ; et le premier redémarrage de Setup reste non mesuré.

- [ ] **Step 5: agrégateur et commit** — 33 suites, exit 0.
