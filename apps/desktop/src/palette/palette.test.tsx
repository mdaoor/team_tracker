import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ManualForm } from "./ManualForm";
import { missingFields, parseMinutes, requiredFor } from "./manual";
import { OutboxList } from "./OutboxList";
import { DEBOUNCE_MS, PaletteInput } from "./PaletteInput";
import * as tray from "./timerTray";
import type { Bridge, Pack, Parsed } from "./types";

const PACK: Pack = {
  projects: [
    { key: "WEB", name: "Website", jira: "WEBJ" },
    { key: "DATA", name: "Ingestion" },
  ],
  people: [{ handle: "@sara", name: "Sara" }],
  tags: ["auth", "bug"],
  my_open_items: [{ key: "WEB-142", title: "fix login", project: "WEB" }],
  manual_mode: {
    required_fields: ["action", "item", "project"],
    per_project: { DATA: ["action", "project", "minutes"] },
  },
};
const parsed = (source: Parsed["source"], value = "fix login"): Parsed => ({
  cards: [
    {
      action: "log_time",
      rows: [
        { label: "Task", value, cue: source === "llm" ? "✓ matched" : "" },
      ],
    },
  ],
  source,
  batch: { workspace: "acme", messages: [{ client_id: "c1" }] },
  notes: [],
});
function bridge(over: Partial<Bridge> = {}): Bridge {
  return {
    parse: vi.fn(async (_t, o) => parsed(o.llm ? "llm" : "structured")),
    pack: vi.fn(async () => PACK),
    send: vi.fn(async () => ({ status: "sent" as const, results: [] })),
    buildManual: vi.fn(async (a) => ({ messages: [a] })),
    outbox: vi.fn(async () => []),
    resend: vi.fn(async () => ({ status: "sent" as const, results: [] })),
    discard: vi.fn(async () => undefined),
    openInEditor: vi.fn(async (b) => ({ ...b, edited: true })),
    active: vi.fn(async () => null),
    ...over,
  };
}
const type = (text: string) =>
  fireEvent.change(screen.getByLabelText("What did you do?"), {
    target: { value: text },
  });

describe("palette input", () => {
  it("debounces 300 ms, shows the structured rows at once and the model's when they arrive", async () => {
    vi.useFakeTimers();
    let release: (p: Parsed) => void = () => undefined;
    const b = bridge({
      parse: vi.fn((_t, o) =>
        o.llm
          ? new Promise<Parsed>((r) => (release = r))
          : Promise.resolve(parsed("structured")),
      ),
    });
    render(
      <PaletteInput
        bridge={b}
        onSent={vi.fn()}
        onManual={vi.fn()}
        onDiscard={vi.fn()}
      />,
    );
    type("fixed it");
    expect(b.parse).not.toHaveBeenCalled();
    await act(async () => void vi.advanceTimersByTime(DEBOUNCE_MS));
    expect(screen.getByText("fix login")).toBeInTheDocument();
    expect(screen.getByText(/checking with the model/)).toBeInTheDocument();
    await act(async () => release(parsed("llm", "fix login (model)")));
    expect(screen.getByText("fix login (model)")).toBeInTheDocument();
    expect(screen.queryByText(/checking with the model/)).toBeNull();
    vi.useRealTimers();
  });

  it("a stale answer never overwrites a newer line", async () => {
    vi.useFakeTimers();
    const resolvers: ((p: Parsed) => void)[] = [];
    const b = bridge({
      parse: vi.fn((_t, o) =>
        o.llm
          ? new Promise<Parsed>((r) => resolvers.push(r))
          : Promise.resolve(parsed("structured", "x")),
      ),
    });
    render(
      <PaletteInput
        bridge={b}
        onSent={vi.fn()}
        onManual={vi.fn()}
        onDiscard={vi.fn()}
      />,
    );
    type("one");
    await act(async () => void vi.advanceTimersByTime(DEBOUNCE_MS));
    type("one two");
    await act(async () => void vi.advanceTimersByTime(DEBOUNCE_MS));
    await act(async () => resolvers[0](parsed("llm", "STALE")));
    expect(screen.queryByText("STALE")).toBeNull();
    vi.useRealTimers();
  });

  it("Enter sends without waiting for the model; Esc discards", async () => {
    const onSent = vi.fn();
    const onDiscard = vi.fn();
    const b = bridge({
      parse: vi.fn(() => new Promise<Parsed>(() => undefined)),
    }); // the model never answers
    b.parse = vi.fn((_t, o) =>
      o.llm
        ? new Promise<Parsed>(() => undefined)
        : Promise.resolve(parsed("structured")),
    );
    render(
      <PaletteInput
        bridge={b}
        onSent={onSent}
        onManual={vi.fn()}
        onDiscard={onDiscard}
      />,
    );
    type("fixed it");
    fireEvent.keyDown(screen.getByLabelText("What did you do?"), {
      key: "Enter",
    });
    await waitFor(() => expect(onSent).toHaveBeenCalled());
    expect(b.send).toHaveBeenCalledWith(
      expect.objectContaining({ workspace: "acme" }),
      { jira: false },
    );
    fireEvent.keyDown(screen.getByLabelText("What did you do?"), {
      key: "Escape",
    });
    expect(onDiscard).toHaveBeenCalled();
  });

  it("⌘J toggles the Jira flag only when the project has Jira; ⌘M opens the manual form", async () => {
    const onManual = vi.fn();
    const b = bridge();
    const { rerender } = render(
      <PaletteInput
        bridge={b}
        jiraAvailable={false}
        onSent={vi.fn()}
        onManual={onManual}
        onDiscard={vi.fn()}
      />,
    );
    const box = screen.getByLabelText("What did you do?");
    type("x");
    fireEvent.keyDown(box, { key: "j", metaKey: true });
    expect(screen.queryByLabelText("Jira chip")).toBeNull();
    fireEvent.keyDown(box, { key: "m", metaKey: true });
    expect(onManual).toHaveBeenCalledWith("x");
    rerender(
      <PaletteInput
        bridge={b}
        jiraAvailable
        onSent={vi.fn()}
        onManual={onManual}
        onDiscard={vi.fn()}
      />,
    );
    fireEvent.keyDown(box, { key: "j", metaKey: true });
    expect(screen.getByLabelText("Jira chip")).toHaveTextContent("Jira on");
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() =>
      expect(b.send).toHaveBeenCalledWith(expect.anything(), { jira: true }),
    );
  });

  it("shows ghost examples on first run and ⌘E re-validates an edited file", async () => {
    const b = bridge();
    render(
      <PaletteInput
        bridge={b}
        onSent={vi.fn()}
        onManual={vi.fn()}
        onDiscard={vi.fn()}
      />,
    );
    expect(screen.getByLabelText("Examples").children).toHaveLength(3);
    type("fixed it");
    await waitFor(
      () => expect(screen.getAllByText("fix login").length).toBeGreaterThan(0),
      { timeout: 2000 },
    );
    fireEvent.keyDown(screen.getByLabelText("What did you do?"), {
      key: "e",
      ctrlKey: true,
    });
    await waitFor(() => expect(b.openInEditor).toHaveBeenCalled());
  });
});

describe("manual form", () => {
  it("refuses to send with a missing mandatory field and names it; works with no bridge at all", () => {
    const onSubmit = vi.fn();
    render(
      <ManualForm
        pack={PACK}
        onSubmit={onSubmit}
        onSaveDraft={vi.fn()}
        onOpenJson={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText("Send"));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getAllByRole("alert").map((e) => e.textContent)).toEqual([
      "project is required",
      "item is required",
    ]);
    fireEvent.change(screen.getByLabelText(/Project/), {
      target: { value: "WEB" },
    });
    fireEvent.change(screen.getByLabelText(/Task/), {
      target: { value: "WEB-142" },
    });
    fireEvent.click(screen.getByText("Send"));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        project: "WEB",
        item: "WEB-142",
        action: "note",
      }),
    );
  });

  it("per-project rules apply, presets fill time, and a bad duration blocks", () => {
    const onSubmit = vi.fn();
    render(
      <ManualForm
        pack={PACK}
        initial={{ project: "DATA" }}
        onSubmit={onSubmit}
        onSaveDraft={vi.fn()}
        onOpenJson={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText("Send"));
    expect(screen.getByRole("alert")).toHaveTextContent("minutes is required");
    fireEvent.click(screen.getByText("30m"));
    expect((screen.getByLabelText(/Time/) as HTMLInputElement).value).toBe(
      "30m",
    );
    fireEvent.change(screen.getByLabelText(/Time/), {
      target: { value: "soon" },
    });
    fireEvent.click(screen.getByText("Send"));
    expect(screen.getByRole("alert")).toHaveTextContent("not a duration");
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("offers exactly the pack's projects, tags and tasks; draft and JSON actions pass the answers through", () => {
    const draft = vi.fn();
    const json = vi.fn();
    render(
      <ManualForm
        pack={PACK}
        onSubmit={vi.fn()}
        onSaveDraft={draft}
        onOpenJson={json}
      />,
    );
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual(
      expect.arrayContaining(["WEB", "DATA", "WEB-142 fix login"]),
    );
    expect(
      screen.getAllByRole("checkbox").map((c) => c.parentElement?.textContent),
    ).toEqual(["auth", "bug"]);
    fireEvent.click(screen.getByLabelText("auth"));
    fireEvent.click(screen.getByText("Save draft to outbox"));
    expect(draft).toHaveBeenCalledWith(
      expect.objectContaining({ tags: ["auth"] }),
    );
    fireEvent.click(screen.getByText("Open as JSON"));
    expect(json).toHaveBeenCalled();
  });

  it("required-field rules: log_time needs minutes, create needs a type", () => {
    expect(requiredFor(PACK, { action: "log_time", project: "WEB" })).toEqual([
      "action",
      "item",
      "project",
      "minutes",
    ]);
    expect(
      missingFields(PACK, { action: "create", project: "WEB", item: "x" }),
    ).toEqual(["item_type"]);
  });
});

describe("durations and outbox", () => {
  it("parses the forms people type", () => {
    expect(
      ["45m", "2h", "1h30", "1h 30m", "90", "1.5h", "soon", ""].map(
        parseMinutes,
      ),
    ).toEqual([45, 120, 90, 90, 90, 90, null, null]);
  });

  it("lists pending/failed with actions, sent without, and the empty state", () => {
    const [r, d, e] = [vi.fn(), vi.fn(), vi.fn()];
    const { rerender } = render(
      <OutboxList
        entries={[
          {
            id: "a",
            status: "failed",
            summary: "fixed login",
            error: "offline",
          },
          {
            id: "b",
            status: "sent",
            summary: "wrote tests",
            jira_key: "WEBJ-9",
          },
        ]}
        onResend={r}
        onDiscard={d}
        onEdit={e}
      />,
    );
    expect(screen.getAllByText("Resend")).toHaveLength(1);
    expect(screen.getByText(/Jira WEBJ-9/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("Resend"));
    expect(r).toHaveBeenCalledWith("a");
    rerender(<OutboxList entries={[]} onResend={r} onDiscard={d} onEdit={e} />);
    expect(screen.getByText(/Nothing waiting/)).toBeInTheDocument();
  });
});

describe("tray timer", () => {
  const t0 = Date.parse("2026-10-23T09:00:00Z");
  const min = 60000;
  it("starting another item pauses the first; elapsed stays right across pause and resume", () => {
    let s = tray.start(tray.idle, { key: "WEB-1", title: "a" }, t0);
    expect(tray.format(tray.elapsedMs(s, t0 + 90 * min))).toBe("1h 30m");
    s = tray.pause(s, t0 + 30 * min);
    expect(tray.elapsedMs(s, t0 + 600 * min)).toBe(30 * min); // paused: the clock stops
    s = tray.resume(s, t0 + 60 * min);
    expect(tray.elapsedMs(s, t0 + 75 * min)).toBe(45 * min);
    expect(tray.start(s, { key: "WEB-1", title: "a" }, t0 + 80 * min)).toBe(s); // same item: no reset
    expect(
      tray.start(s, { key: "WEB-2", title: "b" }, t0 + 80 * min).current?.key,
    ).toBe("WEB-2");
    expect(tray.done()).toBe(tray.idle);
  });
  it("the server's active activity wins when online", () => {
    const s = tray.fromServer(
      { key: "WEB-1", title: "a", started_at: new Date(t0).toISOString() },
      t0 + 5 * min,
    );
    expect(tray.format(tray.elapsedMs(s, t0 + 5 * min))).toBe("5m");
    expect(tray.fromServer(null, t0)).toBe(tray.idle);
  });
});
