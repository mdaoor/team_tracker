import { expect, test } from "@playwright/test";
import { API } from "./api";

test("a template is created in the form editor, shows inline errors, and starts a presentation", async ({
  page,
}) => {
  await page.goto("/w/acme/present/templates");
  await expect(
    page.getByRole("list", { name: "Templates" }).getByText("Portfolio review"),
  ).toBeVisible();
  await page.getByRole("button", { name: "New template" }).click();
  await page.getByRole("button", { name: "JSON" }).click();
  const box = page.getByLabel("Template JSON");
  await box.fill(
    JSON.stringify({
      schema_version: 1,
      key: "e2e_tpl",
      name: "E2E template",
      slides: [{ kind: "nope", config: {} }],
    }),
  );
  await expect(page.getByLabel("Template problems").getByText("/slides/0/kind")).toBeVisible();
  await expect(page.getByRole("button", { name: "Save" })).toBeDisabled();
  await box.fill(
    JSON.stringify({
      schema_version: 1,
      key: "e2e_tpl",
      name: "E2E template",
      slides: [
        { kind: "title", title_template: "${scope.label}", config: {} },
        { kind: "kpis", config: {} },
      ],
    }),
  );
  await expect(page.getByLabel("Template problems")).toHaveCount(0);
  await page.getByRole("button", { name: "Save" }).click();
  await expect(
    page.getByRole("list", { name: "Templates" }).getByText("E2E template"),
  ).toBeVisible();
  await page
    .getByRole("listitem")
    .filter({ hasText: "E2E template" })
    .getByRole("button", { name: "Use" })
    .click();
  await expect(page).toHaveURL(/\/present\/[0-9a-f-]{20,}/);
  await expect(page.getByRole("list", { name: "Slide order" }).locator("li")).toHaveCount(2);
});

test("R1.3 deleting a template asks first, and Undo brings it back", async ({ page, request }) => {
  const key = "e2e_delete_me";
  const made = await request.post(`${API}/presentation-templates`, {
    data: {
      body: {
        schema_version: 1,
        key,
        name: "Delete me template",
        slides: [{ kind: "title", title_template: "${scope.label}", config: {} }],
      },
    },
  });
  expect(made.ok()).toBeTruthy();
  await page.goto("/w/acme/present/templates");
  const row = () => page.getByRole("listitem").filter({ hasText: "Delete me template" });
  await row().getByRole("button", { name: "Delete" }).click();
  const dlg = page.getByRole("dialog", { name: /Delete “Delete me template”\?/ });
  await expect(dlg).toBeVisible();
  await dlg.getByRole("button", { name: "Cancel" }).click();
  await expect(row()).toHaveCount(1);
  await row().getByRole("button", { name: "Delete" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
  await expect(row()).toHaveCount(0);
  await page.getByRole("button", { name: "Undo" }).click();
  await expect(row()).toHaveCount(1);
  const list = (await (await request.get(`${API}/presentation-templates`)).json()).data as {
    key: string;
  }[];
  expect(list.map((t) => t.key)).toContain(key);
});
