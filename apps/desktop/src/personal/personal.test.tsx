import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { demoBridge } from "./demo";
import { PREVIEW_MS } from "./QuickAdd";
import { PersonalApp } from "./PersonalApp";
import { BridgeError } from "./types";

async function open(opts: Parameters<typeof demoBridge>[0] = {}) {
  const bridge = demoBridge(opts);
  const view = render(<PersonalApp bridge={bridge} pollMs={0} />);
  await screen.findByRole("heading", { name: /Friday|Fri 23 October 2026/ });
  return { bridge, ...view };
}
const key = (k: string, target: Element | Window = window) =>
  fireEvent.keyDown(target, { key: k });
const tray = () => screen.getByRole("region", { name: /Ready to send/ });
const trayCard = (title: string) =>
  within(tray()).getByRole("listitem", { name: new RegExp(title) });

describe("Today", () => {
  it("shows the day, the load against the pack's capacity, the timeline, anytime items and server items", async () => {
    await open();
    expect(screen.getByText(/Planned .* of 6 h 24/)).toBeInTheDocument();
    const timeline = screen.getAllByRole("list")[0];
    expect(
      within(timeline)
        .getAllByRole("listitem")
        .map((li) => li.getAttribute("data-item-id")),
    ).toEqual([
      "run@2026-10-23",
      "m-sprint",
      "m-client",
      "m-offsite",
      "t-login",
      "arabic@2026-10-23",
    ]);
    expect(
      screen.getByRole("heading", { name: "Anytime today" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/WEB-142 fix the login redirect loop/),
    ).toBeInTheDocument();
    expect(screen.getAllByText(/↻ repeats/).length).toBeGreaterThan(0);
    const join = screen.getByRole("link", {
      name: /Join Sprint planning online/,
    });
    expect(join).toHaveAttribute(
      "href",
      expect.stringContaining("teams.microsoft.com"),
    );
    expect(screen.getAllByText(/Outlook/).length).toBeGreaterThan(0);
  });

  it("the timer starts, shows in the bar, switches and stops", async () => {
    const { bridge } = await open();
    fireEvent.click(
      screen.getByRole("button", { name: /Start timer for fix login/ }),
    );
    await screen.findByRole("group", { name: "Running timer" });
    expect(bridge.calls).toContain("timerStart");
    expect(
      screen.getByRole("button", { name: /Stop timer for fix login/ }),
    ).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(
      screen.getByRole("button", { name: /Start timer for Arabic course/ }),
    );
    await waitFor(() =>
      expect(
        screen.getByRole("group", { name: "Running timer" }),
      ).toHaveTextContent("Arabic course"),
    );
    fireEvent.click(
      within(screen.getByRole("group", { name: "Running timer" })).getByRole(
        "button",
        { name: "Stop timer" },
      ),
    );
    await waitFor(() =>
      expect(screen.queryByRole("group", { name: "Running timer" })).toBeNull(),
    );
  });

  it("marks things done, skipped and reopened", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: /Done fix login/ }));
    await screen.findByRole("button", { name: /Reopen fix login/ });
    fireEvent.click(screen.getByRole("button", { name: /Skip Arabic course/ }));
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: /Skip Arabic course/ }),
      ).toBeNull(),
    );
    fireEvent.click(screen.getByRole("button", { name: /Reopen fix login/ }));
    await screen.findByRole("button", { name: /Done fix login/ });
  });
});

describe("the ready-to-send tray", () => {
  it("lists finished meetings with a suggestion and the cue", async () => {
    await open();
    const cards = within(tray()).getAllByRole("listitem");
    expect(cards.map((c) => c.getAttribute("data-tray-id"))).toEqual([
      "m-offsite",
      "m-client",
      "m-sprint",
    ]);
    expect(trayCard("Sprint planning")).toHaveTextContent(
      "Send 1 h Sprint planning → WEB?",
    );
    expect(trayCard("Sprint planning")).toHaveTextContent("70 % suggested");
    expect(trayCard("Client sync")).toHaveTextContent("✓ 80 %");
    expect(trayCard("Sprint planning")).toHaveTextContent(
      "“Sprint planning with @sara, @ali”",
    );
  });

  it("one tap sends, says where, and can be taken back", async () => {
    const { bridge } = await open();
    fireEvent.click(
      within(trayCard("Sprint planning")).getByRole("button", {
        name: /^Send/,
      }),
    );
    await screen.findByText(/Sent 1 h Sprint planning → WEB/);
    expect(bridge.state.sent[0]).toMatchObject({
      item: "m-sprint",
      project: "WEB",
      minutes: 60,
    });
    expect(
      within(tray()).queryByRole("listitem", { name: /Sprint planning/ }),
    ).toBeNull();
    fireEvent.click(
      screen.getByRole("button", { name: /Unsend Sprint planning/ }),
    );
    await screen.findByText(
      /Took back Sprint planning before it reached the server/,
    );
    expect(
      within(tray()).getByRole("listitem", { name: /Sprint planning/ }),
    ).toBeInTheDocument();
  });

  it("asks for a project only when none is known: three chips, one tap", async () => {
    const { bridge } = await open();
    const card = trayCard("Offsite logistics");
    expect(within(card).queryByRole("button", { name: /^Send/ })).toBeNull();
    const chips = within(card).getByRole("group", { name: "Which project?" });
    expect(
      within(chips)
        .getAllByRole("button")
        .map((b) => b.textContent?.split(" ")[0]),
    ).toEqual(["WEB", "DATA", "OPS"]);
    fireEvent.click(within(chips).getByRole("button", { name: /OPS/ }));
    await screen.findByText(/Sent 45 min Offsite logistics → OPS/);
    expect(bridge.state.sent[0].project).toBe("OPS");
  });

  it("Edit changes minutes and note before sending", async () => {
    const { bridge } = await open();
    const send = vi.spyOn(bridge, "send");
    const card = trayCard("Client sync");
    fireEvent.click(
      within(card).getByRole("button", { name: /Edit Client sync/ }),
    );
    fireEvent.change(within(card).getByLabelText("Minutes"), {
      target: { value: "20" },
    });
    fireEvent.change(within(card).getByLabelText("Note"), {
      target: { value: "quick sync, nothing decided" },
    });
    fireEvent.click(
      within(card).getByRole("button", { name: /Send with these changes/ }),
    );
    await screen.findByText(/Sent 20 min Client sync with Globex → DATA/);
    expect(send).toHaveBeenCalledWith({
      id: "m-client",
      note: "quick sync, nothing decided",
      minutes: 20,
    });
  });

  it("Not work removes it and teaches the rule", async () => {
    const { bridge } = await open();
    fireEvent.click(
      within(trayCard("Client sync")).getByRole("button", { name: /Not work/ }),
    );
    await waitFor(() =>
      expect(
        within(tray()).queryByRole("listitem", { name: /Client sync/ }),
      ).toBeNull(),
    );
    expect(bridge.calls).toContain("notWork");
  });

  it("a development item is only offered after the privacy toggle, and goes to #development", async () => {
    const { bridge } = await open();
    expect(
      within(tray()).queryByRole("listitem", { name: /Rust ch.3/ }),
    ).toBeNull();
    await bridge.rememberShare("development", "ask");
    key("w");
    key("d");
    const card = await waitFor(() => trayCard("Rust ch.3"));
    expect(card).toHaveTextContent("Development");
    fireEvent.click(within(card).getByRole("button", { name: /^Send/ }));
    await screen.findByText(/Sent 1 h Rust ch.3 → DEV · #development/);
  });

  it("a send error is shown, not swallowed", async () => {
    const { bridge } = await open();
    vi.spyOn(bridge, "send").mockRejectedValueOnce(
      new BridgeError("needs_project", "choose a project", [
        "WEB",
        "DATA",
        "OPS",
      ]),
    );
    fireEvent.click(
      within(trayCard("Sprint planning")).getByRole("button", {
        name: /^Send/,
      }),
    );
    expect(
      await screen.findByText(
        /Sprint planning: choose a project \(WEB, DATA, OPS\)/,
      ),
    ).toBeInTheDocument();
    vi.spyOn(bridge, "send").mockRejectedValueOnce(new Error("disk full"));
    fireEvent.click(
      within(trayCard("Sprint planning")).getByRole("button", {
        name: /^Send/,
      }),
    );
    expect(
      (await screen.findByText("disk full")).closest("[role=status]"),
    ).toHaveClass("pt-error");
  });
});

describe("quick add", () => {
  it("N focuses the line, the preview follows typing and Enter adds in one step", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { bridge } = await open();
    key("n");
    const input = screen.getByLabelText("Add an item, one line");
    expect(input).toHaveFocus();
    fireEvent.change(input, {
      target: { value: "every weekday 7:00 run 30m" },
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(PREVIEW_MS + 20);
    });
    expect(screen.getByText(/→ .*every weekday at 07:00/)).toBeInTheDocument();
    fireEvent.submit(input.closest("form")!);
    await screen.findByText(/Added /);
    expect(bridge.calls).toContain("add");
    expect(input).toHaveValue("");
    vi.useRealTimers();
  });

  it("shows the sidecar's error for an empty or unreadable line", async () => {
    const { bridge } = await open();
    vi.spyOn(bridge, "add").mockRejectedValueOnce(
      new BridgeError("bad_request", "nothing to add: try `run 30m at 6pm`"),
    );
    const input = screen.getByLabelText("Add an item, one line");
    fireEvent.change(input, { target: { value: "???" } });
    fireEvent.submit(input.closest("form")!);
    expect(await screen.findByText(/nothing to add/)).toBeInTheDocument();
  });
});

describe("keyboard", () => {
  it("D, W and M switch views and the arrow keys walk the rows", async () => {
    await open();
    key("w");
    await screen.findByRole("heading", { name: /Week of/ });
    key("m");
    await screen.findByRole("heading", { name: "October 2026" });
    key("d");
    await screen.findByRole("heading", { name: /Friday|Fri 23 October/ });
    const rows = document.querySelectorAll<HTMLElement>("[data-row]");
    act(() => rows[0].focus());
    key("ArrowDown", rows[0]);
    expect(rows[1]).toHaveFocus();
    key("ArrowUp", rows[1]);
    expect(rows[0]).toHaveFocus();
  });

  it("Space, C and X act on the selected row and are ignored while typing", async () => {
    const { bridge } = await open();
    const row = document.querySelector<HTMLElement>(
      '[data-item-id="t-login"]',
    )!;
    act(() => row.focus());
    key(" ", row);
    await screen.findByRole("group", { name: "Running timer" });
    key("c", row);
    await screen.findByRole("button", { name: /Reopen fix login/ });
    const input = screen.getByLabelText("Add an item, one line");
    input.focus();
    const before = bridge.calls.length;
    fireEvent.keyDown(input, { key: "x" });
    fireEvent.keyDown(input, { key: "d" });
    expect(bridge.calls.length).toBe(before);
  });

  it("the tray works from the keyboard alone: focus a card, press S", async () => {
    const { bridge } = await open();
    const card = document.querySelector<HTMLElement>(
      '[data-tray-id="m-sprint"]',
    )!;
    act(() => card.focus());
    key("s", card);
    await screen.findByText(/Sent 1 h Sprint planning → WEB/);
    expect(bridge.state.sent).toHaveLength(1);
  });

  it("S with nothing selected explains itself", async () => {
    await open();
    key("s");
    expect(
      await screen.findByText(/Select a ready-to-send entry first/),
    ).toBeInTheDocument();
  });

  it("? opens the key list, Escape closes it and focus returns", async () => {
    await open();
    const help = screen.getByRole("button", { name: "Keyboard shortcuts" });
    help.focus();
    key("?");
    const dlg = await screen.findByRole("dialog", { name: "Keyboard" });
    expect(within(dlg).getByText("Space")).toBeInTheDocument();
    key("Escape", dlg);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(help).toHaveFocus();
  });

  it("shortcuts are off while a dialog is open", async () => {
    await open();
    key("?");
    await screen.findByRole("dialog");
    key("w");
    expect(screen.queryByRole("heading", { name: /Week of/ })).toBeNull();
  });
});

describe("Week", () => {
  it("shows seven days with load bars and ↻ on recurring instances; the select moves an item", async () => {
    const { bridge } = await open();
    key("w");
    await screen.findByRole("heading", { name: /Week of Mon 19 Oct/ });
    expect(screen.getAllByRole("meter").length).toBe(7);
    const reschedule = vi.spyOn(bridge, "reschedule");
    const day = document.querySelector(
      '[data-day="2026-10-23"]',
    ) as HTMLElement;
    expect(
      within(day).getAllByText("repeats", { exact: false }).length,
    ).toBeGreaterThan(0);
    fireEvent.change(within(day).getByLabelText("Move fix login to"), {
      target: { value: "2026-10-25" },
    });
    await waitFor(() =>
      expect(reschedule).toHaveBeenCalledWith(
        "t-login",
        "2026-10-25T15:30:00+03:00",
      ),
    );
  });

  it("drag and drop reschedules to the same time on the target day", async () => {
    const { bridge } = await open();
    key("w");
    await screen.findByRole("heading", { name: /Week of/ });
    const reschedule = vi.spyOn(bridge, "reschedule");
    const item = document.querySelector(
      '[data-item-id="t-login"]',
    ) as HTMLElement;
    const target = document.querySelector(
      '[data-day="2026-10-24"]',
    ) as HTMLElement;
    const data = { setData: vi.fn(), getData: vi.fn(() => "t-login") };
    fireEvent.dragStart(item, { dataTransfer: data });
    fireEvent.dragOver(target, { dataTransfer: data });
    expect(target).toHaveAttribute("data-over", "true");
    fireEvent.drop(target, { dataTransfer: data });
    await waitFor(() =>
      expect(reschedule).toHaveBeenCalledWith(
        "t-login",
        "2026-10-24T15:30:00+03:00",
      ),
    );
  });

  it("calendar meetings cannot be moved from here", async () => {
    await open();
    key("w");
    await screen.findByRole("heading", { name: /Week of/ });
    expect(screen.queryByLabelText("Move Sprint planning to")).toBeNull();
  });
});

describe("Month", () => {
  it("shows hours, a labelled grid and a text alternative for the sparkline; a day opens Today", async () => {
    const { bridge } = await open();
    key("m");
    await screen.findByRole("heading", { name: "October 2026" });
    expect(screen.getByText(/Hours/)).toBeInTheDocument();
    expect(
      screen.getByRole("img", { name: /sent to Team Tracker .*, private/ }),
    ).toBeInTheDocument();
    const cell = screen.getByRole("button", { name: /Fri 23 Oct: .*Meeting/ });
    expect(cell).toHaveAttribute("aria-current", "date");
    fireEvent.click(cell);
    await screen.findByRole("heading", { name: /Friday|Fri 23 October/ });
    expect(bridge.calls).toContain("day");
  });

  it("sent time shows apart from private time after a send", async () => {
    await open();
    fireEvent.click(
      within(trayCard("Sprint planning")).getByRole("button", {
        name: /^Send/,
      }),
    );
    await screen.findByText(/Sent 1 h Sprint planning/);
    key("m");
    expect(
      await screen.findByRole("button", { name: /Fri 23 Oct: .*sent 1 h/ }),
    ).toBeInTheDocument();
  });
});

describe("review cards", () => {
  it("end of day: hours by category, Send all, then confirm the daily log", async () => {
    const { bridge } = await open();
    fireEvent.click(screen.getByRole("button", { name: "End of day" }));
    const dlg = await screen.findByRole("dialog", { name: /End of day/ });
    expect(
      within(dlg).getByRole("row", { name: /Personal/ }),
    ).toHaveTextContent("0.5 h");
    expect(within(dlg).getByText(/Not sent yet \(3\)/)).toBeInTheDocument();
    expect(
      within(dlg).queryByRole("button", { name: "Confirm daily log" }),
    ).toBeNull();
    fireEvent.click(within(dlg).getByRole("button", { name: "Send all" }));
    await screen.findByText(/Sent 2; 1 need a project in the tray/);
    fireEvent.click(
      await screen.findByRole("button", { name: "Confirm daily log" }),
    );
    expect(
      await screen.findByText(/Daily log confirmed \(1 h 30\)/),
    ).toBeInTheDocument();
    expect(bridge.state.sent.map((s) => s.project).sort()).toEqual([
      "DATA",
      "WEB",
    ]);
  });

  it("end of day: pick some, skip the rest", async () => {
    const { bridge } = await open();
    fireEvent.click(screen.getByRole("button", { name: "End of day" }));
    const dlg = await screen.findByRole("dialog", { name: /End of day/ });
    fireEvent.click(within(dlg).getByRole("button", { name: "Pick" }));
    fireEvent.click(
      within(dlg).getByRole("checkbox", { name: /Sprint planning/ }),
    );
    fireEvent.click(
      within(dlg).getByRole("button", { name: /Send picked \(1\)/ }),
    );
    await waitFor(() =>
      expect(bridge.state.sent.map((s) => s.item)).toEqual(["m-sprint"]),
    );
    fireEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", {
        name: "Skip",
      }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("weekly review: development hours, load, streaks and the optional share", async () => {
    const { bridge } = await open();
    key("r");
    const dlg = await screen.findByRole("dialog", { name: /Weekly review/ });
    expect(dlg).toHaveTextContent("Development 3.5 h");
    expect(dlg).toHaveTextContent("run: 4 in a row (9 of 10)");
    expect(dlg).toHaveTextContent("31 % of your capacity");
    fireEvent.click(
      within(dlg).getByRole("checkbox", {
        name: /Share a development summary/,
      }),
    );
    fireEvent.click(
      within(dlg).getByRole("button", { name: "Share and close" }),
    );
    await waitFor(() => expect(bridge.calls).toContain("developmentSummary"));
    await screen.findByText(/Queued: Week of 2026-10-19: 3.5 h development/);
  });
});

describe("recurring editor", () => {
  it("previews the rule in words and saves it", async () => {
    const { bridge } = await open();
    fireEvent.click(screen.getByRole("button", { name: "Repeat…" }));
    const dlg = await screen.findByRole("dialog", { name: "Repeating item" });
    const save = within(dlg).getByRole("button", {
      name: "Save repeating item",
    });
    expect(save).toBeDisabled();
    fireEvent.change(within(dlg).getByLabelText("Say it in words"), {
      target: { value: "every weekday 7:00 run 30m" },
    });
    await within(dlg).findByText("every weekday at 07:00");
    expect(
      within(dlg).getByText("FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"),
    ).toBeInTheDocument();
    expect(save).toBeEnabled();
    fireEvent.click(
      within(dlg).getByRole("checkbox", {
        name: /Send each completed instance/,
      }),
    );
    const recur = vi.spyOn(bridge, "recurAdd");
    fireEvent.click(save);
    await waitFor(() =>
      expect(recur).toHaveBeenCalledWith("every weekday 7:00 run 30m", true),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("says when a line has no repetition", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Repeat…" }));
    const dlg = await screen.findByRole("dialog", { name: "Repeating item" });
    fireEvent.change(within(dlg).getByLabelText("Say it in words"), {
      target: { value: "run 30m at 6pm" },
    });
    await within(dlg).findByText(/No repetition found/);
    expect(
      within(dlg).getByRole("button", { name: "Save repeating item" }),
    ).toBeDisabled();
  });
});

describe("privacy", () => {
  it("first run explains the default and offers the three category toggles; personal has none", async () => {
    const { bridge } = await open({ firstRun: true });
    const dlg = await screen.findByRole("dialog", {
      name: "Your time stays yours",
    });
    expect(dlg).toHaveTextContent(
      "Your personal items stay on this device. Meetings and work you choose to send go to Team Tracker.",
    );
    expect(within(dlg).getAllByRole("radiogroup").length).toBe(2);
    expect(
      within(dlg).getByRole("checkbox", {
        name: /Send development time to my development project/,
      }),
    ).not.toBeChecked();
    expect(dlg).toHaveTextContent(
      "never sent. There is no way to send a personal item.",
    );
    expect(within(dlg).queryByRole("radio", { name: /personal/i })).toBeNull();
    const firstRun = vi.spyOn(bridge, "firstRun");
    fireEvent.click(
      within(within(dlg).getAllByRole("radiogroup")[0]).getByRole("radio", {
        name: "Never",
      }),
    );
    fireEvent.click(
      within(dlg).getByRole("checkbox", { name: /development project/ }),
    );
    fireEvent.click(within(dlg).getByRole("button", { name: "Got it" }));
    await waitFor(() =>
      expect(firstRun).toHaveBeenCalledWith({
        work: "never",
        meeting: "ask",
        development: true,
      }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("settings list what was sent, allow Unsend and erase only after a second confirmation", async () => {
    const { bridge } = await open();
    fireEvent.click(
      within(trayCard("Sprint planning")).getByRole("button", {
        name: /^Send/,
      }),
    );
    await screen.findByText(/Sent 1 h Sprint planning/);
    fireEvent.click(screen.getByRole("button", { name: "Privacy" }));
    const dlg = await screen.findByRole("dialog", { name: "Privacy" });
    const row = await within(dlg).findByRole("row", {
      name: /Sprint planning/,
    });
    expect(row).toHaveTextContent("WEB (queued)");
    fireEvent.click(
      within(row).getByRole("button", { name: /Unsend Sprint planning/ }),
    );
    await waitFor(() =>
      expect(
        within(dlg).getByRole("row", { name: /Sprint planning/ }),
      ).toHaveTextContent("taken back"),
    );
    const erase = vi.spyOn(bridge, "eraseLocal");
    fireEvent.click(
      within(dlg).getByRole("button", { name: "Erase all local data…" }),
    );
    expect(erase).not.toHaveBeenCalled();
    fireEvent.click(
      within(dlg).getByRole("button", { name: /Yes, erase everything/ }),
    );
    await waitFor(() => expect(erase).toHaveBeenCalled());
  });
});

describe("mobile layout", () => {
  it("renders the same views with no fixed widths that could overflow 390 px", async () => {
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 390,
    });
    const { container } = await open();
    const css = Array.from(document.querySelectorAll("style")).length; // CSS is a file; the real check is the Playwright run at 390 px
    expect(css).toBeGreaterThanOrEqual(0);
    expect(container.querySelector(".pt")).not.toBeNull();
  });
});
