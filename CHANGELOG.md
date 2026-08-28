# Changelog

Historique des versions et modifications de Nivuus.

## [Non publié]

### Ajouté
- Contrat `nivuus.dev/v1` : `requires.packages` déclare les packages
  pré-requis. Les installations sont ordonnées topologiquement, et un
  pré-requis manquant est refusé au wizard plutôt qu'en cours d'installation.

### Modifié
- Une clé inconnue sous `requires:` est désormais une erreur de manifeste.
  Aucun manifeste existant n'est concerné.

## [2.1.0] - 2026-04-09

### Optimisé
- **Temperatures idle CPU réduites** (~63°C → estimé ~40-45°C)
  - P-cores: governor `performance` → `powersave` (intel_pstate monte toujours à 3600 MHz sous charge)
  - P-cores EPP: `performance` → `balance_power` (réduit tensions idle)
  - E-cores EPP: `balance_performance` → `power` (efficacité maximale)

- **Courbe de ventilation plus agressive** (nct6798)
  - Ancienne: 20°C/20% → 70°C/70% → 75°C/100%
  - Nouvelle: 35°C/30% → 50°C/50% → 60°C/75% → 70°C/90% → 80°C/100%
  - Détection dynamique du hwmon nct6798 (plus robuste aux changements d'index)

### Amélioré
- **Script `optimize-cpu-thermal.sh`**
  - Ajout configuration EPP (Energy Performance Preference) par core
  - Ajout optimisation courbe ventilateur intégrée au script
  - Affichage EPP dans le tableau de status
  - Prédiction températures idle dans le résumé

- **C-states CPU débloqués** (`/etc/kernel/cmdline`)
  - `intel_idle.max_cstate=1` → `intel_idle.max_cstate=3` (autorise C1E + C6)
  - Permet aux cores de dormir quand la VM est idle (bureau Windows, pas de jeu)
  - KVM `halt_poll_ns=200μs` préserve la latence gaming automatiquement
  - **Nécessite un reboot** pour prendre effet

### Notes
- Pâte thermique à remplacer (serveur 24/7 depuis >6 mois)

---

## [2.0.0] - 2025-10-18

### Ajouté
- **Optimisation thermique complète** CPU
  - Limitation P-cores à 3600 MHz (objectif 80°C atteint)
  - Limitation E-cores à 2000 MHz avec governor powersave
  - Service systemd pour persistance au boot
  - Tests de validation thermique automatisés

- **Optimisation GPU**
  - Activation Dynamic P-State pour RTX 4070
  - Réduction consommation idle: 38W → 3.9W (-89%)
  - Documentation complète activation P-States

- **Documentation complète**
  - `docs/thermal-optimization.md` - Guide optimisation thermique
  - `docs/vm-configuration.md` - Configuration QEMU/KVM détaillée
  - `docs/test-results.md` - Résultats tests réels
  - `QUICKSTART.md` - Guide démarrage rapide
  - `README.md` - Documentation principale

- **Scripts d'installation**
  - `install.sh` - Installation complète automatisée
  - `scripts/optimize-cpu-thermal.sh` - Optimisation thermique
  - `scripts/validate-install.sh` - Validation installation
  - `tests/stress-test.sh` - Test de charge combiné

- **Configurations**
  - `configs/vm-template.xml` - Template VM Windows
  - `configs/grub-example.conf` - Configuration GRUB exemple
  - `configs/systemd/cpu-thermal-optimization.service` - Service systemd

### Modifié
- **Configuration VM** (Breaking Change)
  - Changement: 16 vCPUs → 14 vCPUs (tous P-cores)
  - Emulator threads: CPUs 12-15,20-23 → 14-15 (isolés)
  - Raison: Élimination contention avec host OS
  - Impact: Résolution problème fans bruyants

- **CPU Pinning**
  - vCPUs 0-13 → Physical CPUs 0-13 (1:1 mapping)
  - Emulator + IOthreads → CPUs 14-15
  - Tous les threads VM sur CPUs isolés (0-15)

### Optimisé
- **Consommation électrique**
  - Idle: 75W → 28W (-47W, -63%)
  - Gaming: Réduction -40W moyenne

- **Thermique**
  - CPU Package max: 100°C → 78-80°C (-20°C)
  - E-cores: Réduction -3 à -7°C
  - Système silencieux (<40 dB vs 60+ dB)

### Corrigé
- **Issue #1**: Fans à fond pendant downloads VM
  - Cause: Emulator threads sur E-cores non isolés
  - Fix: Migration emulator vers P-cores isolés (14-15)
  - Status: ✅ Résolu

- **Issue #2**: Thermal throttling CPU
  - Cause: Fréquence stock trop élevée (5200 MHz)
  - Fix: Limitation progressive → 3600 MHz (80°C exact)
  - Status: ✅ Résolu

- **Issue #3**: Consommation idle excessive
  - Cause: GPU P0 permanent + E-cores performance mode
  - Fix: Dynamic P-State + E-cores powersave
  - Status: ✅ Résolu

### Performance
- **CPU**: -30% (benchmarks synthétiques)
- **Gaming**: -3% à -12% FPS (selon jeu, acceptable)
- **Latence réseau**: <1ms (LAN VM↔Host)
- **Stabilité**: Aucun throttling sous charge

### Tests
- Test P-cores seuls: 80°C ✅
- Test E-cores seuls: 47°C ✅
- Test combiné E+P: 78°C ✅ (2°C marge)
- Test GPU: ⚠️ Incomplet (nécessite benchmark réel)

## [1.0.0] - 2024-XX-XX

### Initial Release

- Configuration basique QEMU/KVM
- GPU Passthrough RTX 4070 (VFIO)
- CPU Pinning manuel
- 16 vCPUs (12 P-cores + 4 E-cores)
- Isolation CPUs via isolcpus
- Fréquences CPU stock (pas d'optimisation thermique)

### Problèmes Connus v1.0
- ❌ Températures atteignant 100°C sous charge
- ❌ Fans bruyants pendant downloads
- ❌ Consommation idle élevée (75W)
- ⚠️ Thermal throttling occasionnel

---

## Notes de Migration

### 1.0 → 2.0

**Breaking Changes:**
- Configuration VM modifiée (14 vCPU vs 16)
- CPU pinning modifié
- Nécessite mise à jour XML VM

**Migration:**
```bash
# Backup configuration actuelle
virsh dumpxml Windows > ~/Windows-v1-backup.xml

# Éditer configuration avec nouveau pinning
virsh edit Windows

# Ou utiliser le nouveau template
virsh undefine Windows
virsh define /home/mallanic/Projects/Nivuus/configs/vm-template.xml
```

**Post-Migration:**
```bash
# Installer service thermal
sudo cp /home/mallanic/Projects/Nivuus/configs/systemd/cpu-thermal-optimization.service \
        /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cpu-thermal-optimization.service

# Valider
sudo /home/mallanic/Projects/Nivuus/scripts/validate-install.sh
```

---

## Roadmap

### Version 2.1 (À venir)
- [ ] Support AMD CPUs (Ryzen 7000+)
- [ ] Script auto-detection hardware
- [ ] Optimisation réseau avancée (SR-IOV)
- [ ] Monitoring dashboard (Grafana)
- [ ] Backup/restore automatisé

### Version 3.0 (Futur)
- [ ] Multi-VM support
- [ ] Load balancing dynamique
- [ ] GPU scheduling intelligent
- [ ] Container integration (Docker)
- [ ] Web UI pour configuration

---

## Support

Pour signaler des bugs ou demander des features:
- GitHub Issues: https://github.com/mallanic/Nivuus/issues
- Documentation: `/home/mallanic/Projects/Nivuus/docs/`

---

## Auteurs

- **mallanic** - Développement et optimisations

## Licence

MIT License - Voir LICENSE file
