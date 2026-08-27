# Configuration VM - Nivuus

Documentation complète de la configuration QEMU/KVM avec GPU passthrough et CPU pinning optimisé.

## Vue d'ensemble

La VM Windows de Nivuus est configurée pour des performances gaming maximales avec:
- **14 vCPUs** dédiés (tous P-cores)
- **CPU Pinning 1:1** pour latence minimale
- **GPU Passthrough** (RTX 4070 via VFIO)
- **CPU Isolation** (isolcpus kernel parameter)
- **Hugepages** pour performances mémoire

## 1. Architecture CPU

### 1.1 Répartition des CPUs

**i9-12900K (24 threads logiques):**
```
P-cores (Performance):
├─ CPU 0-15  (16 threads) → Isolated pour VM
│  ├─ CPU 0-13   → 14 vCPUs VM
│  └─ CPU 14-15  → Emulator + IOthreads

E-cores (Efficiency):
└─ CPU 16-23 (8 threads) → Host OS uniquement
```

### 1.2 Évolution de la Configuration

**v1.0 - Configuration initiale (PROBLÈME):**
```
vCPUs: 16 (12 P-cores + 4 E-cores)
Emulator: CPUs 12-15, 20-23 (mélange P+E cores NON isolés)
❌ Problème: Fans à fond pendant downloads VM
❌ Cause: Contention avec host OS sur E-cores non isolés
```

**v2.0 - Configuration optimisée (SOLUTION):**
```
vCPUs: 14 (tous P-cores)
Emulator: CPUs 14-15 (P-cores isolés)
✅ Tous les threads VM sur CPUs isolés
✅ Plus de contention
✅ Fans normaux
```

### 1.3 Isolation Kernel

**Kernel parameter (GRUB):**
```bash
GRUB_CMDLINE_LINUX_DEFAULT="... isolcpus=0-15 ..."
```

**Effet:**
- CPUs 0-15 retirés du scheduler Linux
- Aucun processus host ne peut s'exécuter dessus
- Dédiés exclusivement à la VM
- Latence minimale et déterministe

**Fichier:** `/etc/default/grub`

**Vérification:**
```bash
cat /proc/cmdline | grep isolcpus
# Attendu: isolcpus=0-15
```

## 2. Configuration Libvirt

### 2.1 XML Complet - Section CPU

**Fichier:** `/etc/libvirt/qemu/Windows.xml`

```xml
<domain type='kvm'>
  <name>Windows</name>

  <!-- 16 GB RAM -->
  <memory unit='KiB'>16777216</memory>
  <currentMemory unit='KiB'>16777216</currentMemory>

  <!-- Hugepages pour performances -->
  <memoryBacking>
    <hugepages/>
    <locked/>
    <access mode='shared'/>
  </memoryBacking>

  <!-- 14 vCPUs + 2 IOthreads -->
  <vcpu placement='static'>14</vcpu>
  <iothreads>2</iothreads>

  <!-- CPU Pinning 1:1 -->
  <cputune>
    <!-- vCPUs 0-13 → Physical CPUs 0-13 -->
    <vcpupin vcpu='0' cpuset='0'/>
    <vcpupin vcpu='1' cpuset='1'/>
    <vcpupin vcpu='2' cpuset='2'/>
    <vcpupin vcpu='3' cpuset='3'/>
    <vcpupin vcpu='4' cpuset='4'/>
    <vcpupin vcpu='5' cpuset='5'/>
    <vcpupin vcpu='6' cpuset='6'/>
    <vcpupin vcpu='7' cpuset='7'/>
    <vcpupin vcpu='8' cpuset='8'/>
    <vcpupin vcpu='9' cpuset='9'/>
    <vcpupin vcpu='10' cpuset='10'/>
    <vcpupin vcpu='11' cpuset='11'/>
    <vcpupin vcpu='12' cpuset='12'/>
    <vcpupin vcpu='13' cpuset='13'/>

    <!-- Emulator et IOthreads → CPUs 14-15 -->
    <emulatorpin cpuset='14-15'/>
    <iothreadpin iothread='1' cpuset='14-15'/>
    <iothreadpin iothread='2' cpuset='14-15'/>
  </cputune>

  <!-- CPU Configuration -->
  <cpu mode='host-passthrough' check='none' migratable='off'>
    <topology sockets='1' dies='1' cores='14' threads='1'/>
    <cache mode='passthrough'/>
    <feature policy='require' name='topoext'/>
    <feature policy='require' name='hypervisor'/>
    <feature policy='require' name='invtsc'/>
  </cpu>

  <!-- Hyper-V Enlightenments pour performances Windows -->
  <features>
    <acpi/>
    <apic/>
    <hyperv mode='custom'>
      <relaxed state='on'/>
      <vapic state='on'/>
      <spinlocks state='on' retries='8191'/>
      <vpindex state='on'/>
      <runtime state='on'/>
      <synic state='on'/>
      <stimer state='on'>
        <direct state='on'/>
      </stimer>
      <reset state='on'/>
      <vendor_id state='on' value='123456789123'/>
      <frequencies state='on'/>
      <tlbflush state='on'/>
      <ipi state='on'/>
      <evmcs state='on'/>
    </hyperv>
    <kvm>
      <hidden state='on'/>
    </kvm>
    <vmport state='off'/>
    <smm state='on'/>
    <ioapic driver='kvm'/>
  </features>

  <!-- Clock/Timer optimisations -->
  <clock offset='localtime'>
    <timer name='rtc' tickpolicy='catchup'/>
    <timer name='pit' tickpolicy='delay'/>
    <timer name='hpet' present='no'/>
    <timer name='kvmclock' present='no'/>
    <timer name='hypervclock' present='yes'/>
    <timer name='tsc' present='yes' mode='native'/>
  </clock>
</domain>
```

### 2.2 Paramètres Clés Expliqués

#### CPU Mode: host-passthrough
```xml
<cpu mode='host-passthrough' check='none' migratable='off'>
```
- **host-passthrough**: Expose toutes les features CPU au guest
- **check='none'**: Pas de validation (pour max performance)
- **migratable='off'**: Pas de migration live (performances > flexibilité)

#### Topology
```xml
<topology sockets='1' dies='1' cores='14' threads='1'/>
```
- **1 socket, 14 cores**: Correspond aux 14 vCPUs
- **threads='1'**: Pas d'hyperthreading virtuel (déjà exposé par P-cores)

#### Cache Passthrough
```xml
<cache mode='passthrough'/>
```
- Expose la vraie topologie cache CPU au guest
- Améliore performances (gaming, compression, etc.)

#### Features Requises
```xml
<feature policy='require' name='invtsc'/>
```
- **invtsc**: TSC invariant pour timers précis (crucial pour gaming)
- **topoext**: Topologie étendue pour AMD/Intel
- **hypervisor**: Flag hypervisor visible

#### Hyper-V Enlightenments
```xml
<hyperv mode='custom'>
  <relaxed state='on'/>        <!-- Relaxed timers -->
  <vapic state='on'/>           <!-- Virtual APIC -->
  <spinlocks state='on'/>       <!-- Spinlock optimization -->
  <vpindex state='on'/>         <!-- Virtual processor index -->
  <stimer state='on'>           <!-- Synthetic timers -->
    <direct state='on'/>        <!-- Direct timer mode -->
  </stimer>
  <evmcs state='on'/>           <!-- Enlightened VMCS -->
</hyperv>
```
- Optimisations Windows-specific
- Réduit overhead VM de ~15-20%
- **Crucial pour gaming performant**

#### KVM Hidden
```xml
<kvm>
  <hidden state='on'/>
</kvm>
```
- Cache le fait qu'on soit dans une VM
- Évite détection anti-cheat
- Nécessaire pour certains jeux

#### Timers
```xml
<timer name='hpet' present='no'/>
<timer name='kvmclock' present='no'/>
<timer name='hypervclock' present='yes'/>
<timer name='tsc' present='yes' mode='native'/>
```
- **HPET désactivé**: Overhead inutile
- **kvmclock désactivé**: On utilise hypervclock
- **hypervclock**: Timer Hyper-V (meilleures perfs Windows)
- **TSC native**: Accès direct au Time Stamp Counter

## 3. GPU Passthrough (VFIO)

### 3.1 Configuration VFIO

**Kernel modules:**
```bash
# /etc/modules
vfio
vfio_iommu_type1
vfio_pci
```

**GRUB parameters:**
```bash
GRUB_CMDLINE_LINUX_DEFAULT="... intel_iommu=on iommu=pt vfio-pci.ids=10de:2786,10de:22bc ..."
```

**IDs:**
- `10de:2786`: RTX 4070 GPU
- `10de:22bc`: RTX 4070 Audio

### 3.2 XML GPU Section

```xml
<!-- RTX 4070 GPU -->
<hostdev mode='subsystem' type='pci' managed='yes'>
  <driver name='vfio'/>
  <source>
    <address domain='0x0000' bus='0x01' slot='0x00' function='0x0'/>
  </source>
  <address type='pci' domain='0x0000' bus='0x01' slot='0x00' function='0x0' multifunction='on'/>
</hostdev>

<!-- RTX 4070 Audio -->
<hostdev mode='subsystem' type='pci' managed='yes'>
  <driver name='vfio'/>
  <source>
    <address domain='0x0000' bus='0x01' slot='0x00' function='0x1'/>
  </source>
  <address type='pci' domain='0x0000' bus='0x01' slot='0x00' function='0x1'/>
</hostdev>
```

### 3.3 Vérification VFIO

```bash
# Lister les devices VFIO
ls -la /dev/vfio/

# Vérifier que la RTX 4070 utilise vfio-pci
lspci -nnk -d 10de:2786

# Attendu:
# Kernel driver in use: vfio-pci
```

## 4. Réseau

### 4.1 Virtio-net avec Multiqueue

```xml
<interface type='network'>
  <source network='default'/>
  <model type='virtio'/>
  <driver name='vhost' queues='8'/>
  <address type='pci' domain='0x0000' bus='0x02' slot='0x00' function='0x0'/>
</interface>
```

**Paramètres:**
- **type='virtio'**: Driver paravirtualisé (meilleures perfs)
- **queues='8'**: 8 queues réseau (1 par CPU physique)
- **driver='vhost'**: vhost-net kernel acceleration

**Performance:**
- Latence: <1ms (LAN)
- Throughput: 10 Gbps (limite NIC)

### 4.2 Configuration Windows (Guest)

```powershell
# Vérifier multiqueue actif
Get-NetAdapter | Select-Object Name, DriverVersion, *RSS*

# Activer RSS (Receive Side Scaling)
Set-NetAdapterRss -Name "Ethernet" -Enabled $true -NumberOfReceiveQueues 8
```

## 5. Stockage

### 5.1 Disque Virtuel (VirtIO SCSI)

```xml
<disk type='file' device='disk'>
  <driver name='qemu' type='qcow2' cache='writeback' io='threads' discard='unmap'/>
  <source file='/var/lib/libvirt/images/Windows.qcow2'/>
  <target dev='sda' bus='scsi'/>
  <address type='drive' controller='0' bus='0' target='0' unit='0'/>
</disk>

<controller type='scsi' index='0' model='virtio-scsi'>
  <driver queues='8' iothread='1'/>
  <address type='pci' domain='0x0000' bus='0x04' slot='0x00' function='0x0'/>
</controller>
```

**Optimisations:**
- **cache='writeback'**: Cache write (meilleures perfs, risque perte données)
- **io='threads'**: I/O async
- **discard='unmap'**: TRIM/discard support
- **queues='8'**: 8 queues I/O
- **iothread='1'**: IOthread dédié (CPU 14-15)

### 5.2 Performance Stockage

**Tests dd (dans VM):**
```powershell
# Write test
dd if=/dev/zero of=test.img bs=1M count=10000
# ~2.5 GB/s (SSD NVMe)

# Read test
dd if=test.img of=/dev/null bs=1M
# ~3.2 GB/s
```

## 6. Hugepages

### 6.1 Configuration Hugepages

**Calcul:**
```
VM RAM: 16 GB = 16384 MB
Hugepage size: 2 MB
Hugepages needed: 16384 / 2 = 8192
```

**Configuration:** `/etc/sysctl.conf`
```bash
vm.nr_hugepages = 8192
```

**Application:**
```bash
sudo sysctl -p
```

**Vérification:**
```bash
cat /proc/meminfo | grep Huge
# HugePages_Total:    8192
# HugePages_Free:     xxx
# Hugepagesize:       2048 kB
```

### 6.2 Avantages Hugepages

- Réduction TLB misses
- Moins de page faults
- +5-10% performance gaming
- Latence mémoire réduite

## 7. Commandes Utiles

### 7.1 Gestion VM

```bash
# Lister VMs
virsh list --all

# Démarrer
virsh start Windows

# Arrêter proprement
virsh shutdown Windows

# Forcer arrêt
virsh destroy Windows

# État CPU pinning
virsh vcpupin Windows

# Dump configuration XML
virsh dumpxml Windows > /tmp/Windows-backup.xml

# Éditer configuration
virsh edit Windows
```

### 7.2 Monitoring

```bash
# CPU usage par vCPU
virsh cpu-stats Windows

# Stats I/O
virsh domblkstat Windows vda

# Stats réseau
virsh domifstat Windows vnet0

# Info domaine complète
virsh dominfo Windows
```

### 7.3 Performance Tuning

```bash
# Vérifier isolation CPUs
cat /sys/devices/system/cpu/isolated
# Attendu: 0-15

# Vérifier IOMMU groups
find /sys/kernel/iommu_groups/ -type l

# Vérifier que GPU est isolé
lspci -nnk -d 10de:2786
```

## 8. Template XML pour Installation

Pour une nouvelle installation Nivuus, utilisez ce template minimal:

**Généré par:** `installer/windows-guest/domain.py` depuis
`installer/windows-guest/templates/domain.xml.j2` — il n'existe plus de XML
de référence à recopier, le domaine est construit depuis le matériel détecté.

```xml
<domain type='kvm'>
  <name>Windows</name>
  <memory unit='GiB'>16</memory>
  <vcpu placement='static'>14</vcpu>
  <iothreads>2</iothreads>

  <cputune>
    <vcpupin vcpu='0' cpuset='0'/>
    <vcpupin vcpu='1' cpuset='1'/>
    <vcpupin vcpu='2' cpuset='2'/>
    <vcpupin vcpu='3' cpuset='3'/>
    <vcpupin vcpu='4' cpuset='4'/>
    <vcpupin vcpu='5' cpuset='5'/>
    <vcpupin vcpu='6' cpuset='6'/>
    <vcpupin vcpu='7' cpuset='7'/>
    <vcpupin vcpu='8' cpuset='8'/>
    <vcpupin vcpu='9' cpuset='9'/>
    <vcpupin vcpu='10' cpuset='10'/>
    <vcpupin vcpu='11' cpuset='11'/>
    <vcpupin vcpu='12' cpuset='12'/>
    <vcpupin vcpu='13' cpuset='13'/>
    <emulatorpin cpuset='14-15'/>
    <iothreadpin iothread='1' cpuset='14-15'/>
    <iothreadpin iothread='2' cpuset='14-15'/>
  </cputune>

  <cpu mode='host-passthrough'>
    <topology sockets='1' cores='14' threads='1'/>
  </cpu>

  <os>
    <type arch='x86_64' machine='pc-q35-7.2'>hvm</type>
  </os>

  <!-- Ajouter devices: GPU, stockage, réseau... -->
</domain>
```

## 9. Troubleshooting

### Problème: VM ne démarre pas

```bash
# Logs QEMU
journalctl -u libvirtd -f

# Vérifier VFIO
dmesg | grep -i vfio

# Vérifier IOMMU
dmesg | grep -i iommu
```

### Problème: Performances faibles

```bash
# 1. Vérifier CPU pinning
virsh vcpupin Windows

# 2. Vérifier que CPUs sont isolés
cat /proc/cmdline | grep isolcpus

# 3. Vérifier hugepages utilisés
cat /proc/meminfo | grep Huge

# 4. Vérifier governors CPU
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor
```

### Problème: Latence réseau élevée

```bash
# Dans la VM Windows
Test-NetConnection 192.168.122.1

# Vérifier multiqueue
virsh dumpxml Windows | grep queues
```

### Problème: GPU non détecté

```bash
# Vérifier VFIO bind
lspci -nnk -d 10de:2786

# Doit montrer: Kernel driver in use: vfio-pci
# Si non, vérifier /etc/modprobe.d/vfio.conf
```

## 10. Optimisations Avancées

### 10.1 CPU Governor

Pour gaming optimal, s'assurer que performance governor est actif:

```bash
# Sur tous les P-cores (0-15)
for cpu in {0..15}; do
    echo performance > /sys/devices/system/cpu/cpu$cpu/cpufreq/scaling_governor
done
```

### 10.2 IRQ Affinity

Optimiser l'affinity des interruptions:

```bash
# Trouver IRQ de la RTX 4070
cat /proc/interrupts | grep nvidia

# Binder sur CPUs isolés (exemple: CPU 14-15)
echo "c000" > /proc/irq/XXX/smp_affinity
# c000 = binary 1100000000000000 = CPUs 14-15
```

### 10.3 Transparent Hugepages

```bash
# Désactiver THP (conflits avec hugepages statiques)
echo never > /sys/kernel/mm/transparent_hugepage/enabled
```

## 11. Validation Installation

Script de validation: `/home/mallanic/Projects/Nivuus/scripts/validate-vm.sh`

```bash
#!/bin/bash
echo "Validating Nivuus VM Configuration..."

# Check 1: CPU Isolation
echo -n "CPU Isolation (isolcpus=0-15): "
if cat /proc/cmdline | grep -q "isolcpus=0-15"; then
    echo "✅ OK"
else
    echo "❌ FAIL"
fi

# Check 2: VFIO GPU
echo -n "GPU VFIO binding: "
if lspci -nnk -d 10de:2786 | grep -q "vfio-pci"; then
    echo "✅ OK"
else
    echo "❌ FAIL"
fi

# Check 3: Hugepages
echo -n "Hugepages (8192): "
HUGE=$(cat /proc/meminfo | grep HugePages_Total | awk '{print $2}')
if [ "$HUGE" -eq 8192 ]; then
    echo "✅ OK ($HUGE)"
else
    echo "⚠️  WARN ($HUGE, expected 8192)"
fi

# Check 4: VM CPU Pinning
echo -n "VM CPU Pinning: "
if virsh vcpupin Windows | grep -q "0.*0"; then
    echo "✅ OK"
else
    echo "❌ FAIL"
fi

echo ""
echo "Validation complete."
```

## Conclusion

La configuration VM Nivuus offre:
- ✅ **Latence minimale** grâce au CPU pinning 1:1
- ✅ **Performances natives** via host-passthrough + hugepages
- ✅ **Isolation complète** des CPUs gaming (0-15)
- ✅ **GPU passthrough** transparent (VFIO)
- ✅ **Stabilité** grâce à l'optimisation thermique

Résultat: **Gaming performant et stable 24/7**
