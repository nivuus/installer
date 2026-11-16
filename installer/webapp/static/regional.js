"use strict";

// Locale, keyboard layout, time zone and Wi-Fi country: questions, each with
// a proposal detected from the browser, never a constant. The choices come
// from the medium's own Debian lists (/api/regional, common/regional.py);
// nothing here lists languages or countries.
//
// Detection, all from standard browser APIs:
//   locale    navigator.languages, BCP 47 tag -> glibc name ("ja-JP" ->
//             "ja_JP.UTF-8"), else the first supported locale of the language
//   keyboard  the region of that tag lowercased when it names an XKB layout
//             ("de-CH" -> "ch", "ja-JP" -> "jp"), else the language subtag
//             ("de" -> "de"), else "us"
//   time zone Intl.DateTimeFormat().resolvedOptions().timeZone
//   country   the region subtag of the browser language ("fr-BE" -> "BE")
// Every proposal stays editable: the browser's language is a guess about
// the owner, not a fact.

let regionalChoices = { locales: [], keyboards: [], missing: [] };

function browserTags() {
  return (navigator.languages || [navigator.language || ""]).filter(Boolean);
}

function regionOf(tag) {
  try { return new Intl.Locale(tag).maximize().region || ""; } catch (e) { return ""; }
}

function languageOf(tag) {
  try { return new Intl.Locale(tag).language || ""; } catch (e) { return ""; }
}

function proposeLocale(locales) {
  for (const tag of browserTags()) {
    const lang = languageOf(tag);
    const exact = `${lang}_${regionOf(tag)}.UTF-8`;
    if (locales.includes(exact)) return exact;
    const same = locales.find(l => l.startsWith(`${lang}_`) && l.includes(".UTF-8"));
    if (same) return same;
  }
  return locales.includes("en_US.UTF-8") ? "en_US.UTF-8" : (locales[0] || "en_US.UTF-8");
}

function proposeLayout(layouts) {
  for (const tag of browserTags()) {
    for (const code of [regionOf(tag).toLowerCase(), languageOf(tag)]) {
      if (code && layouts.includes(code)) return code;
    }
  }
  return "us";
}

function localeLabel(name) {
  // "pt_BR.UTF-8" -> "<language (region) in the UI language> — pt_BR.UTF-8".
  const base = name.split(/[.@]/)[0].replace("_", "-");
  try {
    const shown = new Intl.DisplayNames([I18N.lang], { type: "language" }).of(base);
    return shown && shown !== base ? `${shown} — ${name}` : name;
  } catch (e) {
    return name;
  }
}

// A list the medium lacks becomes a free text field: the operator types the
// value, the engine checks it against the target.
function asTextInput(select, value) {
  const input = document.createElement("input");
  input.name = select.name; input.id = select.id; input.value = value;
  select.replaceWith(input);
}

function fillSelect(select, items, selected) {
  select.innerHTML = "";
  for (const { value, label } of items) {
    const opt = document.createElement("option");
    opt.value = value; opt.textContent = label;
    if (value === selected) opt.selected = true;
    select.appendChild(opt);
  }
}

function fillVariants(layout) {
  const entry = regionalChoices.keyboards.find(k => k.layout === layout);
  const variants = (entry && entry.variants) || [];
  fillSelect($("#keyboard_variant"),
    [{ value: "", label: t("system.keyboard_variant_none") },
     ...variants.map(v => ({ value: v.variant, label: v.description }))], "");
}

async function loadRegional() {
  try {
    const res = await fetch("/api/regional");
    if (res.ok) regionalChoices = await res.json();
  } catch (e) { /* the fields below fall back to free text */ }

  const locales = regionalChoices.locales;
  const proposedLocale = proposeLocale(locales);
  if (locales.length) {
    fillSelect($("#locale"), locales.map(l => ({ value: l, label: localeLabel(l) })),
               proposedLocale);
  } else {
    asTextInput($("#locale"), proposedLocale);
  }

  const layouts = regionalChoices.keyboards.map(k => k.layout);
  const proposedLayout = proposeLayout(layouts);
  if (layouts.length) {
    fillSelect($("#keyboard_layout"), regionalChoices.keyboards.map(k => ({
      value: k.layout, label: `${k.description} (${k.layout})` })), proposedLayout);
    fillVariants(proposedLayout);
    $("#keyboard_layout").addEventListener("change", e => fillVariants(e.target.value));
  } else {
    asTextInput($("#keyboard_layout"), proposedLayout);
    asTextInput($("#keyboard_variant"), "");
  }

  if (regionalChoices.missing.length) {
    const note = $("#regional_missing");
    note.textContent = t("system.list_missing", { files: regionalChoices.missing.join(", ") });
    note.hidden = false;
  }

  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (zone) $("#timezone").value = zone;
  const country = browserTags().map(regionOf).find(Boolean);
  if (country) $("#wifi_country").value = country;
}
