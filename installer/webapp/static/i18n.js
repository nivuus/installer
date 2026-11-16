"use strict";

// Message catalogs for the wizard. One JSON file per language under
// /static/i18n/, English (en.json) being the reference every key exists in.
// The language is negotiated from navigator.languages: each tag is tried as
// is, then its primary subtag ("fr-CA" -> "fr-CA", "fr"); a language with no
// catalog falls back to English, and a key missing from a catalog falls back
// to the English text - never to the key, never to an empty string.
// Adding a language = adding a JSON file; no code lists the languages.
// Same convention as the suite's Home Assistant integrations
// (custom_components/*/translations/<lang>.json).

const I18N = { lang: "en", messages: {}, reference: {} };

async function fetchCatalog(tag) {
  try {
    const res = await fetch(`/static/i18n/${encodeURIComponent(tag)}.json`);
    return res.ok ? await res.json() : null;
  } catch (e) {
    return null;
  }
}

function candidateTags() {
  const tags = [];
  for (const raw of navigator.languages || [navigator.language || "en"]) {
    for (const tag of [raw, raw.split("-")[0]]) {
      if (tag && !tags.includes(tag)) tags.push(tag);
    }
  }
  return tags;
}

async function loadI18n() {
  I18N.reference = (await fetchCatalog("en")) || {};
  I18N.messages = I18N.reference;
  for (const tag of candidateTags()) {
    if (tag === "en") break;
    const catalog = await fetchCatalog(tag);
    if (catalog) { I18N.lang = tag; I18N.messages = catalog; break; }
  }
  document.documentElement.lang = I18N.lang;
  applyI18n(document);
}

// t("error.prefix", {message: "x"}) -> "Error: x"
function t(key, vars) {
  let text = I18N.messages[key];
  if (text === undefined) text = I18N.reference[key];
  if (text === undefined) text = key;
  for (const [name, value] of Object.entries(vars || {})) {
    text = text.split(`{${name}}`).join(String(value));
  }
  return text;
}

// data-i18n -> textContent, data-i18n-placeholder -> placeholder.
function applyI18n(root) {
  root.querySelectorAll("[data-i18n]").forEach(el => { el.textContent = t(el.dataset.i18n); });
  root.querySelectorAll("[data-i18n-placeholder]").forEach(el => {
    el.placeholder = t(el.dataset.i18nPlaceholder);
  });
}
