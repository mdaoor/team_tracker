// Page mode vs the prototype (docs/spec/team_tracker_presentation.html): full-page screenshots at 1280 wide, both themes,
// compared pixel by pixel over the common height. Acceptance: <= 2 % of pixels differ.
import { expect, test } from "@playwright/test";
import pixelmatch from "pixelmatch";
import { PNG } from "pngjs";

for (const scheme of ["light", "dark"] as const) {
  test(`page mode is within 2% of the prototype (${scheme})`, async ({
    browser,
  }) => {
    const shot = async (url: string, ready?: string) => {
      const page = await browser.newPage({
        viewport: { width: 1280, height: 900 },
        colorScheme: scheme,
      });
      await page.goto(url);
      if (ready) await page.waitForSelector(ready, { state: "attached" });
      const png = PNG.sync.read(await page.screenshot({ fullPage: true }));
      await page.close();
      return png;
    };
    const a = await shot("/prototype.html");
    const c = await shot(
      `/?src=/golden.json&mode=page&theme=${scheme}`,
      "html[data-ready='1']",
    );
    const w = 1280, h = Math.min(a.height, c.height); // prettier-ignore
    const crop = (img: PNG) => {
      const o = new PNG({ width: w, height: h });
      PNG.bitblt(img, o, 0, 0, w, h, 0, 0);
      return o;
    };
    const bad = pixelmatch(
      crop(a).data,
      crop(c).data,
      new PNG({ width: w, height: h }).data,
      w,
      h,
      { threshold: 0.1 },
    );
    const pct = (bad / (w * h)) * 100;
    console.log(
      `page vs prototype (${scheme}): ${pct.toFixed(3)}% of pixels differ`,
    );
    expect(pct).toBeLessThanOrEqual(2);
  });
}
