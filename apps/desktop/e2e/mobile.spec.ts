import { expect, test, type Page } from "@playwright/test";

/** Mobile Lite (ADR-0057) in a real browser, on the production build: the manifest is valid, the service worker installs and controls the
 * page, the app OPENS OFFLINE after one visit, an item added offline is still there after a reload, and a 390 px phone never scrolls
 * sideways. Run with `pnpm --filter @tt/desktop e2e:mobile` (it builds and serves `dist/`). */
const APP = "/mobile.html";

async function firstRun(page: Page) {
  // the privacy explainer shows once on a fresh browser profile
  const got = page.getByRole("button", { name: "Got it" });
  await got.waitFor();
  await got.click();
}

test.use({ viewport: { width: 390, height: 844 } });

test("the manifest is linked and valid", async ({ page }) => {
  await page.goto(APP);
  const href = await page.locator('link[rel="manifest"]').getAttribute("href");
  expect(href).toBe("/manifest.webmanifest");
  const m = await (await page.request.get(href!)).json();
  expect(m).toMatchObject({ display: "standalone", start_url: "./mobile.html", scope: "./" });
  const sizes = m.icons.map((i: { sizes: string }) => i.sizes);
  expect(sizes).toEqual(expect.arrayContaining(["192x192", "512x512"]));
  for (const icon of m.icons) {
    const r = await page.request.get(`/${icon.src}`);
    expect(r.status(), icon.src).toBe(200);
    expect(r.headers()["content-type"]).toContain("image/png");
  }
});

test("installs a service worker and opens offline; what was added offline survives a reload", async ({
  page,
  context,
}) => {
  await page.goto(APP);
  await firstRun(page);
  await expect(page.getByRole("heading", { name: "My time" })).toBeVisible();

  // the worker takes control (it installs on the first visit and claims the page)
  await page.evaluate(() => navigator.serviceWorker.ready.then(() => undefined));
  await page.reload();
  await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true);
  const cached = await page.evaluate(async () => {
    const names = await caches.keys();
    const keys = names.length ? await (await caches.open(names[0])).keys() : [];
    return keys.map((k) => new URL(k.url).pathname);
  });
  expect(cached).toEqual(expect.arrayContaining(["/mobile.html", "/manifest.webmanifest"]));
  expect(cached.some((p) => p.startsWith("/assets/") && p.endsWith(".js"))).toBe(true);

  await context.setOffline(true);
  await page.reload();
  await expect(page.getByRole("heading", { name: "My time" })).toBeVisible();
  await expect(page.getByText(/Offline/)).toBeVisible();

  await page.getByLabel("Add an item, one line").fill("work: write the report 45m p:web");
  await page.getByLabel("Add an item, one line").press("Enter");
  await expect(page.getByText("write the report").first()).toBeVisible();

  await page.reload(); // still offline: the plan comes from localStorage
  await expect(page.getByText("write the report").first()).toBeVisible();

  // no horizontal scroll on a 390 px phone
  const wide = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(wide).toBe(false);
});

test("a send made offline waits, and is delivered when the connection returns", async ({ page, context }) => {
  const posted: { url: string; auth: string | undefined; body: string }[] = [];
  await page.route("**/api/v1/w/acme/capture/batches", async (route) => {
    const req = route.request();
    posted.push({ url: req.url(), auth: req.headers()["authorization"], body: req.postData() ?? "" });
    await route.fulfill({ status: 207, contentType: "application/json", body: "{}" });
  });
  await page.goto(APP);
  await firstRun(page);
  await page.getByRole("button", { name: "Phone settings" }).click();
  const dlg = page.getByRole("dialog", { name: "Phone settings" });
  await dlg.getByLabel("Your handle").fill("@mahmoud");
  await dlg.getByLabel("Workspace").fill("acme");
  await dlg.getByLabel("API token").fill("tok-e2e");
  await dlg.getByLabel("Your projects").fill("WEB, DATA");
  await dlg.getByRole("button", { name: "Save" }).click();

  await context.setOffline(true);
  const line = page.getByLabel("Add an item, one line");
  await line.fill("work: fix login 30m p:web");
  await line.press("Enter");
  await page.getByRole("button", { name: /^Done fix login/ }).click();
  await page
    .getByRole("region", { name: /Ready to send/ })
    .getByRole("button", { name: /^Send fix login/ })
    .click();
  await expect(page.getByText(/1 waiting to send/)).toBeVisible();
  expect(posted).toHaveLength(0);

  await context.setOffline(false);
  await expect(page.getByText(/Nothing waiting/)).toBeVisible({ timeout: 15000 });
  expect(posted).toHaveLength(1);
  expect(posted[0].auth).toBe("Bearer tok-e2e");
  const msg = JSON.parse(posted[0].body).messages[0];
  expect(msg).toMatchObject({ actor: "@mahmoud", action: "log_time", project: "WEB", minutes: 30 });
});
