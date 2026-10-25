import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

/** Phase 15 acceptance, in a real browser: axe (0 serious, with colour contrast) on every new screen in light and dark, usable at 390 px
 * with no horizontal scroll, and Today plus the tray operable from the keyboard alone. */
const DEMO = "/demo.html";

type Screen = [string, (p: Page) => Promise<void>];
const open = async (p: Page, query = "") => {
  await p.goto(DEMO + query);
  await p.getByRole("heading", { name: /Fri 23 October 2026/ }).waitFor();
  await p.getByRole("region", { name: /Ready to send/ }).waitFor();
};
const SCREENS: Screen[] = [
  ["today + tray", async () => undefined],
  [
    "today with the edit form",
    async (p) => {
      await p.getByRole("button", { name: /Edit Client sync/ }).click();
    },
  ],
  [
    "week",
    async (p) => {
      await p.getByRole("button", { name: /^Week view/ }).click();
      await p.getByRole("heading", { name: /Week of/ }).waitFor();
    },
  ],
  [
    "month",
    async (p) => {
      await p.getByRole("button", { name: /^Month view/ }).click();
      await p.getByRole("heading", { name: "October 2026" }).waitFor();
    },
  ],
  [
    "end of day",
    async (p) => {
      await p.getByRole("button", { name: "End of day" }).click();
      await p.getByRole("dialog", { name: /End of day/ }).waitFor();
    },
  ],
  [
    "weekly review",
    async (p) => {
      await p.getByRole("button", { name: "Weekly review" }).click();
      await p.getByRole("dialog", { name: /Weekly review/ }).waitFor();
    },
  ],
  [
    "recurring editor",
    async (p) => {
      await p.getByRole("button", { name: "Repeat…" }).click();
      await p.getByLabel("Say it in words").fill("every weekday 7:00 run 30m");
      await p.getByText("every weekday at 07:00").waitFor();
    },
  ],
  [
    "privacy settings",
    async (p) => {
      await p.getByRole("button", { name: "Privacy" }).click();
      await p.getByRole("dialog", { name: "Privacy" }).waitFor();
    },
  ],
  [
    "keyboard help",
    async (p) => {
      await p.keyboard.press("?");
      await p.getByRole("dialog", { name: "Keyboard" }).waitFor();
    },
  ],
];

for (const scheme of ["light", "dark"] as const) {
  for (const [name, setup] of SCREENS) {
    test(`axe (${scheme}): ${name}`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await open(page);
      await setup(page);
      const res = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
        .analyze();
      const severe = res.violations.filter(
        (v) => v.impact === "serious" || v.impact === "critical",
      );
      const total = res.passes.length + res.violations.length;
      const score = total ? Math.round((res.passes.length / total) * 100) : 100;
      console.log(
        `a11y ${scheme} ${name}: score ${score}, violations ${res.violations.map((v) => `${v.id}(${v.impact}) x${v.nodes.length}`).join("; ") || "none"}`,
      );
      expect(
        severe.map((v) => `${v.id}: ${v.nodes[0]?.html.slice(0, 120)}`),
      ).toEqual([]);
      expect(score).toBeGreaterThanOrEqual(95);
    });
  }

  test(`axe (${scheme}): first-run privacy explainer`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: scheme });
    await page.goto(`${DEMO}?firstrun=1`);
    await page.getByRole("dialog", { name: "Your time stays yours" }).waitFor();
    const res = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    expect(
      res.violations.filter(
        (v) => v.impact === "serious" || v.impact === "critical",
      ),
    ).toEqual([]);
  });
}

test.describe("390 px (mobile layout)", () => {
  test.use({ viewport: { width: 390, height: 844 } });
  const overflow = (p: Page) =>
    p.evaluate(
      () =>
        document.documentElement.scrollWidth -
        document.documentElement.clientWidth,
    );

  for (const [name, setup] of SCREENS.slice(0, 4)) {
    test(`no horizontal scroll and axe-clean: ${name}`, async ({ page }) => {
      await open(page);
      await setup(page);
      expect(await overflow(page)).toBeLessThanOrEqual(0);
      const res = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
        .analyze();
      expect(
        res.violations.filter(
          (v) => v.impact === "serious" || v.impact === "critical",
        ),
      ).toEqual([]);
    });
  }

  test("Today and the tray fit and every control is reachable", async ({
    page,
  }) => {
    await open(page);
    const box = await page
      .getByRole("region", { name: /Ready to send/ })
      .boundingBox();
    expect(box && box.x >= 0 && box.x + box.width <= 390).toBeTruthy();
    const send = page.getByRole("button", { name: /^Send Sprint planning/ });
    await send.scrollIntoViewIfNeeded();
    const b = await send.boundingBox();
    expect(b && b.height >= 32).toBeTruthy();
    await send.click();
    await expect(
      page
        .getByRole("status")
        .filter({ hasText: /Sent 1 h Sprint planning → WEB/ }),
    ).toBeVisible();
  });
});

test.describe("keyboard only", () => {
  test("N adds a line, Space times it, the tray sends with S, D/W/M switch views", async ({
    page,
  }) => {
    await open(page);
    await page.keyboard.press("n");
    await expect(page.getByLabel("Add an item, one line")).toBeFocused();
    await page.keyboard.type("run 30m at 6pm");
    await page.keyboard.press("Enter");
    await expect(
      page.getByRole("status").filter({ hasText: /Added run/ }),
    ).toBeVisible();

    // walk to the first timeline row and start its timer with Space
    await page.getByLabel("Add an item, one line").press("Escape");
    await page.locator('[data-item-id="t-login"]').focus();
    await page.keyboard.press("Space");
    await expect(
      page.getByRole("group", { name: "Running timer" }),
    ).toContainText("fix login");

    // the tray: focus a card and press S
    await page.locator('[data-tray-id="m-client"]').focus();
    await page.keyboard.press("s");
    await expect(
      page
        .getByRole("status")
        .filter({ hasText: /Sent 30 min Client sync with Globex → DATA/ }),
    ).toBeVisible();

    await page.keyboard.press("w");
    await expect(page.getByRole("heading", { name: /Week of/ })).toBeVisible();
    await page.keyboard.press("m");
    await expect(
      page.getByRole("heading", { name: "October 2026" }),
    ).toBeVisible();
    await page.keyboard.press("d");
    await expect(
      page.getByRole("heading", { name: /Fri 23 October 2026/ }),
    ).toBeVisible();
  });

  test("Tab reaches every control of a tray card and Escape closes a dialog back to its opener", async ({
    page,
  }) => {
    await open(page);
    const card = page.locator('[data-tray-id="m-sprint"]');
    await card.focus();
    await page.keyboard.press("Tab");
    await expect(card.getByRole("button", { name: /^Send/ })).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(card.getByRole("button", { name: /^Edit/ })).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(card.getByRole("button", { name: /Not work/ })).toBeFocused();
    const opener = page.getByRole("button", { name: "End of day" });
    await opener.focus();
    await page.keyboard.press("Enter");
    await page.getByRole("dialog", { name: /End of day/ }).waitFor();
    await page.keyboard.press("Escape");
    await expect(opener).toBeFocused();
  });
});
