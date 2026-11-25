// Tar skjermbilder og ferdig rendret tekst/HTML av hovedvisningene på radaren, for LLM-er som ikke kjører JS.
//   node deploy/snapshot/snapshot.mjs https://radar.vikingsea.no ut/
import { chromium } from "playwright";
import fs from "node:fs";
const [base = "https://radar.vikingsea.no", out = "snapshot-out"] = process.argv.slice(2);
for (const d of ["skjermbilder", "tekst", "html"]) fs.mkdirSync(`${out}/${d}`, { recursive: true });
const views = [
  ["01-oversikt", "/"],
  ["02-arbeidsflate-forhoyet", "/arbeid?tier=Forh%C3%B8yet"],
  ["03-arbeidsflate-alle", "/arbeid"],
  ["04-oppdragsgivere", "/oppdragsgivere"],
  ["05-leverandorer", "/leverandorer"],
  ["07-doffin", "/doffin"],
  ["08-metode", "/metode"],
  ["09-fakturaer", "/fakturaer"],
];
const browser = await chromium.launch();
async function grab(page, name, hash, full = true) {
  await page.goto(`${base}/#${hash}`, { waitUntil: "networkidle", timeout: 90000 });
  await page.waitForTimeout(2500);
  await page.screenshot({ path: `${out}/skjermbilder/${name}.png`, fullPage: full });
  const text = await page.evaluate(() => document.querySelector("main")?.innerText || document.body.innerText);
  fs.writeFileSync(`${out}/tekst/${name}.md`, `# ${name}\n\nURL: ${base}/#${hash}\n\n${text}\n`);
  const html = await page.evaluate(() => {
    const d = document.documentElement.cloneNode(true);
    d.querySelectorAll("script").forEach((s) => s.remove());
    return "<!doctype html>\n" + d.outerHTML;
  });
  fs.writeFileSync(`${out}/html/${name}.html`, html);
  console.log("ok", name, text.length, "tegn");
}
const desk = await browser.newPage({ viewport: { width: 1440, height: 900 } });
for (const [n, h] of views) await grab(desk, n, h).catch((e) => console.log("FEIL", n, e.message));
await desk.goto(`${base}/#/leverandorer`, { waitUntil: "networkidle" });
await desk.waitForTimeout(2000);
const href = await desk.evaluate(() => [...document.querySelectorAll("a")].map((a) => a.getAttribute("href") || "").find((h) => /leverandorer\/\d{9}/.test(h)));
if (href) await grab(desk, "06-leverandor-eksempel", href.replace(/^#/, "")).catch((e) => console.log("FEIL leverandør", e.message));
const mob = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
for (const [n, h] of [["10-mobil-oversikt", "/"], ["11-mobil-arbeidsflate", "/arbeid?tier=Forh%C3%B8yet"]])
  await grab(mob, n, h, false).catch((e) => console.log("FEIL", n, e.message));
await browser.close();
