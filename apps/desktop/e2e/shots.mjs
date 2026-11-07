// Not a test: `TT_CHROMIUM=... node e2e/shots.mjs <outdir>` writes screenshots of the demo views (used for docs/uat).
import { chromium } from "@playwright/test";

const out = process.argv[2] ?? "shots";
const browser = await chromium.launch({ executablePath: process.env.TT_CHROMIUM || undefined });
for (const [name, viewport] of [
  ["desktop", { width: 1280, height: 900 }],
  ["mobile", { width: 390, height: 844 }],
]) {
  const page = await browser.newPage({ viewport });
  await page.goto("http://localhost:1420/demo.html");
  await page.getByRole("region", { name: /Ready to send/ }).waitFor();
  await page.screenshot({ path: `${out}/${name}-today.png`, fullPage: true });
  await page.keyboard.press("w");
  await page.getByRole("heading", { name: /Week of/ }).waitFor();
  await page.screenshot({ path: `${out}/${name}-week.png`, fullPage: true });
  await page.keyboard.press("m");
  await page.getByRole("heading", { name: "October 2026" }).waitFor();
  await page.screenshot({ path: `${out}/${name}-month.png`, fullPage: true });
  await page.keyboard.press("d");
  await page.getByRole("button", { name: "End of day" }).click();
  await page.getByRole("dialog").waitFor();
  await page.screenshot({ path: `${out}/${name}-eod.png` });
  await page.close();
}
await browser.close();
