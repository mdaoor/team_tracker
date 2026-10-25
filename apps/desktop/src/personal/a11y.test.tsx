import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import axe from "axe-core";
import { describe, expect, it } from "vitest";
import { demoBridge } from "./demo";
import { PersonalApp } from "./PersonalApp";

/** Accessibility audit of every new screen with axe-core in jsdom: no serious or critical violation (plan 15 acceptance), and in fact none
 * at all for WCAG 2.1 A/AA. Colour contrast needs a real renderer and is checked in the Playwright run (e2e/a11y.spec.ts, light and dark). */
async function audit(label: string): Promise<void> {
  const res = await axe.run(document.body, {
    runOnly: {
      type: "tag",
      values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"],
    },
    rules: { "color-contrast": { enabled: false }, region: { enabled: false } },
  });
  const severe = res.violations.filter(
    (v) => v.impact === "serious" || v.impact === "critical",
  );
  expect(
    severe.map((v) => `${label}: ${v.id} ${v.nodes[0]?.html.slice(0, 100)}`),
  ).toEqual([]);
  expect(
    res.violations.map(
      (v) =>
        `${label}: ${v.id} (${v.impact}) ${v.nodes[0]?.html.slice(0, 100)}`,
    ),
  ).toEqual([]);
}

async function open(opts: Parameters<typeof demoBridge>[0] = {}) {
  const bridge = demoBridge(opts);
  render(<PersonalApp bridge={bridge} pollMs={0} />);
  await screen.findByRole("heading", { name: /Fri 23 October 2026/ });
  await screen.findByRole("region", { name: /Ready to send/ });
  return bridge;
}

describe("axe: the audit itself", () => {
  it("really finds problems (an unlabelled button and an image without alt)", async () => {
    render(
      <main>
        <button type="button" />
        <img src="x.png" />
      </main>,
    );
    const res = await axe.run(document.body, {
      rules: {
        "color-contrast": { enabled: false },
        region: { enabled: false },
      },
    });
    expect(res.violations.map((v) => v.id).sort()).toEqual(
      expect.arrayContaining(["button-name", "image-alt"]),
    );
  });
});

describe("axe: new screens", () => {
  it("Today with the tray", async () => {
    await open();
    await audit("today");
  });

  it("Today with the timer bar, an edit form and the project chips open", async () => {
    await open();
    fireEvent.click(
      screen.getByRole("button", { name: /Start timer for fix login/ }),
    );
    await screen.findByRole("group", { name: "Running timer" });
    fireEvent.click(screen.getByRole("button", { name: /Edit Client sync/ }));
    await audit("today + timer + edit");
  });

  it("Week", async () => {
    await open();
    fireEvent.keyDown(window, { key: "w" });
    await screen.findByRole("heading", { name: /Week of/ });
    await audit("week");
  });

  it("Month", async () => {
    await open();
    fireEvent.keyDown(window, { key: "m" });
    await screen.findByRole("heading", { name: "October 2026" });
    await audit("month");
  });

  it("end-of-day card", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: "End of day" }));
    await screen.findByRole("dialog", { name: /End of day/ });
    await audit("end of day");
  });

  it("end-of-day card while picking", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: "End of day" }));
    const dlg = await screen.findByRole("dialog", { name: /End of day/ });
    fireEvent.click(within(dlg).getByRole("button", { name: "Pick" }));
    await audit("end of day (pick)");
  });

  it("weekly review", async () => {
    await open();
    fireEvent.keyDown(window, { key: "r" });
    await screen.findByRole("dialog", { name: /Weekly review/ });
    await audit("weekly review");
  });

  it("recurring editor with a preview", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Repeat…" }));
    const dlg = await screen.findByRole("dialog", { name: "Repeating item" });
    fireEvent.change(within(dlg).getByLabelText("Say it in words"), {
      target: { value: "every weekday 7:00 run 30m" },
    });
    await within(dlg).findByText("every weekday at 07:00");
    await audit("recurring editor");
  });

  it("first-run privacy explainer", async () => {
    const bridge = demoBridge({ firstRun: true });
    render(<PersonalApp bridge={bridge} pollMs={0} />);
    await screen.findByRole("dialog", { name: "Your time stays yours" });
    await audit("first run");
  });

  it("privacy settings with the sent list", async () => {
    await open();
    fireEvent.click(
      within(
        screen.getByRole("region", { name: /Ready to send/ }),
      ).getAllByRole("button", { name: /^Send/ })[0],
    );
    await screen.findByText(/Sent /);
    fireEvent.click(screen.getByRole("button", { name: "Privacy" }));
    const dlg = await screen.findByRole("dialog", { name: "Privacy" });
    await within(dlg).findByRole("table");
    await waitFor(() =>
      expect(within(dlg).getAllByRole("row").length).toBeGreaterThan(1),
    );
    await audit("privacy settings");
  });

  it("keyboard help", async () => {
    await open();
    fireEvent.keyDown(window, { key: "?" });
    await screen.findByRole("dialog", { name: "Keyboard" });
    await audit("keyboard help");
  });
});
