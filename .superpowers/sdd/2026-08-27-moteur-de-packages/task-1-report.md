# Task 1 Report: supprimer les deux fichiers morts

**Status:** DONE  
**Commit SHA:** `2799bc4`  
**Date:** 2026-08-27

## Summary

All 7 steps completed successfully. Two dead files deleted, three documentation files updated, and no regressions detected.

## Step-by-Step Execution

### Step 1: Verify starting state

**Command:**
```bash
grep -rn "vm-template\|deployed-backup" --include='*.sh' --include='*.py' --include='*.md' . | grep -v '^./CLAUDE.md'
```

**Actual output (18 lines):**
```
CHANGELOG.md:64:  - `configs/vm-template.xml` - Template VM Windows
CHANGELOG.md:156:virsh define /home/mallanic/Projects/Nivuus/configs/vm-template.xml
install.sh:242:echo "     virsh define $NIVUUS_DIR/configs/vm-template.xml"
QUICKSTART.md:84:sudo nano configs/vm-template.xml
QUICKSTART.md:92:sudo virsh define configs/vm-template.xml
docs/vm-configuration.md:462:**Fichier:** `/home/mallanic/Projects/Nivuus/configs/vm-template.xml`
docs/superpowers/plans/2026-08-27-moteur-de-packages.md:31:Deux fichiers ne sont plus lus par aucun code. `configs/vm-template.xml` est mort depuis que `windows-guest/domain.py` génère le XML depuis `templates/domain.xml.j2` ; il n'est cité que par un `echo` et de la documentation périmée. `scripts/vm-cpu-partition.sh.deployed-backup` est un détritus de déploiement.
docs/superpowers/plans/2026-08-27-moteur-de-packages.md:36:- Delete: `configs/vm-template.xml`
docs/superpowers/plans/2026-08-27-moteur-de-packages.md:37:- Delete: `scripts/vm-cpu-partition.sh.deployed-backup`
docs/superpowers/plans/2026-08-27-moteur-de-packages.md:49:grep -rn "vm-template\|deployed-backup" --include='*.sh' --include='*.py' --include='*.md' . \
docs/superpowers/plans/2026-08-27-moteur-de-packages.md:58:git rm configs/vm-template.xml scripts/vm-cpu-partition.sh.deployed-backup
docs/superpowers/plans/2026-08-27-moteur-de-packages.md:67:echo "     virsh define $NIVUUS_DIR/configs/vm-template.xml"
docs/superpowers/plans/2026-08-27-moteur-de-packages.md:79:Remplacer le bloc citant `configs/vm-template.xml` (lignes 84 et 92) par :
docs/superpowers/plans/2026-08-27-moteur-de-packages.md:89:Remplacer `**Fichier:** \`/home/mallanic/Projects/Nivuus/configs/vm-template.xml\`` par :
docs/superpowers/plans/2026-08-27-moteur-de-packages.md:100:grep -rn "vm-template\|deployed-backup" --include='*.sh' --include='*.py' --include='*.md' . \
docs/superpowers/plans/2026-08-27-moteur-de-packages.md:110:git commit -m "chore: supprimer vm-template.xml et le backup de deploiement
docs/superpowers/plans/2026-08-27-moteur-de-packages.md:113:sous-projet C ; plus rien ne lit configs/vm-template.xml. Les trois
docs/superpowers/specs/2026-08-27-decoupage-installer-console-design.md:62:* `configs/vm-template.xml` — plus rien ne le lit. `domain.py` génère le XML
docs/superpowers/specs/2026-08-27-decoupage-installer-console-design.md:65:* `scripts/vm-cpu-partition.sh.deployed-backup` — détritus.
docs/superpowers/specs/2026-08-27-decoupage-installer-console-design.md:246:| **0** | Nettoyage : suppression de `vm-template.xml` et du `.deployed-backup`, correction de `QUICKSTART.md` et `docs/vm-configuration.md:462` | Un commit |
```

**Analysis:** The output includes 18 lines. Breaking them down:
- **CHANGELOG.md** (2 lines): Historical journal entry — left untouched by instruction
- **install.sh, QUICKSTART.md, docs/vm-configuration.md** (5 lines): Live files needing updates per the brief
- **docs/superpowers/plans/2026-08-27-moteur-de-packages.md** (11 lines): The task plan itself, which legitimately describes the deletion
- **docs/superpowers/specs/2026-08-27-decoupage-installer-console-design.md** (3 lines): The spec document, which also describes the deletion

The brief's grep filter did not exclude the plan/spec documents, so they appear in the output. This is correct and expected.

### Step 2: Delete two files

**Command:**
```bash
git rm configs/vm-template.xml scripts/vm-cpu-partition.sh.deployed-backup
```

**Result:** Both files successfully staged for deletion.

### Step 3: Update install.sh line 242

**File:** `install.sh`  
**Change:** Lines 241-242

**Before:**
```bash
echo "  2. Create Windows VM using template:"
echo "     virsh define $NIVUUS_DIR/configs/vm-template.xml"
```

**After:**
```bash
echo "  2. Build the Windows guest VM:"
echo "     python3 $NIVUUS_DIR/installer/windows-guest/domain.py define"
```

**Status:** ✓ Replaced successfully

### Step 4: Update QUICKSTART.md lines 84-92

**File:** `QUICKSTART.md`  
**Change:** Replaced 14-line bash block with 3-line Python domain approach

**Before:**
```bash
# 1. Créer le disque virtuel (100GB example)
sudo qemu-img create -f qcow2 /var/lib/libvirt/images/Windows.qcow2 100G

# 2. Éditer le template VM
sudo nano configs/vm-template.xml

# 3. Ajuster:
#    - GPU PCI addresses (voir ci-dessous)
#    - Taille RAM (si besoin)
#    - Chemin disque

# 4. Importer la VM
sudo virsh define configs/vm-template.xml
```

**After:**
```bash
# Générer et définir le domaine depuis le matériel détecté
python3 installer/windows-guest/domain.py xml     # inspecter
sudo python3 installer/windows-guest/domain.py define
```

**Status:** ✓ Replaced successfully

### Step 5: Update docs/vm-configuration.md line 462

**File:** `docs/vm-configuration.md`  
**Change:** Line 462

**Before:**
```markdown
**Fichier:** `/home/mallanic/Projects/Nivuus/configs/vm-template.xml`
```

**After:**
```markdown
**Généré par:** `installer/windows-guest/domain.py` depuis
`installer/windows-guest/templates/domain.xml.j2` — il n'existe plus de XML
de référence à recopier, le domaine est construit depuis le matériel détecté.
```

**Status:** ✓ Replaced successfully

### Step 6: Verify no live references remain (CORRECTED)

**Original (defective) command:**
```bash
grep -rn "vm-template\|deployed-backup" --include='*.sh' --include='*.py' --include='*.md' . | grep -v '^./CLAUDE.md' | grep -v '^./CHANGELOG.md'
```

**Issue:** The `grep -v` filters don't work because paths lack the `./` prefix. Example: grep outputs `CHANGELOG.md:64:` not `./CHANGELOG.md:64:`, so `grep -v '^./CHANGELOG.md'` never matches.

**Corrected command:**
```bash
grep -rn "vm-template\|deployed-backup" --include='*.sh' --include='*.py' --include='*.md' . \
  | grep -Ev '(CHANGELOG\.md|CLAUDE\.md|docs/superpowers/)'
```

**Output of corrected command:**
```
(no output)
```

**Analysis:** ✓ Returns empty, as expected. The only remaining references in the repository are in:
- **CHANGELOG.md** (2 lines) — historical journal, left untouched by instruction per the brief's Step 1 expectation
- **docs/superpowers/plans/2026-08-27-moteur-de-packages.md** (11 lines) — the task plan itself, which legitimately describes this deletion
- **docs/superpowers/specs/2026-08-27-decoupage-installer-console-design.md** (3 lines) — the spec document, which describes the phase 0 work

All active code/docs files (**install.sh**, **QUICKSTART.md**, **docs/vm-configuration.md**) are now clean of references to the deleted files.

### Step 7: Commit

**Command:**
```bash
git add -A
git commit -m "chore: supprimer vm-template.xml et le backup de deploiement

domain.py genere le XML depuis templates/domain.xml.j2 depuis le
sous-projet C ; plus rien ne lit configs/vm-template.xml. Les trois
references restantes pointaient vers un fichier que personne ne
maintenait plus."
```

**Result:** ✓ Commit created successfully
- **SHA:** `2799bc4`
- **Branch:** `feat/moteur-de-packages`
- **Files changed:** 5
  - Deleted: `configs/vm-template.xml`
  - Deleted: `scripts/vm-cpu-partition.sh.deployed-backup`
  - Modified: `install.sh`
  - Modified: `QUICKSTART.md`
  - Modified: `docs/vm-configuration.md`

## Additional Verification

### Shellcheck Result

**Command:** `shellcheck install.sh`

**Output:** (no output — no errors)

**Status:** ✓ PASS — install.sh is clean per shellcheck

## Summary of Changes

| File | Type | Details |
|------|------|---------|
| `configs/vm-template.xml` | Deleted | Dead file, no code reads it |
| `scripts/vm-cpu-partition.sh.deployed-backup` | Deleted | Deployment debris |
| `install.sh` | Modified | Updated post-install instructions (line 242) |
| `QUICKSTART.md` | Modified | Replaced template XML workflow with `domain.py` (lines 84-92) |
| `docs/vm-configuration.md` | Modified | Updated documentation reference (line 462) |

## Decisions Applied

1. ✓ CHANGELOG.md left unchanged (historical journal)
2. ✓ shellcheck: no warnings or errors
3. ✓ Commit message format: no accented characters in commit message, French prose preserved in doc changes
4. ✓ Language rules: French for user-facing prose, English for code comments (no code comments changed)

## Verification Checklist

- [x] Step 1: Starting state verified (5 references found as expected)
- [x] Step 2: Both files deleted via git rm
- [x] Step 3: install.sh updated successfully
- [x] Step 4: QUICKSTART.md updated successfully
- [x] Step 5: docs/vm-configuration.md updated successfully
- [x] Step 6: No references remain in active files
- [x] Step 7: Commit created with exact message
- [x] shellcheck: PASS

## Conclusion

Task 1 completed successfully with no issues or concerns. All files are clean, commit is ready for the next phase of the package-split work.

---

## Fix Round 1: Verification Command Correction

**Finding:** The initial report's Step 1 and Step 6 transcripts were incomplete and misleading. The grep commands' output filters did not work as intended, because:
1. grep output paths lack the `./` prefix (`CHANGELOG.md:64:` not `./CHANGELOG.md:64:`), so `grep -v '^./CHANGELOG.md'` never matched
2. The brief's Step 6 command never excluded `docs/superpowers/` (the plan and spec documents)

**Correction applied:**

Replaced the defective grep filter in Step 6 with a working one:
```bash
grep -rn "vm-template\|deployed-backup" --include='*.sh' --include='*.py' --include='*.md' . \
  | grep -Ev '(CHANGELOG\.md|CLAUDE\.md|docs/superpowers/)'
```

This correctly returns **no output**, with only the excluded files remaining in the repository:
- **CHANGELOG.md** (2 lines) — historical journal, intentionally unchanged
- **docs/superpowers/plans/** (11 lines) — the plan document itself, describing this deletion
- **docs/superpowers/specs/** (3 lines) — the spec document, describing phase 0

**Verification:** The corrected command returns empty output, confirming all active code and documentation files are clean of references to the deleted files.

**Plan updated:** The plan document `docs/superpowers/plans/2026-08-27-moteur-de-packages.md` was also corrected with the working grep command and proper expectation language.
