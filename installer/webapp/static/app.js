"use strict";

// Wizard step order. "wifi" is conditional but always present in the nav.
const STEPS = ["disk", "system", "account", "wan", "wifi", "features", "review"];
const STEP_LABELS = {
  disk: "Disque", system: "Système", account: "Compte", wan: "WAN",
  wifi: "WiFi", features: "Options", review: "Récap",
};

// Feature catalogue: key -> {label, desc, default}. gpu-passthrough is toggled
// automatically when a discrete GPU is detected.
const FEATURES = [
  { key: "kvm-vfio", label: "KVM / VFIO", desc: "QEMU/KVM, libvirt, IOMMU, hugepages (cloud gaming)", def: true },
  { key: "thermal", label: "Optimisation thermique", desc: "Gestion fréquences P/E-cores + ventilateurs", def: true },
  { key: "networking", label: "Réseau (bridges + WAN)", desc: "Bridges NetworkManager + PPPoE/DHCP", def: true },
  { key: "wifi-ap", label: "Point d'accès WiFi", desc: "hostapd dual-band (lié à l'étape WiFi)", def: false },
  { key: "firewall", label: "Pare-feu", desc: "firewalld + fail2ban + nftables", def: true },
  { key: "docker", label: "Docker", desc: "Moteur Docker + docker compose", def: false },
  { key: "home-assistant", label: "Home Assistant + MQTT", desc: "Domotique + agent système MQTT", def: false },
  { key: "gpu-passthrough", label: "GPU passthrough", desc: "Liaison vfio-pci (auto-détecté)", def: false, auto: true },
  { key: "retro", label: "Rétrogaming (VM Windows)", desc: "RetroArch + bibliothèque de jeux rétro dans Steam (nécessite KVM / VFIO)", def: false },
];

let hw = null;          // detected hardware snapshot
let current = 0;        // current step index
let selectedDisk = null;

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

document.addEventListener("DOMContentLoaded", init);

async function init() {
  buildStepNav();
  buildFeatureList();
  wireNav();
  wireConditionalFields();
  wireRetroDependency();
  await loadHardware();
  showStep(0);
}

function buildStepNav() {
  const nav = $("#stepnav");
  nav.innerHTML = STEPS.map((s, i) =>
    `<span data-i="${i}">${i + 1}. ${STEP_LABELS[s]}</span>`).join("");
}

function buildFeatureList() {
  $("#features").innerHTML = FEATURES.map(f => `
    <label class="feature">
      <input type="checkbox" data-feature="${f.key}" ${f.def ? "checked" : ""}>
      <span class="fmeta"><span class="fname">${f.label}</span>
      <span class="fdesc">${f.desc}</span></span>
    </label>`).join("");
}

async function loadHardware() {
  try {
    const res = await fetch("/api/hardware");
    hw = await res.json();
  } catch (e) {
    hw = { disks: [], ethernet: [], wifi: [], gpus: [], cpu: {}, passthrough_candidates: [] };
  }
  renderDisks();
  renderWan();
  renderWifi();
  renderGpu();
}

function renderDisks() {
  const box = $("#disks");
  if (!hw.disks.length) { box.textContent = "Aucun disque détecté."; return; }
  box.innerHTML = hw.disks.map(d => `
    <div class="card" data-disk="${d.path}">
      <input type="radio" name="disk" value="${d.path}">
      <span class="meta">
        <span class="name">${d.path} — ${d.size}</span>
        <span class="detail">${d.model}${d.removable ? " · amovible" : ""}${d.rotational ? " · HDD" : " · SSD"}</span>
      </span>
    </div>`).join("");
  $$("#disks .card").forEach(card => card.addEventListener("click", () => {
    $$("#disks .card").forEach(c => c.classList.remove("selected"));
    card.classList.add("selected");
    card.querySelector("input").checked = true;
    selectedDisk = card.dataset.disk;
  }));
}

function renderWan() {
  const sel = $("#wan_interface");
  sel.innerHTML = (hw.ethernet || []).map(e =>
    `<option value="${e.name}">${e.name} (${e.mac}${e.carrier ? ", câble détecté" : ""})</option>`).join("");
}

function renderWifi() {
  const apCapable = (hw.wifi || []).filter(w => w.ap_capable);
  const box = $("#wifi_ifaces");
  if (!apCapable.length) {
    box.innerHTML = "<em>Aucune interface WiFi compatible AP détectée.</em>";
  } else {
    box.innerHTML = "Interfaces WiFi AP détectées : " +
      apCapable.map(w => `<code>${w.name}</code>`).join(", ");
  }
}

function renderGpu() {
  const candidates = hw.passthrough_candidates || [];
  const cb = $('input[data-feature="gpu-passthrough"]');
  const wrap = cb.closest(".feature");
  if (candidates.length) {
    cb.checked = true;
    const ids = candidates.flatMap(g => g.ids).join(", ");
    wrap.querySelector(".fdesc").textContent =
      `Détecté : ${candidates.map(g => g.description).join("; ")} (${ids})`;
  } else {
    cb.checked = false; cb.disabled = true;
    wrap.querySelector(".fdesc").textContent = "Aucun GPU dédié détecté.";
  }
}

function wireNav() {
  $("#nextBtn").addEventListener("click", () => move(1));
  $("#prevBtn").addEventListener("click", () => move(-1));
  $("#installBtn").addEventListener("click", startInstall);
  $$("#stepnav span").forEach(s =>
    s.addEventListener("click", () => showStep(parseInt(s.dataset.i, 10))));
}

function wireConditionalFields() {
  $("#wan_mode").addEventListener("change", e => {
    $("#pppoe_fields").hidden = e.target.value !== "pppoe";
  });
  $("#wifi_enabled").addEventListener("change", e => {
    $("#wifi_fields").hidden = !e.target.checked;
    // Keep the wifi-ap feature in sync with this toggle.
    const f = $('input[data-feature="wifi-ap"]');
    if (f) f.checked = e.target.checked;
  });
}

// retro (RetroArch, via the `retro` package, provisioned on the Windows
// guest VM) makes no sense without the VM itself: say so at the moment of
// the choice, not as a submit-time error (the backend still refuses the
// combination too - see models.py).
function wireRetroDependency() {
  const vm = $('input[data-feature="kvm-vfio"]');
  const retro = $('input[data-feature="retro"]');
  if (!vm || !retro) return;
  // The catalogue entry already carries the "on" description; no need for
  // a second, hand-copied literal that could drift from it.
  const defaultDesc = FEATURES.find(f => f.key === "retro").desc;
  const sync = () => {
    retro.disabled = !vm.checked;
    if (!vm.checked) retro.checked = false;
    const wrap = retro.closest(".feature");
    wrap.querySelector(".fdesc").textContent = vm.checked
      ? defaultDesc
      : "Nécessite la VM Windows (KVM / VFIO) : cochez-la d'abord.";
  };
  vm.addEventListener("change", sync);
  sync();
}

function move(delta) {
  const target = current + delta;
  if (target < 0 || target >= STEPS.length) return;
  if (delta > 0 && !validateStep(STEPS[current])) return;
  showStep(target);
}

function showStep(i) {
  current = i;
  STEPS.forEach((s, idx) => {
    $(`.step[data-step="${s}"]`).hidden = idx !== i;
    const tab = $(`#stepnav span[data-i="${idx}"]`);
    tab.classList.toggle("active", idx === i);
    tab.classList.toggle("done", idx < i);
  });
  $("#prevBtn").disabled = i === 0;
  const last = i === STEPS.length - 1;
  $("#nextBtn").hidden = last;
  $("#installBtn").hidden = !last;
  if (last) $("#summary").textContent = JSON.stringify(buildConfig(), null, 2);
}

function validateStep(step) {
  if (step === "disk" && !selectedDisk) { alert("Sélectionnez un disque cible."); return false; }
  if (step === "account") {
    const u = $('[name="username"]').value.trim();
    if (!u) { alert("Le nom d'utilisateur est requis."); return false; }
    const pw = $('[name="password"]').value;
    const key = $('[name="ssh_key"]').value.trim();
    if (!pw && !key) { alert("Définissez un mot de passe ou une clé SSH."); return false; }
  }
  return true;
}

function val(name) { const el = $(`[name="${name}"]`); return el ? el.value.trim() : ""; }
function checked(name) { const el = $(`[name="${name}"]`); return el ? el.checked : false; }

function buildConfig() {
  const features = $$('input[data-feature]')
    .filter(c => c.checked).map(c => c.dataset.feature);
  features.unshift("os-base");

  const candidates = hw && hw.passthrough_candidates || [];
  const cfg = {
    disk: { path: selectedDisk, use_lvm: $("#use_lvm").checked },
    hostname: val("hostname") || "nivuus",
    domain: val("domain"),
    locale: val("locale"),
    timezone: val("timezone") || "Europe/Paris",
    user: {
      username: val("username"),
      password: val("password") || null,
      ssh_key: val("ssh_key"),
      ssh_port: parseInt(val("ssh_port") || "22", 10),
      password_auth: checked("password_auth"),
    },
    wan: {
      mode: $("#wan_mode").value,
      interface: $("#wan_interface").value || "",
      vlan: parseInt(val("wan_vlan") || "835", 10),
      pppoe_user: val("pppoe_user"),
      pppoe_password: val("pppoe_password"),
    },
    wifi_ap: buildWifi(),
    gpu_passthrough: {
      enabled: features.includes("gpu-passthrough") && candidates.length > 0,
      ids: candidates.flatMap(g => g.ids),
    },
    cpu: { isolcpus: (hw && hw.cpu && hw.cpu.isolcpus) || "" },
    features: Array.from(new Set(features)),
  };
  return cfg;
}

function buildWifi() {
  const enabled = $("#wifi_enabled").checked;
  const ap = (hw && hw.wifi || []).filter(w => w.ap_capable).map(w => w.name);
  return {
    enabled,
    country: val("wifi_country") || "FR",
    private_ssid: val("private_ssid") || "Nivuus",
    private_passphrase: val("private_passphrase"),
    public_ssid: val("public_ssid"),
    public_passphrase: val("public_passphrase"),
    dual_band: checked("dual_band"),
    // Heuristic interface assignment; the engine writes 2.4 then 5GHz confs.
    interfaces_24: ap.slice(0, 2),
    interfaces_5: ap.slice(0, 2),
  };
}

async function startInstall() {
  const cfg = buildConfig();
  $("#installBtn").disabled = true;
  let res;
  try {
    res = await fetch("/api/install/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cfg),
    });
  } catch (e) {
    alert("Impossible de joindre le serveur d'installation."); $("#installBtn").disabled = false; return;
  }
  const data = await res.json();
  if (!res.ok || !data.ok) {
    alert("Erreur : " + (data.error || res.status));
    $("#installBtn").disabled = false;
    return;
  }
  $("#wizard").hidden = true;
  $(".nav-buttons").hidden = true;
  $("#stepnav").hidden = true;
  $("#progress").hidden = false;
  streamProgress();
}

function streamProgress() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/progress`);
  const log = $("#log");
  ws.onmessage = (ev) => {
    const e = JSON.parse(ev.data);
    $("#barFill").style.width = `${e.pct}%`;
    $("#pctLabel").textContent = `${e.pct} %`;
    const li = document.createElement("li");
    li.className = e.level;
    li.textContent = `[${e.step}] ${e.msg}`;
    log.appendChild(li);
    log.scrollTop = log.scrollHeight;
    if (e.level === "done") finish(true, e.msg);
    if (e.level === "error") finish(false, e.msg);
  };
  ws.onclose = () => {
    // Reconnect briefly in case the install is still running.
    setTimeout(() => { if ($("#finished").hidden) streamProgress(); }, 1500);
  };
}

function finish(ok, msg) {
  const box = $("#finished");
  box.hidden = false;
  box.innerHTML = `<div class="banner ${ok ? "ok" : "err"}">${
    ok ? "✅ " : "❌ "}${msg}${ok ? " — vous pouvez redémarrer la machine." : ""}</div>`;
}
