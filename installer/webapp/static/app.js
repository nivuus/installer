"use strict";

// Wizard step order. "wifi" is conditional but always present in the nav.
const STEPS = ["disk", "system", "account", "wan", "wifi", "features", "review"];
// Step labels are the catalog's "steps.<name>" entries (static/i18n.js).

// Feature catalogue: key -> default; label and description are the catalog's
// "features.<key>.label/.desc". The VM (kvm-vfio, gpu-passthrough, retro) is
// gone from this list: it is the console package now, and the wizard does not
// render packages yet (see /api/packages) - deliberately deferred, not
// forgotten.
const FEATURES = [
  { key: "thermal", def: true },
  { key: "networking", def: true },
  { key: "wifi-ap", def: false },
  { key: "firewall", def: true },
  { key: "docker", def: false },
  { key: "home-assistant", def: false },
];

let hw = null;          // detected hardware snapshot
let current = 0;        // current step index
let selectedDisk = null;

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

document.addEventListener("DOMContentLoaded", init);

async function init() {
  await loadI18n();
  buildStepNav();
  buildFeatureList();
  wireNav();
  wireConditionalFields();
  await Promise.all([loadHardware(), loadRegional()]);
  showStep(0);
}

function buildStepNav() {
  const nav = $("#stepnav");
  nav.innerHTML = STEPS.map((s, i) =>
    `<span data-i="${i}">${i + 1}. ${t(`steps.${s}`)}</span>`).join("");
}

function buildFeatureList() {
  $("#features").innerHTML = FEATURES.map(f => `
    <label class="feature">
      <input type="checkbox" data-feature="${f.key}" ${f.def ? "checked" : ""}>
      <span class="fmeta"><span class="fname">${t(`features.${f.key}.label`)}</span>
      <span class="fdesc">${t(`features.${f.key}.desc`)}</span></span>
    </label>`).join("");
}

async function loadHardware() {
  try {
    const res = await fetch("/api/hardware");
    hw = await res.json();
  } catch (e) {
    hw = { disks: [], ethernet: [], wifi: [], gpus: [] };
  }
  renderDisks();
  renderWan();
  renderWifi();
}

function renderDisks() {
  const box = $("#disks");
  if (!hw.disks.length) { box.textContent = t("disk.none"); return; }
  box.innerHTML = hw.disks.map(d => `
    <div class="card" data-disk="${d.path}">
      <input type="radio" name="disk" value="${d.path}">
      <span class="meta">
        <span class="name">${d.path} — ${d.size}</span>
        <span class="detail">${d.model}${d.removable ? ` · ${t("disk.removable")}` : ""} · ${d.rotational ? t("disk.hdd") : t("disk.ssd")}</span>
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
    `<option value="${e.name}">${e.name} (${e.mac}${e.carrier ? `, ${t("wan.cable_detected")}` : ""})</option>`).join("");
}

function renderWifi() {
  const apCapable = (hw.wifi || []).filter(w => w.ap_capable);
  const box = $("#wifi_ifaces");
  if (!apCapable.length) {
    box.innerHTML = `<em>${t("wifi.no_ap")}</em>`;
  } else {
    box.innerHTML = `${t("wifi.ap_found")} ` +
      apCapable.map(w => `<code>${w.name}</code>`).join(", ");
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
  if (step === "disk" && !selectedDisk) { alert(t("validate.disk")); return false; }
  if (step === "account") {
    const u = $('[name="username"]').value.trim();
    if (!u) { alert(t("validate.username")); return false; }
    const pw = $('[name="password"]').value;
    const key = $('[name="ssh_key"]').value.trim();
    if (!pw && !key) { alert(t("validate.credentials")); return false; }
  }
  if (step === "wifi" && $("#wifi_enabled").checked &&
      !/^[A-Za-z]{2}$/.test(val("wifi_country"))) {
    alert(t("validate.wifi_country")); return false;
  }
  return true;
}

function val(name) { const el = $(`[name="${name}"]`); return el ? el.value.trim() : ""; }
function checked(name) { const el = $(`[name="${name}"]`); return el ? el.checked : false; }

function buildConfig() {
  const features = $$('input[data-feature]')
    .filter(c => c.checked).map(c => c.dataset.feature);
  features.unshift("os-base");

  const cfg = {
    disk: { path: selectedDisk, use_lvm: $("#use_lvm").checked },
    hostname: val("hostname") || "nivuus",
    domain: val("domain"),
    locale: val("locale"),
    timezone: val("timezone") || "Etc/UTC",
    keyboard: { layout: val("keyboard_layout") || "us", variant: val("keyboard_variant") },
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
    features: Array.from(new Set(features)),
  };
  return cfg;
}

function buildWifi() {
  const enabled = $("#wifi_enabled").checked;
  const ap = (hw && hw.wifi || []).filter(w => w.ap_capable).map(w => w.name);
  return {
    enabled,
    country: val("wifi_country").toUpperCase(),
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
    alert(t("error.unreachable")); $("#installBtn").disabled = false; return;
  }
  const data = await res.json();
  if (!res.ok || !data.ok) {
    const known = data.code && t(`error.code.${data.code}`);
    const message = known && known !== `error.code.${data.code}` ? known : (data.error || res.status);
    alert(t("error.prefix", { message }));
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
    ok ? "✅ " : "❌ "}${msg}${ok ? ` — ${t("progress.reboot")}` : ""}</div>`;
}
