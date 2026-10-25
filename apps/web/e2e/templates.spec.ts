import { expect, test } from "@playwright/test";

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
