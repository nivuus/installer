# WinRM Setup - Nivuus

Guide d'installation et configuration de WinRM pour communiquer avec la VM Windows depuis l'hôte Linux.

## Vue d'ensemble

**WinRM** (Windows Remote Management) permet d'exécuter des commandes PowerShell et CMD depuis l'hôte Linux sur la VM Windows. C'est essentiel pour:
- Monitoring GPU et températures
- Tests de stress automatisés
- Gestion à distance de la VM
- Automatisation des tâches

## Architecture

```
Linux Host                    Windows VM
┌─────────────┐              ┌──────────────┐
│             │  WinRM/HTTP  │              │
│  winvm CLI  │─────────────>│  WinRM       │
│  wrapper    │   Port 5985  │  Service     │
│             │              │              │
└─────────────┘              └──────────────┘
      │                             │
      v                             v
  winrm-cli                   PowerShell
  (Go binary)                 Commands
```

## Installation

### Partie 1: Host Linux

#### Étape 1: Installer winrm-cli

```bash
cd /home/mallanic/Projects/Nivuus
sudo ./console/host/install-winrm-cli.sh
```

Ce script:
- Installe Go (si nécessaire)
- Clone winrm-cli depuis GitHub
- Compile le binaire
- Installe dans `/usr/local/bin/winrm`

**Vérification:**
```bash
winrm --version
# Doit afficher la version
```

#### Étape 2: Installer le wrapper winvm

```bash
sudo install -m 755 console/host/winvm /usr/local/bin/winvm
```

#### Étape 3: Configurer les credentials

```bash
# Créer le répertoire de configuration
mkdir -p ~/.config/nivuus

# Créer le fichier de configuration
cat > ~/.config/nivuus/winvm.conf << 'EOF'
VM_HOSTNAME="192.168.3.2"
VM_USERNAME="Administrateur"
VM_PASSWORD="your-password-here"
EOF

# Sécuriser le fichier (important!)
chmod 600 ~/.config/nivuus/winvm.conf
```

**Variables:**
- `VM_HOSTNAME`: IP de la VM (vérifier avec `virsh domifaddr Windows`)
- `VM_USERNAME`: Utilisateur Windows (généralement "Administrateur")
- `VM_PASSWORD`: Mot de passe Windows

### Partie 2: VM Windows

#### Étape 1: Copier le script de setup

Dans la VM Windows, copier le fichier:
```
\\vboxsvr\Nivuus\configs\setup-winrm.ps1
```

Ou depuis l'hôte, si vous avez un partage réseau configuré.

#### Étape 2: Exécuter le script

**Dans la VM Windows:**
1. Clic droit sur PowerShell → "Exécuter en tant qu'administrateur"
2. Naviguer vers le script:
   ```powershell
   cd C:\Path\To\Nivuus\configs
   ```
3. Exécuter:
   ```powershell
   Set-ExecutionPolicy Bypass -Scope Process -Force
   .\setup-winrm.ps1
   ```

Le script configure automatiquement:
- ✅ Service WinRM
- ✅ Authentification Basic
- ✅ Règles firewall
- ✅ Trusted hosts

#### Configuration manuelle (alternative)

Si vous préférez configurer manuellement:

```powershell
# Run as Administrator

# Enable WinRM
Enable-PSRemoting -Force -SkipNetworkProfileCheck

# Configure service
Set-Service -Name WinRM -StartupType Automatic
Start-Service -Name WinRM

# Enable Basic auth
Set-Item WSMan:\localhost\Service\Auth\Basic -Value $true
Set-Item WSMan:\localhost\Service\AllowUnencrypted -Value $true

# Firewall rules
New-NetFirewallRule -DisplayName "WinRM HTTP" `
    -Direction Inbound -Protocol TCP -LocalPort 5985 -Action Allow

# Trusted hosts
Set-Item WSMan:\localhost\Client\TrustedHosts -Value "*" -Force

# Test
Test-WSMan -ComputerName localhost
```

## Vérification

### Depuis l'hôte Linux

```bash
# Test simple
winvm "hostname"
# Doit retourner le nom de la VM Windows

# Test PowerShell
winvm 'powershell -Command "Get-ComputerInfo | Select-Object WindowsVersion"'

# Test GPU
winvm 'nvidia-smi --query-gpu=name,temperature.gpu --format=csv'
```

### Troubleshooting

#### Erreur: "connection refused"

**Cause:** WinRM service non démarré ou firewall bloque.

**Solution:**
```powershell
# Dans Windows
Start-Service WinRM
Get-Service WinRM  # Vérifier status
```

#### Erreur: "authentication failed"

**Cause:** Credentials incorrects.

**Solution:**
```bash
# Vérifier config
cat ~/.config/nivuus/winvm.conf

# Tester manuellement
/usr/local/bin/winrm -hostname 192.168.3.2 \
                     -username Administrateur \
                     -password "your-password" \
                     "hostname"
```

#### Erreur: "winrm: command not found"

**Cause:** winrm-cli pas installé.

**Solution:**
```bash
sudo console/host/install-winrm-cli.sh
```

#### Connexion lente

**Cause:** DNS reverse lookup timeout.

**Solution:**
```bash
# Ajouter l'IP dans /etc/hosts
echo "192.168.3.2 windows-vm" | sudo tee -a /etc/hosts

# Utiliser le nom dans config
VM_HOSTNAME="windows-vm"
```

## Sécurité

### ⚠️ Avertissements

WinRM en configuration Basic + HTTP:
- ❌ Credentials en clair sur le réseau
- ❌ Aucun chiffrement des données
- ❌ Vulnérable aux attaques MITM

**À UTILISER UNIQUEMENT:**
- Sur réseau privé isolé (bridge VM)
- Pour development/testing
- Avec réseau host-only ou isolated

### 🔒 Configuration Sécurisée (Production)

Pour un environnement de production:

1. **Utiliser HTTPS:**
   ```powershell
   # Créer certificat auto-signé
   New-SelfSignedCertificate -DnsName "windows-vm" -CertStoreLocation Cert:\LocalMachine\My

   # Configurer HTTPS listener
   winrm create winrm/config/Listener?Address=*+Transport=HTTPS `
       @{Hostname="windows-vm"; CertificateThumbprint="THUMBPRINT"}
   ```

2. **Désactiver Basic Auth:**
   ```powershell
   Set-Item WSMan:\localhost\Service\Auth\Basic -Value $false
   Set-Item WSMan:\localhost\Service\Auth\Kerberos -Value $true
   ```

3. **Limiter trusted hosts:**
   ```powershell
   # Au lieu de "*", spécifier l'IP de l'hôte
   Set-Item WSMan:\localhost\Client\TrustedHosts -Value "192.168.122.1"
   ```

4. **Fichier credentials sécurisé:**
   ```bash
   # Permissions strictes
   chmod 600 ~/.config/nivuus/winvm.conf

   # Ou utiliser un gestionnaire de secrets
   # (pass, vault, etc.)
   ```

## Usage Avancé

### Variables d'environnement

Alternative au fichier config:

```bash
export VM_HOSTNAME="192.168.3.2"
export VM_USERNAME="Administrateur"
export VM_PASSWORD="secret"

winvm "hostname"
```

### Scripts automatisés

```bash
#!/bin/bash
# Exemple: Monitoring GPU en boucle

while true; do
    TEMP=$(winvm 'nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits')
    POWER=$(winvm 'nvidia-smi --query-gpu=power.draw --format=csv,noheader,nounits')

    echo "$(date): GPU ${TEMP}°C, ${POWER}W"

    sleep 5
done
```

### Exécution de scripts PowerShell

```bash
# Inline
winvm 'powershell -Command "$x = 1 + 1; Write-Host $x"'

# Script file (dans la VM)
winvm 'powershell -File C:\Scripts\monitor.ps1'

# Here-doc
winvm "powershell -Command \"$(cat <<'PSEOF'
$processes = Get-Process | Sort-Object CPU -Descending | Select-Object -First 5
$processes | Format-Table Name, CPU, WorkingSet
PSEOF
)\""
```

## Intégration Nivuus

### Tests de stress

Le script `tests/stress-test.sh` utilise winvm pour:
- Démarrer stress CPU dans la VM
- Monitorer températures GPU
- Mesurer consommation électrique

**Exemple:**
```bash
# Stress CPU dans VM
winvm 'powershell -Command "$jobs = @(); for ($i = 0; $i -lt 14; $i++) { $jobs += Start-Job -ScriptBlock { while($true) { 1+1 } } }"'

# Monitorer pendant le stress
while true; do
    winvm 'nvidia-smi --query-gpu=temperature.gpu,power.draw --format=csv'
    sleep 1
done
```

### Monitoring système

```bash
# CPU usage
winvm 'powershell -Command "Get-Counter '\''\Processor(_Total)\% Processor Time'\''"'

# Mémoire
winvm 'powershell -Command "Get-Counter '\''\Memory\Available MBytes'\''"'

# Disque
winvm 'powershell -Command "Get-Counter '\''\PhysicalDisk(_Total)\% Disk Time'\''"'
```

## Exemples Utiles

### GPU Management

```bash
# Infos GPU complètes
winvm 'nvidia-smi'

# Température + Power
winvm 'nvidia-smi --query-gpu=temperature.gpu,power.draw,pstate --format=csv'

# Changer P-State (force performance)
winvm 'nvidia-smi -pm 1'  # Persistence mode
```

### Windows Management

```bash
# Version Windows
winvm 'powershell -Command "Get-ComputerInfo | Select WindowsVersion"'

# Uptime
winvm 'powershell -Command "(Get-Date) - (gcim Win32_OperatingSystem).LastBootUpTime"'

# Redémarrer
winvm 'shutdown /r /t 0'

# Processus top CPU
winvm 'powershell -Command "Get-Process | Sort CPU -Desc | Select -First 10"'
```

### Network

```bash
# IP addresses
winvm 'ipconfig'

# Test connectivity
winvm 'ping -n 4 8.8.8.8'

# Speedtest (si installé)
winvm 'speedtest-cli'
```

## Désinstallation

```bash
# Supprimer binaire
sudo rm /usr/local/bin/winrm

# Supprimer wrapper
sudo rm /usr/local/bin/winvm

# Supprimer config
rm -rf ~/.config/nivuus

# Dans Windows
# Désactiver WinRM:
winvm 'Disable-PSRemoting -Force'
```

## Références

- [WinRM CLI GitHub](https://github.com/masterzen/winrm-cli)
- [Microsoft WinRM Documentation](https://docs.microsoft.com/en-us/windows/win32/winrm/portal)
- [PowerShell Remoting Guide](https://docs.microsoft.com/en-us/powershell/scripting/learn/remoting/running-remote-commands)

## Support

Pour les problèmes WinRM:
1. Vérifier service Windows: `Get-Service WinRM`
2. Tester localement: `Test-WSMan -ComputerName localhost`
3. Vérifier firewall: `Get-NetFirewallRule -DisplayName "WinRM*"`
4. Logs Windows: Event Viewer → Applications and Services Logs → Microsoft → Windows → WinRM

---

**Note:** Cette configuration est optimisée pour un environnement de développement/testing sur réseau privé. Pour production, utilisez HTTPS + Kerberos ou Certificate authentication.
