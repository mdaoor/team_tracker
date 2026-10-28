import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { demoBridge } from "./demo";
import { PersonalApp } from "./PersonalApp";
import axe from "axe-core";

async function open() {
  const bridge = demoBridge();
  render(<PersonalApp bridge={bridge} pollMs={0} />);
  await screen.findByRole("heading", { name: /Fri 23 October 2026/ });
  fireEvent.click(screen.getByRole("button", { name: "Calendars" }));
  const dlg = await screen.findByRole("dialog", { name: "Calendars" });
  return { bridge, dlg };
}

describe("calendar settings (15 M7/M8)", () => {
  it("lists each calendar with a switch; unchecking one excludes it and checking brings it back", async () => {
    const { bridge, dlg } = await open();
    const web = await within(dlg).findByRole("checkbox", {
      name: /Web team/,
    });
    expect(web).toBeChecked();
    fireEvent.click(web);
    await waitFor(() => expect(web).not.toBeChecked());
    expect(bridge.calls).toContain("calExclude");
    expect(await within(dlg).findByText(/not read/)).toBeInTheDocument();
    fireEvent.click(web);
    await waitFor(() => expect(web).toBeChecked());
  });

  it("calendars the provider hides are shown but cannot be switched on from here", async () => {
    const { dlg } = await open();
    const hol = await within(dlg).findByRole("checkbox", { name: /Holidays/ });
    expect(hol).not.toBeChecked();
    expect(hol).toBeDisabled();
    expect(within(dlg).getByText(/hidden by google/i)).toBeInTheDocument();
  });

  it("refresh re-lists the calendars and says what changed", async () => {
    const { dlg } = await open();
    fireEvent.click(
      await within(dlg).findByRole("button", { name: "Refresh the list" }),
    );
    expect(await within(dlg).findByRole("status")).toHaveTextContent(
      /google: 1 added \(Platform\), 0 removed/,
    );
    expect(
      await within(dlg).findByRole("checkbox", { name: /Platform/ }),
    ).toBeChecked();
  });

  it("suppress lists: add an attendee and a domain, remove one; bad input is explained", async () => {
    const { dlg } = await open();
    const input = within(dlg).getByLabelText(/Never offer meetings with/);
    fireEvent.change(input, { target: { value: "Sara@Acme.com" } });
    fireEvent.click(within(dlg).getByRole("button", { name: "Suppress" }));
    expect(
      await within(dlg).findByRole("button", { name: "Remove sara@acme.com" }),
    ).toBeInTheDocument();
    fireEvent.change(input, { target: { value: "@globex.com" } });
    fireEvent.click(within(dlg).getByRole("button", { name: "Suppress" }));
    expect(
      await within(dlg).findByRole("button", { name: "Remove globex.com" }),
    ).toBeInTheDocument();
    fireEvent.click(
      within(dlg).getByRole("button", { name: "Remove globex.com" }),
    );
    await waitFor(() =>
      expect(
        within(dlg).queryByRole("button", { name: "Remove globex.com" }),
      ).not.toBeInTheDocument(),
    );
    fireEvent.change(input, { target: { value: "a b@c" } });
    fireEvent.click(within(dlg).getByRole("button", { name: "Suppress" }));
    await waitFor(() =>
      expect(within(dlg).getByRole("status")).toHaveTextContent(
        /not an e-mail/,
      ),
    );
  });

  it("has no accessibility violations", async () => {
    const { dlg } = await open();
    await within(dlg).findByRole("checkbox", { name: /Web team/ });
    const res = await axe.run(document.body, {
      runOnly: {
        type: "tag",
        values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"],
      },
      rules: {
        "color-contrast": { enabled: false },
        region: { enabled: false },
      },
    });
    expect(
      res.violations.map((v) => `${v.id}: ${v.nodes[0]?.html.slice(0, 90)}`),
    ).toEqual([]);
  });
});
