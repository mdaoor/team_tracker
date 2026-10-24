import { expect, test } from "@playwright/test";

test("an admin saves the Jira connection and the webhook address appears once", async ({
  page,
}) => {
  await page.goto("/w/acme/setup/jira");
  await page.getByLabel("Jira URL").fill("https://acme.atlassian.net");
  await page.getByLabel("Account e-mail").fill("bot@acme.test");
  await page.getByLabel("API token", { exact: true }).fill("tok-abcdef");
  await page.getByRole("button", { name: "Save connection" }).click();
  await expect(page.getByText(/webhook\/acme\?token=[\w-]{20,}/)).toBeVisible();
  await expect(page.getByLabel("API token (leave empty to keep)")).toHaveValue("");
  // the mapping editor loads for a project
  await page.getByLabel("Project", { exact: true }).selectOption({ index: 1 });
  await page.waitForLoadState("networkidle");
  await page.getByRole("button", { name: "Add row" }).first().click();
  await expect(
    page.getByRole("textbox", { name: "Item type → Jira issue type key" }),
  ).toBeVisible();
});
