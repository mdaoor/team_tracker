import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { API } from "./api";

/** Phase 13 R6.3 (UX §3.3): the setup editors behave as designed, not as flat CRUD tables. */
const unique = () => Math.random().toString(36).slice(2, 7);

test("a stage chip opens an editor with a live mini-pipeline and saves", async ({ page }) => {
  await page.goto("/w/acme/setup/process");
  const chip = page.locator(".stage-chip").first();
  const name = (await chip.evaluate((el) => el.childNodes[0].textContent ?? "")).trim();
  await chip.click();
  const dialog = page.getByRole("dialog", { name: /Edit stage/ });
  await expect(dialog).toBeVisible();
  for (const label of ["Expected days", "WIP limit", "Needs approval", "Category"])
    await expect(dialog.getByLabel(label)).toBeVisible();
  const mini = dialog.getByRole("img", { name: "Mini pipeline preview" });
  const before = await mini.locator("span.on").evaluate((el) => getComputedStyle(el).flexGrow);
  await dialog.getByLabel("Expected days").fill("40");
  await expect(mini.locator("span.on")).toHaveAttribute("title", `${name}: 40 d`);
  const after = await mini.locator("span.on").evaluate((el) => getComputedStyle(el).flexGrow);
  expect(Number(after)).toBeGreaterThan(Number(before));
  await dialog.getByLabel("WIP limit").fill("7");
  await dialog.getByLabel("Needs approval").check();
  await dialog.getByRole("button", { name: "Save stage" }).click();
  await expect(page.getByText("Stage updated")).toBeVisible();
  await expect(page.locator(".stage-chip").first()).toContainText("40 d");
  await expect(page.locator(".stage-chip").first()).toContainText("WIP 7");
  await expect(page.locator(".stage-chip").first()).toContainText("approval");
});

test("the stage editor is a popover anchored to its chip: focus, Escape, outside click, axe", async ({
  page,
}) => {
  await page.goto("/w/acme/setup/process");
  await page.waitForLoadState("networkidle");
  const chips = page.locator(".stage-chip");
  const last = chips.nth((await chips.count()) - 1);
  await last.click();
  const pop = page.getByRole("dialog", { name: /Edit stage/ });
  await expect(pop).toBeVisible();
  // anchored: inside the chip's own list item, directly below the chip, and never wider than the viewport
  await expect(page.locator("li[data-stage]").last().getByRole("dialog")).toHaveCount(1);
  const c = (await last.boundingBox())!;
  const p = (await pop.boundingBox())!;
  const vw = page.viewportSize()!.width;
  expect(p.y).toBeGreaterThanOrEqual(c.y + c.height - 1);
  expect(p.y).toBeLessThan(c.y + c.height + 40);
  expect(p.x).toBeGreaterThanOrEqual(0);
  expect(p.x + p.width).toBeLessThanOrEqual(vw + 1);
  // keyboard: focus lands in the first field, Escape closes and gives focus back to the chip
  await expect(pop.getByLabel("Name")).toBeFocused();
  const res = await new AxeBuilder({ page }).include(".stages").analyze();
  expect(res.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toEqual(
    [],
  );
  await page.keyboard.press("Escape");
  await expect(pop).toHaveCount(0);
  await expect(last).toBeFocused();
  // a click elsewhere closes it too, and the chip toggles it
  await last.click();
  await expect(pop).toBeVisible();
  await page.getByRole("heading", { level: 1 }).click();
  await expect(pop).toHaveCount(0);
  await last.click();
  await last.click();
  await expect(pop).toHaveCount(0);
});

test("at 390x844 the stage popover stays on screen and the page does not scroll sideways", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/w/acme/setup/process");
  await page.waitForLoadState("networkidle");
  const chips = page.locator(".stage-chip");
  for (const i of [0, (await chips.count()) - 1]) {
    await chips.nth(i).click();
    const box = (await page.getByRole("dialog", { name: /Edit stage/ }).boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(391);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
      390,
    );
    await page.keyboard.press("Escape");
  }
});

test("the Projects tree is product > project > epic; products reparent by drag and by Alt+Arrow", async ({
  page,
  request,
}) => {
  const tag = unique().toUpperCase();
  const names = { a: `ZA ${tag}`, b: `ZB ${tag}` };
  for (const [k, n] of [
    [`ZA${tag}`, names.a],
    [`ZB${tag}`, names.b],
  ])
    expect(
      (await request.post(`${API}/products`, { data: { key: k, name: n } })).ok(),
    ).toBeTruthy();
  await page.goto("/w/acme/setup/projects");
  const tree = page.getByRole("tree", { name: "Products, projects and epics" });
  await expect(tree).toBeVisible();

  // the epic level: Customer Portal > WEB > WEB-120 (seed data)
  const web = tree
    .locator('li[aria-level="1"]', { hasText: "Customer Portal" })
    .locator('li[aria-level="2"]')
    .filter({ has: page.locator("strong", { hasText: /^WEB$/ }) });
  await expect(web).toHaveCount(1);
  await expect(web.locator('li[aria-level="3"]').filter({ hasText: "WEB-120" })).toHaveCount(1);

  // drag product B onto product A: B becomes a child of A
  const btn = (n: string) => tree.getByRole("button", { name: new RegExp(`^Product ${n}\\.`) });
  const level = (n: string) =>
    btn(n).locator("xpath=ancestor::li[@role='treeitem'][1]").getAttribute("aria-level");
  expect(await level(names.b)).toBe("1");
  await btn(names.b).dragTo(btn(names.a));
  await expect(page.getByText(`Moved ZB${tag}`)).toBeVisible();
  await expect.poll(() => level(names.b)).toBe("2");

  // Alt+Left moves it back out; Alt+Right nests it under the previous sibling again
  await btn(names.b).focus();
  await page.keyboard.press("Alt+ArrowLeft");
  await expect.poll(() => level(names.b)).toBe("1");
  await btn(names.b).focus();
  await page.keyboard.press("Alt+ArrowRight");
  await expect.poll(() => level(names.b)).toBe("2");
  // already top-level: the keys say so instead of failing silently
  await btn(names.a).focus();
  await page.keyboard.press("Alt+ArrowLeft");
  await expect(page.getByText("Already top-level")).toBeVisible();
});

test("projects move between products and epics inside their project, by drag and by Alt+Arrow", async ({
  page,
  request,
}) => {
  const tag = unique().toUpperCase();
  const pk = `Q${tag}`;
  const prod = { a: `ZA${tag}`, b: `ZB${tag}` };
  for (const k of [prod.a, prod.b])
    expect(
      (await request.post(`${API}/products`, { data: { key: k, name: `P ${k}` } })).ok(),
    ).toBeTruthy();
  expect(
    (await request.post(`${API}/projects`, { data: { key: pk, name: `Proj ${tag}` } })).ok(),
  ).toBeTruthy();
  const linked = await request.post(`${API}/products/${prod.a}/links`, {
    data: { target_type: "project", target_id: pk, role: "delivers", weight: 2 },
  });
  expect(linked.ok(), await linked.text()).toBeTruthy();
  const epic = async (title: string): Promise<string> => {
    const r = await request.post(`${API}/capture/batches`, {
      data: {
        workspace: "acme",
        messages: [
          {
            actor: "@mahmoud",
            action: "create",
            at: new Date().toISOString(),
            client_id: crypto.randomUUID(),
            item: { title },
            project: pk,
            item_type: "epic",
          },
        ],
      },
    });
    const res = (await r.json()).results[0];
    expect(res.item?.key, JSON.stringify(res)).toBeTruthy();
    return res.item.key as string;
  };
  const e1 = await epic(`First epic ${tag}`);
  const e2 = await epic(`Second epic ${tag}`);
  const nest = await request.patch(`${API}/settings`, { data: { allow_same_level_nesting: true } });
  expect(nest.ok()).toBeTruthy();
  try {
    await page.setViewportSize({ width: 1280, height: 2400 }); // a tall tree: every drop target stays on screen
    await page.goto("/w/acme/setup/projects");
    const tree = page.getByRole("tree", { name: "Products, projects and epics" });
    const btn = (re: RegExp) => tree.getByRole("button", { name: re });
    const level = (b: ReturnType<typeof btn>) =>
      b.locator("xpath=ancestor::li[@role='treeitem'][1]").getAttribute("aria-level");
    /** The product button of the nearest product ancestor of a project or epic. */
    const owner = (b: ReturnType<typeof btn>) =>
      b
        .locator(
          "xpath=ancestor::li[@role='treeitem'][.//button[starts-with(@aria-label,'Product ')]]",
        )
        .last()
        .locator("button[aria-label^='Product ']")
        .first()
        .getAttribute("aria-label");
    const product = (k: string) => btn(new RegExp(`^Product P ${k}\\.`));
    const project = btn(new RegExp(`^Project ${pk} `));
    /** The project shows once, under the named product (the two link lists refetch separately, so wait out the overlap). */
    const settled = async (name: string) => {
      await expect(project).toHaveCount(1);
      await expect.poll(() => owner(project)).toContain(name);
      await expect(project).toHaveCount(1);
    };
    await expect(project).toBeVisible();
    expect(await owner(project)).toContain(`P ${prod.a}`);

    // drag the project onto the other product: unlink + link, role and weight carried over
    await project.dragTo(product(prod.b));
    await expect(page.getByText(`Moved ${pk} to ${prod.b}`)).toBeVisible();
    await settled(`P ${prod.b}`);
    const bLinks = (await (await request.get(`${API}/products/${prod.b}/links`)).json()).data;
    expect(bLinks).toHaveLength(1);
    expect([bLinks[0].role, Number(bLinks[0].weight)]).toEqual(["delivers", 2]);
    expect((await (await request.get(`${API}/products/${prod.a}/links`)).json()).data).toHaveLength(
      0,
    );

    // nest B under A, then Alt+Left / Alt+Right move the project out to A and back in under B
    await product(prod.b).dragTo(product(prod.a));
    await expect.poll(() => level(product(prod.b))).toBe("2");
    await expect.poll(() => level(project)).toBe("3");
    await project.focus();
    await page.keyboard.press("Alt+ArrowLeft");
    await settled(`P ${prod.a}`);
    await expect.poll(() => level(project)).toBe("2");
    await project.focus();
    await page.keyboard.press("Alt+ArrowRight");
    await settled(`P ${prod.b}`);
    await project.focus();
    await page.keyboard.press("Alt+ArrowLeft");
    await settled(`P ${prod.a}`);
    await project.focus();
    await page.keyboard.press("Alt+ArrowLeft");
    await expect(page.getByText("Already under a top-level product")).toBeVisible();

    // epics: drag the second onto the first (nests: same-level nesting is on), Alt+Left lifts it, Alt+Right nests it again
    const epicBtn = (k: string) => btn(new RegExp(`^Epic ${k} `));
    await expect(epicBtn(e2)).toBeVisible();
    const top = await level(epicBtn(e1));
    expect(await level(epicBtn(e2))).toBe(top);
    await epicBtn(e2).dragTo(epicBtn(e1));
    await expect(page.getByText(`Moved ${e2}`)).toBeVisible();
    await expect.poll(async () => Number(await level(epicBtn(e2)))).toBe(Number(top) + 1);
    await epicBtn(e2).focus();
    await page.keyboard.press("Alt+ArrowLeft");
    await expect.poll(() => level(epicBtn(e2))).toBe(top);
    await epicBtn(e2).focus();
    await page.keyboard.press("Alt+ArrowRight");
    await expect.poll(async () => Number(await level(epicBtn(e2)))).toBe(Number(top) + 1);
    // dropping on the project makes it top-level again
    await epicBtn(e2).dragTo(project);
    await expect.poll(() => level(epicBtn(e2))).toBe(top);

    // an epic never changes project: the tree says so, and the API refuses the move
    await epicBtn(e2).dragTo(btn(/^Project WEB /));
    await expect(page.getByText(/keeps its project/)).toBeVisible();
    const cross = await request.post(`${API}/items/${e2}/move`, { data: { parent: "WEB-120" } });
    expect(cross.status()).toBe(400);
    expect((await cross.json()).error.code).toBe("cross_project_move");
  } finally {
    await request.patch(`${API}/settings`, { data: { allow_same_level_nesting: false } });
  }
});

test("milestones and iterations show on a month calendar", async ({ page, request }) => {
  const name = `ms-${unique()}`;
  const due = new Date().toISOString().slice(0, 8) + "15";
  const r = await request.post(`${API}/projects/WEB/milestones`, {
    data: { name, due_date: due },
  });
  expect(r.ok()).toBeTruthy();
  await page.goto("/w/acme/setup/planning");
  const cal = page.getByRole("group", { name: /Milestones and iterations: / });
  await expect(cal.locator(`[data-day="${due}"] .ev.milestone`)).toContainText(name);
  const title = await cal.getAttribute("aria-label");
  await page.getByRole("button", { name: "Next month" }).click();
  await expect(cal).not.toHaveAttribute("aria-label", title!);
  await page.getByRole("button", { name: "Today" }).click();
  await expect(cal).toHaveAttribute("aria-label", title!);
});

test("the risk heat map is labelled and a cell filters the register", async ({ page, request }) => {
  const a = `heat-a-${unique()}`;
  const b = `heat-b-${unique()}`;
  for (const [title, probability, impact] of [
    [a, 2, 3],
    [b, 5, 5],
  ] as const) {
    const r = await request.post(`${API}/risks`, {
      data: { project: "WEB", title, probability, impact },
    });
    expect(r.ok()).toBeTruthy();
  }
  await page.goto("/w/acme/setup/risks");
  const map = page.getByRole("table", { name: "Risk heat map" });
  await expect(map.getByText("Probability →")).toBeVisible();
  await expect(map.getByRole("columnheader", { name: /^P3/ })).toBeVisible();
  await expect(map.getByRole("rowheader", { name: /^I5/ })).toBeVisible();
  await expect(page.getByRole("cell", { name: a })).toBeVisible();
  await expect(page.getByRole("cell", { name: b })).toBeVisible();
  await map.getByRole("button", { name: /^probability 2 impact 3: [1-9]/ }).click();
  await expect(page.getByRole("heading", { name: "Risks at P2 × I3" })).toBeVisible();
  await expect(page.getByRole("cell", { name: a })).toBeVisible();
  await expect(page.getByRole("cell", { name: b })).toHaveCount(0);
  await page.getByRole("button", { name: "Show all risks" }).click();
  await expect(page.getByRole("cell", { name: b })).toBeVisible();
});

test("the people screen draws the org tree with team capacity", async ({ page }) => {
  await page.goto("/w/acme/setup/people");
  const tree = page.getByRole("tree", { name: "Reporting lines" });
  await expect(tree).toBeVisible();
  await expect(tree.getByRole("treeitem").first()).toContainText(/h\/wk/);
  await expect(tree).toContainText(/team \d+ h\/wk/);
});

test("pasting a list adds many dimension values at once", async ({ page }) => {
  const key = `d${unique()}`;
  await page.goto("/w/acme/setup/dimensions");
  const form = page.getByRole("form", { name: "Add dimension" });
  await form.getByLabel("Key").fill(key);
  await form.getByLabel("Name").fill(`Dim ${key}`);
  await form.getByRole("button", { name: "Add dimension" }).click();
  const paste = page.getByLabel(`Paste values for ${key}`);
  await paste.fill("alpha, beta\ngamma\n\nalpha");
  await expect(page.getByText("3 values to add")).toBeVisible();
  await paste.locator("xpath=following-sibling::button").click();
  await expect(page.getByText(`Added 3 value(s) to ${key}`)).toBeVisible();
  await expect(page.locator(".card", { hasText: `(${key})` })).toContainText(
    "alpha · beta · gamma",
  );
});
