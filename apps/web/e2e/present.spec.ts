import { expect, test } from "@playwright/test";

const API = "http://localhost:8010/api/v1/w/acme";

test("start from a template, reorder, and the order survives a reload", async ({ page }) => {
  await page.goto("/w/acme/present");
  await page.getByRole("button", { name: "Portfolio review" }).click();
  const list = page.getByRole("list", { name: "Slide order" });
  await expect(list.locator("li")).toHaveCount(9);
  await expect(list.locator("li").first()).toContainText("Key numbers");
  await page.getByRole("button", { name: "Move Key numbers down" }).click();
  await expect(list.locator("li").nth(1)).toContainText("Key numbers");
  await page.reload();
  await expect(list.locator("li")).toHaveCount(9);
  await expect(list.locator("li").nth(1)).toContainText("Key numbers");
  await expect(page.getByTitle("Slide preview")).toHaveAttribute("src", /\/p\/acme\/.+\?slide=1/);
});

test("a matrix view becomes a slide whose numbers equal the view's, and exports to PPTX", async ({
  page,
  request,
}) => {
  await page.goto("/w/acme/views?layout=matrix");
  await expect(page.locator(".vmatrix table")).toBeVisible();
  await page.getByRole("button", { name: "Add to presentation" }).click();
  await page.getByRole("menuitem", { name: /New presentation/ }).click();
  await expect(page.getByText("Added to presentation")).toBeVisible();
  const decks = (await (await request.get(`${API}/presentations`)).json()).data as {
    id: string;
    slides: { kind: string; config: Record<string, unknown> }[];
  }[];
  const deck = decks.find((d) =>
    d.slides.some((s) => s.kind === "chart" && s.config.rows === "team"),
  );
  expect(deck).toBeTruthy();
  const data = await (await request.get(`${API}/presentations/${deck!.id}/data`)).json();
  const agg = await (
    await request.post(`${API}/query/aggregate`, {
      data: { rows: "team", cols: "stage", measure: "count" },
    })
  ).json();
  const slide = deck!.slides.find((s) => s.kind === "chart")! as unknown as { id: string };
  expect(data.slide_data[slide.id].cells).toEqual(agg.cells);
  const pptx = await request.post(`${API}/presentations/${deck!.id}/render?format=pptx`);
  expect(pptx.ok()).toBeTruthy();
  expect((await pptx.body()).subarray(0, 2).toString()).toBe("PK");
});
