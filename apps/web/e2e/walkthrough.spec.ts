import { expect, test } from "@playwright/test";
import { API } from "./api";

/** Acceptance walkthrough of the three UX flows (docs/spec/team_tracker_ux.md §7.1-7.3), recorded as video with
 *  `TT_VIDEO=1 pnpm exec playwright test e2e/walkthrough.spec.ts` (test-results/**\/video.webm; CI uploads them). Each flow also asserts its outcome, so the video is evidence, not decoration. */
test.use({ viewport: { width: 1280, height: 720 } });

const unique = () => Math.random().toString(36).slice(2, 6);

test("7.1 reporter, morning: one line starts the work and it shows as 'now'", async ({
  page,
  request,
}) => {
  const title = `retry refactor on the pipeline ${unique()}`;
  const r = await request.post(`${API}/capture/batches`, {
    data: {
      workspace: "acme",
      messages: [
        {
          actor: "@mahmoud",
          at: new Date().toISOString(),
          client_id: crypto.randomUUID(),
          action: "start",
          item: { title },
          project: "DATA",
          estimate: { value: 2, unit: "days" },
        },
      ],
    },
  });
  expect(r.ok()).toBeTruthy();
  expect((await r.json()).results[0].status).toMatch(/applied/);
  await page.goto("/w/acme/views?layout=now");
  await expect(page.getByText(title).first()).toBeVisible();
});

test("7.2 manager, mid-morning: one click settles a review row", async ({ page, request }) => {
  const phrase = "mystery work " + unique();
  const r = await request.post(`${API}/capture/batches`, {
    data: {
      workspace: "acme",
      messages: [
        {
          actor: "@mahmoud",
          at: new Date().toISOString(),
          client_id: crypto.randomUUID(),
          action: "create",
          item: { title: "fix " + phrase },
          project: phrase,
        },
      ],
    },
  });
  expect(r.ok()).toBeTruthy();
  await page.goto("/w/acme/review");
  const row = page.getByRole("group", { name: new RegExp(phrase) });
  await expect(row).toBeVisible();
  await row.getByLabel("other project").selectOption({ index: 1 });
  await expect(row).toHaveCount(0);
});

test("7.3 senior manager, Friday: the deck, the stuck column, team x stage", async ({ page }) => {
  await page.goto("/w/acme/present");
  await page.getByRole("button", { name: /Portfolio review/ }).click();
  await expect(page).toHaveURL(/\/present\/[^/]+$/);
  await expect(page.getByLabel("Title").first()).toBeVisible();
  await page.goto("/w/acme/views?layout=pipeline");
  await expect(page.getByRole("tab", { name: "pipeline", selected: true })).toBeVisible();
  await page.goto("/w/acme/views?layout=matrix");
  await expect(page.locator(".vmatrix table")).toBeVisible();
  await expect(page).toHaveURL(/layout=matrix/);
});
