// Captures the README screens from a server running with the demo database.
//   node docs/screenshots/capture.mjs http://127.0.0.1:8099 /tmp/discover.json docs
// See docs/screenshots/README.md.
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const { chromium } = createRequire(import.meta.url)("playwright");
const [base, discoverJson, outDir] = process.argv.slice(2);

const SHOTS = [
  { file: "dashboard.png", hash: "#/dashboard", wait: "svg" },
  { file: "applications.png", hash: "#/apps", wait: "tr.clickable" },
  { file: "application.png", hash: "#/apps/1", wait: "#notes .card, #notes *" },
  { file: "compare.png", hash: "#/compare", wait: "table" },
  { file: "new-versions.png", hash: "#/releases", wait: ".ver" },
  { file: "report.png", hash: "#/reports", wait: "h3" },
  {
    file: "discover.png", hash: "#/discover", wait: "#dc-form", height: 1200,
    async after(page) {
      await page.selectOption("#dc-env", { index: 2 }); // production
      await page.fill("#dc-ns", "kube-monitoring");
      await page.click("#dc-form button[type=submit]");
      await page.waitForSelector("#dc-out .card h2");
    },
  },
];

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 1000 }, deviceScaleFactor: 2, colorScheme: "light" });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
await page.route("**/api/environments/*/discover*", (r) => r.fulfill({ contentType: "application/json", body: readFileSync(discoverJson, "utf8") }));
for (const s of SHOTS) {
  await page.setViewportSize({ width: 1400, height: s.height || 1000 });
  await page.goto(`${base}/${s.hash}`);
  await page.waitForSelector(s.wait);
  if (s.after) await s.after(page);
  await page.waitForTimeout(600); // chart animations
  await page.screenshot({ path: `${outDir}/${s.file}` });
  console.log("ok", s.file);
}
await browser.close();
if (errors.length) { console.error(errors.join("\n")); process.exit(1); }
