# Nivuus - Quick Start Guide

Guide de démarrage rapide pour installer et configurer Nivuus en 15 minutes.

## Prérequis

- Debian 12 (Bookworm) ou Ubuntu 22.04+
- Intel CPU 12th gen+ (P-cores + E-cores) ou équivalent AMD
- NVIDIA GPU pour passthrough
- 32GB+ RAM recommandé
- Accès root

## Installation en 4 Étapes

### Étape 1: Cloner le Repository

```bash
cd /home/mallanic/Projects
git clone https://github.com/mallanic/Nivuus.git
cd Nivuus
```

### Étape 2: Identifier votre GPU

```bash
# Lister les devices NVIDIA
lspci -nn | grep -i nvidia

# Example output:
# 01:00.0 VGA [0300]: NVIDIA [10de:2786] (rev a1)
# 01:00.1 Audio [0403]: NVIDIA [10de:22bc] (rev a1)

# Notez les IDs: 10de:2786 (GPU) et 10de:22bc (Audio)
```

### Étape 3: Éditer GRUB (IMPORTANT!)

```bash
# Éditer /etc/default/grub
sudo nano /etc/default/grub

# Modifier la ligne GRUB_CMDLINE_LINUX_DEFAULT:
GRUB_CMDLINE_LINUX_DEFAULT="quiet splash isolcpus=0-15 intel_iommu=on iommu=pt vfio-pci.ids=10de:2786,10de:22bc"

# ⚠️ Remplacez 10de:2786,10de:22bc par les IDs de VOTRE GPU (étape 2)
# ⚠️ Pour AMD: Remplacez intel_iommu=on par amd_iommu=on
```

### Étape 4: Lancer l'Installation

`install.sh` n'existe plus (2026-08-27) : l'installation passe désormais par
l'ISO bootable et son assistant web, ou par le moteur d'installation en
ligne de commande — voir `installer/README.md`.

```bash
# Voie normale : construire l'ISO puis suivre l'assistant web au boot
cd installer && sudo make build-iso

# Essai sans toucher au disque : moteur en ligne de commande, arrêté avant
# les étapes destructives (partition/format/mount)
sudo python3 installer/install-engine/run.py --stop-after partition \
  --config /path/to/config.json
```

Le paramétrage manuel de GRUB décrit à l'étape 3 (`isolcpus`, IOMMU,
`vfio-pci.ids`) est désormais calculé par l'assistant/le moteur à partir du
matériel détecté — voir `installer/common/hardware.py` et, pour la console
de jeu Windows, `console/hardware.py`.

## Post-Installation

### Valider l'Installation

Après le redémarrage:

```bash
cd /home/mallanic/Projects/Nivuus
sudo ./scripts/validate-install.sh
```

Vous devriez voir:
```
✅ Passed:  XX
⚠️  Warnings: X
❌ Failed:  0
```

### Créer la VM Windows

```bash
# Générer et définir le domaine depuis le matériel détecté
python3 installer/windows-guest/domain.py xml     # inspecter
sudo python3 installer/windows-guest/domain.py define
```

#### Trouver les Adresses PCI du GPU

```bash
lspci | grep -i nvidia

# Output example:
# 01:00.0 VGA compatible controller: NVIDIA ...
# 01:00.1 Audio device: NVIDIA ...

# Dans le XML, modifier:
# domain='0x0000' bus='0x01' slot='0x00' function='0x0'  (GPU)
# domain='0x0000' bus='0x01' slot='0x00' function='0x1'  (Audio)
```

### Installer Windows

```bash
# Démarrer la VM avec ISO Windows
sudo virt-manager

# Ou en CLI:
sudo virt-install \
    --name Windows \
    --memory 16384 \
    --vcpus 14 \
    --disk /var/lib/libvirt/images/Windows.qcow2 \
    --cdrom /path/to/windows.iso \
    --os-variant win10 \
    --graphics spice

# Suivre l'installation Windows normalement
```

### Tester les Performances

```bash
# Test de stress thermique (après install Windows)
sudo ./tests/stress-test.sh

# Résultat attendu:
# Maximum CPU Package: ≤80°C ✅
```

## Configuration Windows (Post-Install)

### Activer GPU Dynamic P-State

Dans la VM Windows (PowerShell en Admin):

```powershell
# Activer Dynamic P-State pour économie d'énergie
Set-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}\0000" `
                 -Name DisableDynamicPstate `
                 -Value 0

# Redémarrer la VM
Restart-Computer
```

### Vérifier le GPU

```powershell
# Installer NVIDIA drivers (si pas déjà fait)
# https://www.nvidia.com/Download/index.aspx

# Vérifier
nvidia-smi

# Au repos, vous devriez voir:
# P-State: P8
# Power: 3-5W
```

### Configurer WinRM (Optionnel mais recommandé)

WinRM permet de communiquer avec la VM depuis l'hôte Linux pour monitoring et automatisation.

**Dans la VM Windows (PowerShell Admin):**

```powershell
# Copier le script depuis le partage ou télécharger
# Exécuter:
Set-ExecutionPolicy Bypass -Scope Process -Force
.\setup-winrm.ps1
```

**Sur l'hôte Linux:**

```bash
# Installer winrm-cli
cd /home/mallanic/Projects/Nivuus
sudo ./console/host/install-winrm-cli.sh

# Installer wrapper winvm
sudo install -m 755 console/host/winvm /usr/local/bin/winvm

# Configurer credentials
mkdir -p ~/.config/nivuus
cat > ~/.config/nivuus/winvm.conf << 'EOF'
VM_HOSTNAME="192.168.3.2"
VM_USERNAME="Administrateur"
VM_PASSWORD="your-password-here"
EOF
chmod 600 ~/.config/nivuus/winvm.conf

# Tester
winvm "hostname"
```

Voir [docs/winrm-setup.md](docs/winrm-setup.md) pour plus de détails.

## Vérification Finale

### Températures

```bash
# Sur le host (pendant que VM tourne)
watch -n 1 sensors coretemp-isa-0000

# Attendu au repos:
# Package: 40-50°C
# P-cores: 35-45°C
# E-cores: 30-40°C
```

### Consommation

```bash
# Mesurer consommation totale système (si wattmètre disponible)
# Attendu:
# Idle: 25-30W
# Gaming: 280-320W (selon jeu)
```

### Performance VM

Dans Windows, tester un jeu ou benchmark:
- 3DMark Time Spy
- Cyberpunk 2077
- CS2

Performance attendue: 90-95% du bare metal

## Troubleshooting Rapide

### VM ne démarre pas

```bash
# Vérifier les logs
sudo journalctl -u libvirtd -f

# Vérifier VFIO
lspci -nnk -d 10de:2786 | grep "driver in use"
# Doit afficher: vfio-pci
```

### Températures élevées

```bash
# Vérifier que l'optimisation est active
systemctl status cpu-thermal-optimization.service

# Vérifier fréquences
grep MHz /proc/cpuinfo | head -16
# P-cores doivent être ≤3600 MHz
```

### Fans bruyants pendant downloads VM

```bash
# Vérifier isolation CPUs
cat /proc/cmdline | grep isolcpus
# Doit contenir: isolcpus=0-15

# Vérifier pinning VM
virsh vcpupin Windows
# Tous les vCPUs doivent être sur 0-15
```

## Support

- **Documentation complète**: `/home/mallanic/Projects/Nivuus/docs/`
- **Tests**: `/home/mallanic/Projects/Nivuus/tests/`
- **Configurations**: `/home/mallanic/Projects/Nivuus/configs/`

### Documentation Détaillée

- [Optimisation Thermique](docs/thermal-optimization.md)
- [Configuration VM](docs/vm-configuration.md)
- [README Principal](README.md)

## Commandes Utiles

```bash
# Démarrer VM
sudo virsh start Windows

# Arrêter VM
sudo virsh shutdown Windows

# État VM
sudo virsh list --all

# Températures en temps réel
watch -n 1 sensors

# CPU frequencies
watch -n 1 'grep MHz /proc/cpuinfo | head -24'

# Validation complète
sudo ./scripts/validate-install.sh
```

## Prochaines Étapes

Une fois l'installation terminée et validée:

1. ✅ Configurer Moonlight/Parsec pour streaming
2. ✅ Optimiser réseau (si gaming à distance)
3. ✅ Configurer backups VM
4. ✅ Documenter votre configuration spécifique

Bienvenue sur Nivuus! 🎮
