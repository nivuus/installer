// Drives the wizard's i18n.js and regional.js for real under node: language
// negotiation, English fallback, and the regional proposals built from the
// browser's language. No DOM library: the scripts only need fetch,
// navigator, Intl and two document calls, given here.
//
// Run: node scripts/tests/test_webapp_i18n.mjs
import { readFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const repo = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const staticDir = join(repo, "installer", "webapp", "static");
const failures = [];
const check = (label, got, want) => {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    failures.push(`${label}: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
  }
};

function context(languages) {
  const ctx = {
    Intl, console,
    navigator: { languages, language: languages[0] },
    document: { documentElement: {}, querySelectorAll: () => [] },
    fetch: async (url) => {
      const file = join(staticDir, decodeURIComponent(url.replace("/static/", "")));
      return existsSync(file)
        ? { ok: true, json: async () => JSON.parse(readFileSync(file, "utf8")) }
        : { ok: false };
    },
  };
  vm.createContext(ctx);
  for (const name of ["i18n.js", "regional.js"]) {
    vm.runInContext(readFileSync(join(staticDir, name), "utf8"), ctx);
  }
  return ctx;
}

const run = (ctx, code) => vm.runInContext(code, ctx);

for (const [languages, want] of [
  [["ja-JP", "fr-FR"], "fr"],   // no Japanese catalog yet: the next language
  [["fr-CA"], "fr"],            // primary subtag
  [["en-GB", "fr-FR"], "en"],   // English first stays English
  [["ko-KR"], "en"],            // nothing available: the reference
]) {
  const ctx = context(languages);
  await run(ctx, "loadI18n()");
  check(`${languages} -> catalog`, run(ctx, "I18N.lang"), want);
  check(`${languages} -> <html lang>`, ctx.document.documentElement.lang, want);
}

const fr = context(["fr-FR"]);
await run(fr, "loadI18n()");
check("translated text", run(fr, 't("nav.install")'), "Installer");
check("placeholders filled", run(fr, 't("error.prefix", {message: "x"})'), "Erreur : x");
run(fr, 'I18N.messages = {}');
check("a key a catalog lacks falls back to English", run(fr, 't("nav.install")'), "Install");

const LOCALES = JSON.stringify(["ar_SA.UTF-8", "en_US.UTF-8", "fr_FR.UTF-8", "ja_JP.UTF-8"]);
const LAYOUTS = JSON.stringify(["ara", "ch", "de", "fr", "jp", "us"]);
for (const [languages, locale, layout] of [
  [["ja-JP"], "ja_JP.UTF-8", "jp"],
  [["ar-EG"], "ar_SA.UTF-8", "us"],   // same language, other region; no "eg" layout
  [["de-CH"], "en_US.UTF-8", "ch"],   // no German locale offered; Swiss layout
  [["fr-FR"], "fr_FR.UTF-8", "fr"],
  [["ko-KR"], "en_US.UTF-8", "us"],
]) {
  const ctx = context(languages);
  check(`${languages} -> locale`, run(ctx, `proposeLocale(${LOCALES})`), locale);
  check(`${languages} -> layout`, run(ctx, `proposeLayout(${LAYOUTS})`), layout);
  check(`${languages} -> country`, run(ctx, "browserTags().map(regionOf).find(Boolean)"),
        languages[0].split("-")[1]);
}

if (failures.length) {
  console.log(`FAIL (${failures.length})`);
  for (const f of failures) console.log(`  - ${f}`);
  process.exit(1);
}
console.log("OK - wizard i18n runtime tests passed");
