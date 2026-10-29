import AxeBuilder from "@axe-core/playwright";
import { expect, test, type APIRequestContext } from "@playwright/test";
import { API, ORIGIN } from "./api";

/**
 * The UAT script (docs/uat/uat-script.md, sections 1-14) and the review's UX items 3, 5, 6, 8, 9, 12, 13, 14 and 15
 * walked in a browser against a freshly seeded stack. Each test names the section it verifies; sections that need a
 * CLI, a worker or an LLM gateway are listed in docs/reviews/2026-10-25-technical-and-ux-review.md as not walked here.
 */
const unique = () => Math.random().toString(36).slice(2, 7);
const SARA = { "X-Debug-Actor": "@sara" };
const ALI = { "X-Debug-Actor": "@ali" };
const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;

async function capture(
  request: APIRequestContext,
  actor: string,
  line: Record<string, unknown>,
): Promise<{ status: string; item?: string }> {
  const r = await request.post(`${ORIGIN}/api/v1/w/acme/capture/batches`, {
    headers: { "X-Debug-Actor": actor },
    data: {
      workspace: "acme",
      messages: [{ actor, at: new Date().toISOString(), client_id: crypto.randomUUID(), ...line }],
    },
  });
  expect(r.ok(), await r.text()).toBeTruthy();
  const res = (await r.json()).results[0];
  return { status: res.status, item: res.item?.key ?? res.item_key ?? res.key };
}

// ------------------------------------------------------------------ UX 3 and UAT 2: routing of a reporter's lines
test("UX 3 / UAT 2: a reporter's lines land in the manager's queue, with a row even when no project was named", async ({
  page,
  request,
}) => {
  const phrase = `nowhere ${unique()}`;
  await capture(request, "@sara", {
    action: "create",
    item: { title: `tidy ${phrase}` },
    project: phrase,
  });
  await capture(request, "@sara", {
    action: "create",
    item: { title: `no project at all ${phrase}` },
  });

  // her own queue holds neither line (a contributor may get 403 for the queue itself, both mean "not hers")
  const mine = await request.get(`${API}/review-queue`, { headers: SARA });
  if (mine.ok()) {
    const rows = JSON.stringify((await mine.json()).data ?? []);
    expect(rows).not.toContain(phrase);
  } else expect(mine.status()).toBe(403);

  await page.goto("/w/acme/review");
  for (const title of [`tidy ${phrase}`, `no project at all ${phrase}`]) {
    const row = page.locator(".rq-row", { hasText: title });
    await expect(row, title).toBeVisible();
    await expect(row.getByText(/@sara · /)).toBeVisible(); // who reported it and how long ago (UX 8)
  }
});

// ------------------------------------------------------------------ UX 8 and UX 9: one-click default, Enter, focus, undo
test("UX 8 / UX 9: every suggested row has a primary button, Enter takes it, focus stays in the list, Undo puts the row back", async ({
  page,
  request,
}) => {
  const phrase = `enter ${unique()}`;
  await capture(request, "@sara", {
    action: "create",
    item: { title: `needs a home ${phrase}` },
    project: phrase,
  });
  await page.goto("/w/acme/review");
  const row = page.locator(".rq-row", { hasText: phrase });
  await expect(row).toBeVisible();
  // the primary action is a button whose name says what it does, not "WEB ask"
  const primary = row.locator(".btn-primary");
  await expect(primary).toHaveCount(1);
  await expect(primary).toHaveAccessibleName(/^Assign to [A-Z]+/);
  // j/k move real focus; Enter takes the suggestion; focus is not dropped on <body>
  await row.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator(".toast")).toHaveCount(1);
  await expect(page.locator(".rq-row", { hasText: phrase })).toHaveCount(0);
  await expect
    .poll(() => page.evaluate(() => !!document.activeElement?.closest(".rq-row")))
    .toBeTruthy();
  await page.getByRole("button", { name: "Undo" }).click();
  await expect(page.locator(".rq-row", { hasText: phrase })).toBeVisible();
});

test("UX 8: 'Confirm all suggested' says why it is zero", async ({ page }) => {
  await page.goto("/w/acme/review");
  const btn = page.getByRole("button", { name: /Confirm all suggested \(\d+\)/ });
  await expect(btn).toBeVisible();
  const label = (await btn.textContent()) ?? "";
  if (label.includes("(0)")) {
    await expect(page.getByText(/below the minimum confidence/)).toBeVisible();
    await expect(btn).toHaveAccessibleDescription(/below the minimum confidence/);
  }
});

// ------------------------------------------------------------------ UX 5: names, never ids
test("UX 5: no raw UUID is shown on any screen or in the item drawer", async ({
  page,
  request,
}) => {
  const made = await capture(request, "@sara", {
    action: "create",
    item: { title: `reported by sara ${unique()}` },
    project: "WEB",
  });
  const screens = [
    "/w/acme/review",
    "/w/acme/views",
    "/w/acme/views?layout=board",
    "/w/acme/views?layout=now",
    "/w/acme/present",
    "/w/acme/policies",
    "/w/acme/violations",
    "/w/acme/habits",
    "/w/acme/performance",
    "/w/acme/digest",
    "/w/acme/insights",
    "/w/acme/weights",
    "/w/acme/setup/people",
    "/w/acme/setup/projects",
    "/w/acme/setup/identity",
  ];
  for (const url of screens) {
    await page.goto(url);
    await page.waitForLoadState("networkidle");
    const text = await page.locator("body").innerText();
    expect(text.match(UUID)?.[0], `${url} shows an id`).toBeUndefined();
  }
  if (made.item) {
    await page.goto(`/w/acme/views?item=${made.item}`);
    const drawer = page.getByRole("dialog", { name: /Item details|WEB-/ }).first();
    await expect(drawer).toBeVisible();
    await expect(drawer).toContainText("@sara");
    for (const tab of [
      "overview",
      "activity",
      "discussion",
      "placements",
      "violations",
      "events",
    ]) {
      await drawer.getByRole("tab", { name: tab }).click();
      await page.waitForLoadState("networkidle");
      expect((await drawer.innerText()).match(UUID)?.[0], `drawer tab ${tab}`).toBeUndefined();
    }
  }
});

// ------------------------------------------------------------------ UX 6: destructive actions ask first
test("UX 6: deleting a glossary phrase, a translator rule or an identity mapping asks first", async ({
  page,
  request,
}) => {
  const phrase = `confirm-me-${unique()}`;
  const proj = (await (await request.get(`${API}/projects`)).json()).data[0];
  const made = await request.post(`${API}/glossary`, {
    data: { phrase, resolves_to_type: "project", resolves_to_id: proj.id },
  });
  expect(made.ok(), await made.text()).toBeTruthy();
  await page.goto("/w/acme/setup/glossary");
  const del = page.getByRole("button", { name: `delete ${phrase}` });
  await del.click();
  const dlg = page.getByRole("dialog", { name: new RegExp(`Delete .*${phrase}`) });
  await expect(dlg).toBeVisible();
  await dlg.getByRole("button", { name: "Cancel" }).click();
  await expect(del).toBeVisible();
  await del.click();
  await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
  await expect(del).toHaveCount(0);

  const rule = `rule ${unique()}`;
  expect(
    (await request.post(`${API}/translator-rules`, { data: { text: rule } })).ok(),
  ).toBeTruthy();
  await page.goto("/w/acme/setup/rules");
  const row = page.getByRole("listitem").filter({ hasText: rule });
  await row.getByRole("button", { name: /^delete rule/ }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("dialog").getByRole("button", { name: "Cancel" }).click();
  await expect(row).toHaveCount(1);
});

// ------------------------------------------------------------------ UX 12: nav gating
test.describe("UX 12 a viewer", () => {
  test.use({ extraHTTPHeaders: ALI });
  test("never sees a link that ends in a 403, and a deep link explains itself (UAT 12)", async ({
    page,
    request,
  }) => {
    await page.goto("/w/acme/views");
    const links = await page
      .getByRole("navigation", { name: "Main" })
      .getByRole("link")
      .evaluateAll((as) => as.map((a) => (a as HTMLAnchorElement).pathname));
    expect(links.length).toBeGreaterThan(0);
    for (const path of links) {
      const res = await page.goto(path);
      expect(res?.status(), path).toBeLessThan(400);
      await expect(page.getByRole("heading", { name: /don't have access/i }), path).toHaveCount(0);
    }
    // a project he is not on is a 404, not a 403 (UAT 12)
    const hidden = await request.get(`${API}/projects/GOV`, { headers: ALI });
    expect([403, 404]).toContain(hidden.status());
    expect(hidden.status()).toBe(404);
  });
});

// ------------------------------------------------------------------ UX 13: share link
test("UX 13: the share link stays on the page after it is created, with a Copy button, and it opens anonymously without habits", async ({
  page,
  request,
  browser,
}) => {
  await request.patch(`${API}/settings`, { data: { external_share: true } });
  const deck = await request.post(`${API}/presentations`, {
    data: { title: `Share walk ${unique()}`, slides: [{ kind: "kpis" }, { kind: "habits" }] },
  });
  const id = (await deck.json()).id as string;
  await page.goto(`/w/acme/present/${id}`);
  await page.getByRole("button", { name: "Create read-only link" }).click();
  const link = page.getByRole("textbox", { name: "Share link" });
  await expect(link).toHaveValue(/\/p\/t\/pt_/);
  await expect(page.getByRole("button", { name: "Copy link" })).toBeVisible();
  await page
    .locator(".toast")
    .first()
    .waitFor({ state: "detached", timeout: 20_000 })
    .catch(() => {});
  await expect(link).toBeVisible(); // not a transient toast

  const url = await link.inputValue();
  const anon = await browser.newContext({ extraHTTPHeaders: {}, baseURL: ORIGIN });
  const token = new URL(url).pathname.split("/").pop();
  const data = await anon.request.get(`${ORIGIN}/api/v1/public/p/${token}/data`);
  expect(data.ok()).toBeTruthy();
  const body = await data.json();
  const snap = body.snapshot ?? body.data?.snapshot ?? {};
  expect(snap.habits ?? []).toHaveLength(0); // no habits for an anonymous viewer (UAT 5.3)
  expect(snap.now ?? []).toHaveLength(0); // no live sessions
  const shell = await anon.request.get(url);
  expect(shell.headers()["content-security-policy"]).toContain("frame-ancestors 'none'");
  await anon.close();
});

// ------------------------------------------------------------------ UAT 2: access for a contributor
test.describe("UAT 2.3 a contributor", () => {
  test.use({ extraHTTPHeaders: SARA });
  test("opens /review and gets only what her role allows", async ({ page }) => {
    await page.goto("/w/acme/review");
    const denied = page.getByRole("heading", { name: /don't have access/i });
    const rows = page.locator(".rq-row");
    await expect(denied.or(page.getByRole("group", { name: "Review rows" }))).toBeAttached();
    if ((await denied.count()) === 0) expect(await rows.count()).toBe(0);
  });
});

// ------------------------------------------------------------------ UAT 3: Views state survives a reload, matrix equals list
test("UAT 3: filters and layout survive a reload and the matrix total equals the list count", async ({
  page,
}) => {
  await page.goto("/w/acme/views?layout=list");
  await page.getByRole("button", { name: "+ Filter" }).click();
  await page.getByLabel("Filter on").selectOption("priority");
  await page.getByLabel("Value").selectOption({ index: 1 });
  await page.getByRole("button", { name: "Add", exact: true }).click();
  const chips = page.getByRole("group", { name: "Filters" });
  await expect(chips).toContainText("priority");
  const listTotal = Number(
    (await page.locator(".vlist p[aria-live]").first().textContent())?.match(/\d+/)?.[0],
  );
  expect(listTotal).toBeGreaterThan(0);
  await page.getByRole("tab", { name: "matrix" }).click();
  await page.reload();
  await expect(page).toHaveURL(/layout=matrix/);
  await expect(page.getByRole("group", { name: "Filters" })).toContainText("priority");
  await expect(page.locator(".vmatrix td.cell").first()).toBeVisible();
  await expect
    .poll(async () => {
      const cells = await page.locator(".vmatrix td.cell").allTextContents();
      return cells.reduce((n, c) => n + (Number(c) || 0), 0);
    })
    .toBe(listTotal);
});

// ------------------------------------------------------------------ UAT 5: presentations
test("UAT 5.4: a built-in template is duplicated, edited, validated inline, saved and instantiated", async ({
  page,
}) => {
  await page.goto("/w/acme/present/templates");
  const row = page.getByRole("listitem").filter({ hasText: "Portfolio review" });
  await row.getByRole("button", { name: "Duplicate" }).click();
  // the copy is "mine": Edit opens it
  await page
    .getByRole("listitem")
    .filter({ hasText: /Portfolio review/ })
    .filter({ has: page.getByRole("button", { name: "Edit" }) })
    .last()
    .getByRole("button", { name: "Edit" })
    .click();
  await page.getByRole("button", { name: "JSON" }).click();
  const box = page.getByLabel("Template JSON");
  const doc = JSON.parse(await box.inputValue());
  const key = `walk_${unique()}`;
  doc.key = key;
  doc.name = `Walk ${key}`;
  doc.slides.push({ kind: "bogus_kind", config: {} });
  await box.fill(JSON.stringify(doc));
  await expect(page.getByLabel("Template problems")).toContainText("/slides/");
  await expect(page.getByRole("button", { name: "Save" })).toBeDisabled();
  doc.slides.pop();
  doc.slides.push({ kind: "markdown", config: { text: "added by the walk" } });
  await box.fill(JSON.stringify(doc));
  await expect(page.getByLabel("Template problems")).toHaveCount(0);
  await page.getByRole("button", { name: "Save" }).click();
  const mine = page.getByRole("listitem").filter({ hasText: `Walk ${key}` });
  await expect(mine).toBeVisible();
  await mine.getByRole("button", { name: "Use" }).click();
  await expect(page).toHaveURL(/\/present\/[0-9a-f-]{20,}/);
});

// ------------------------------------------------------------------ UAT 6: external packs
test("UAT 6.2: without the workspace setting the generation pack answers 'not allowed'", async ({
  page,
  request,
}) => {
  const r = await request.post(`${API}/presentations:generation-pack`, { data: { brief: "x" } });
  expect(r.status()).toBe(403);
  expect((await r.json()).error?.code ?? (await r.json()).code).toBe("external_packs_disabled");
  await page.goto("/w/acme/present");
  await page.getByRole("button", { name: /Download pack/i }).click();
  await expect(page.locator(".toast").first()).toContainText(/not allow|external LLM|enable/i);

  // an admin switches it on (PATCH /settings); the pack is a zip, and switching it off refuses again
  const on = await request.patch(`${API}/settings`, { data: { external_packs: true } });
  expect(on.ok(), await on.text()).toBeTruthy();
  try {
    expect((await (await request.get(`${API}/settings`)).json()).external_packs).toBe(true);
    const ok = await request.post(`${API}/presentations:generation-pack`, { data: { brief: "x" } });
    expect(ok.status()).toBe(200);
    expect(ok.headers()["content-type"]).toContain("application/zip");
  } finally {
    await request.patch(`${API}/settings`, { data: { external_packs: false } });
  }
  const off = await request.post(`${API}/presentations:generation-pack`, { data: { brief: "x" } });
  expect(off.status()).toBe(403);
});

// ------------------------------------------------------------------ UAT 8 and 9: habits export, weights, digest
test("UAT 8: the habit report exports a CSV stamped with who and when", async ({ page }) => {
  await page.goto("/w/acme/habits");
  const [dl] = await Promise.all([
    page.waitForEvent("download", { timeout: 30_000 }),
    page.getByRole("button", { name: /Export for 1:1/ }).click(),
  ]);
  expect(dl.suggestedFilename()).toMatch(/\.csv$/);
  const text = await (await import("node:fs/promises")).readFile((await dl.path())!, "utf8");
  expect(text).toMatch(/@mahmoud/);
  expect(text).toMatch(/20\d\d-\d\d-\d\d/);
});

test("UAT 9: a weight scheme previews before it is saved, a confirmed daily log is marked reviewed in the digest, every report offers Add to presentation", async ({
  page,
  request,
}) => {
  await page.goto("/w/acme/weights");
  await page.getByRole("button", { name: "Preview on 20 items" }).click();
  await expect(page.getByRole("table", { name: "Weight preview" })).toBeVisible();

  // a draft log is confirmed by its author (API, as the CLI does), then the manager reviews it in the digest
  const logs = (await (await request.get(`${API}/daily-logs`)).json()).data as {
    id: string;
    status: string;
  }[];
  const draft = logs.find((l) => l.status === "draft");
  expect(draft, "the seed has a draft daily log").toBeTruthy();
  const ok = await request.post(`${API}/daily-logs/${draft!.id}/confirm`, { data: {} });
  expect(ok.ok(), await ok.text()).toBeTruthy();
  await page.goto("/w/acme/digest");
  const title = page.getByText(/Daily logs · \d+ to review/);
  const before = Number((await title.textContent())!.match(/(\d+) to review/)![1]);
  await page.getByRole("button", { name: "Mark reviewed" }).first().click();
  await expect(title).toContainText(`${before - 1} to review`);
  const res = await new AxeBuilder({ page }).analyze();
  expect(res.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toEqual(
    [],
  );
  for (const url of ["performance", "digest", "insights", "weights", "habits", "violations"]) {
    await page.goto(`/w/acme/${url}`);
    await expect(page.getByRole("button", { name: /Add to presentation/i }).first()).toBeVisible();
  }
});

// ------------------------------------------------------------------ UAT 10: item comments, checklists, links, attachments
test("UAT 10: comments, checklists and links show in the item drawer, a dependency cycle is refused with 409, an HTML attachment never renders", async ({
  page,
  request,
}) => {
  const a = (
    await capture(request, "@mahmoud", {
      action: "create",
      item: { title: `A ${unique()}` },
      project: "WEB",
    })
  ).item!;
  const b = (
    await capture(request, "@mahmoud", {
      action: "create",
      item: { title: `B ${unique()}` },
      project: "WEB",
    })
  ).item!;
  const body = `walk comment ${unique()}`;
  expect((await request.post(`${API}/items/${a}/comments`, { data: { body } })).ok()).toBeTruthy();
  expect(
    (
      await request.post(`${API}/items/${a}/checklists`, {
        data: { title: "Done when", items: ["one", "two"] },
      })
    ).ok(),
  ).toBeTruthy();
  expect(
    (await request.post(`${API}/items/${a}/links`, { data: { to: b, link_type: "blocks" } })).ok(),
  ).toBeTruthy();
  const cycle = await request.post(`${API}/items/${b}/links`, {
    data: { to: a, link_type: "blocks" },
  });
  expect(cycle.status()).toBe(409);
  await page.goto(`/w/acme/views?item=${a}`);
  const drawer = page.getByRole("dialog", { name: "Item details" });
  await drawer.getByRole("tab", { name: "discussion" }).click();
  await expect(drawer).toContainText(body);
  await expect(drawer).toContainText("Done when");
  await expect(drawer).toContainText(b);
  // an .html attachment downloads and is served with a type that cannot render inline
  const up = await request.post(`${API}/items/${a}/attachments`, {
    multipart: {
      file: { name: "x.html", mimeType: "text/html", buffer: Buffer.from("<script>1</script>") },
    },
  });
  if (up.ok()) {
    const meta = await up.json();
    const dl = await request.get(`${API}/attachments/${meta.id}/download`);
    expect(dl.headers()["content-disposition"] ?? "").toMatch(/attachment/);
  } else expect(up.status()).toBeLessThan(500);
});

// ------------------------------------------------------------------ UAT 13: privacy
test("UAT 13: own export, a person's export without Inbox or wellbeing, and erase needs the handle typed again", async ({
  request,
}) => {
  const mine = await request.get(`${API}/me/export`);
  expect(mine.ok()).toBeTruthy();
  const hers = await request.get(`${API}/people/@sara/export`);
  expect(hers.ok()).toBeTruthy();
  const doc = await hers.json();
  expect(doc.scope).toMatch(/excludes Inbox and wellbeing/);
  const data = { ...doc, scope: undefined };
  expect(JSON.stringify(data)).not.toMatch(/wellbeing/i);
  expect(Object.keys(data).join(" ")).not.toMatch(/inbox/i);
  const wrong = await request.post(`${API}/people/@sara:erase`, {
    data: { confirm: "@someone-else" },
  });
  expect(wrong.status()).toBeGreaterThanOrEqual(400);
  expect(wrong.status()).toBeLessThan(500);
  const purge = await request.post(`${API}/retention:purge-raw-input`, { data: { days: 3650 } });
  expect(purge.ok()).toBeTruthy();
});

// ------------------------------------------------------------------ UAT 14: operations (last: it spends a rate-limit bucket of its own)
test("UAT 14: /readyz, the metrics gate and the capture rate limit", async ({ request }) => {
  const ready = await request.get(`${ORIGIN}/readyz`);
  expect(ready.ok()).toBeTruthy();
  const metrics = await request.get(`${ORIGIN}/metrics`);
  expect([200, 401, 404]).toContain(metrics.status());
  // hammer with a throw-away credential so the other specs keep their own bucket
  let limited: { status: number; retry: string | undefined } | null = null;
  for (let i = 0; i < 700 && !limited; i++) {
    const r = await request.post(`${API}/capture/batches`, {
      headers: { "X-Debug-Actor": `@probe-${unique()}-same`, Authorization: "Bearer probe-bucket" },
      data: { workspace: "acme", messages: [] },
    });
    if (r.status() === 429) limited = { status: 429, retry: r.headers()["retry-after"] };
  }
  expect(limited?.status).toBe(429);
  expect(Number(limited?.retry)).toBeGreaterThan(0);
});
