import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import axe from "axe-core";
import { describe, expect, it, vi } from "vitest";
import { localBridge } from "./localBridge";
import { MobileShell } from "./MobileShell";

class MemoryStorage implements Storage {
  m = new Map<string, string>();
  get length() {
    return this.m.size;
  }
  clear() {
    this.m.clear();
  }
  getItem(k: string) {
    return this.m.get(k) ?? null;
  }
  key(i: number) {
    return [...this.m.keys()][i] ?? null;
  }
  removeItem(k: string) {
    this.m.delete(k);
  }
  setItem(k: string, v: string) {
    this.m.set(k, v);
  }
}

function open(fetchFn?: typeof fetch) {
  const now = () => new Date(2026, 9, 23, 15, 0, 0);
  const bridge = localBridge({ storage: new MemoryStorage(), now, fetchFn });
  bridge.saveSettings({
    handle: "@mahmoud",
    workspace: "acme",
    token: "t",
    projects: ["web"],
  });
  void bridge.firstRun({
    work: "ask",
    meeting: "ask",
    development: false,
    assist: false,
  });
  render(<MobileShell bridge={bridge} pollMs={0} deliverEveryMs={0} />);
  return bridge;
}

describe("Mobile Lite shell", () => {
  it("shows the planner views around a status bar with online state and the queue", async () => {
    open();
    await screen.findByRole("heading", { name: /Fri 23 October 2026/ });
    const bar = screen.getByRole("region", { name: "Phone status" });
    expect(within(bar).getByText("Online")).toBeInTheDocument();
    expect(within(bar).getByText("Nothing waiting")).toBeInTheDocument();
  });

  it("adding and finishing an item offers it in Ready to send; sending queues it and the count follows", async () => {
    const bridge = open();
    await screen.findByRole("heading", { name: /Fri 23 October 2026/ });
    const line = screen.getByLabelText("Add an item, one line");
    fireEvent.change(line, { target: { value: "work: fix login 30m p:web" } });
    fireEvent.keyDown(line, { key: "Enter" });
    fireEvent.submit(line.closest("form")!);
    fireEvent.click(
      await screen.findByRole("button", { name: /^Done fix login/ }),
    );
    const tray = await screen.findByRole("region", { name: /Ready to send/ });
    fireEvent.click(await within(tray).findByRole("button", { name: /^Send/ }));
    await waitFor(() => expect(bridge.pending()).toBe(1));
    expect(await screen.findByText("1 waiting to send")).toBeInTheDocument();
  });

  it("goes offline and online with the browser; coming back delivers the queue", async () => {
    const fetchFn = vi.fn(
      async () => new Response("{}", { status: 207 }),
    ) as unknown as typeof fetch;
    const bridge = open(fetchFn);
    await screen.findByRole("heading", { name: /Fri 23 October 2026/ });
    const it = (await bridge.add("work: a 30m p:web")).items[0];
    await bridge.complete(it.id);
    await bridge.send({ id: it.id });
    act(() => void window.dispatchEvent(new Event("offline")));
    expect(await screen.findByText(/Offline/)).toBeInTheDocument();
    expect(await screen.findByText("1 waiting to send")).toBeInTheDocument();
    act(() => void window.dispatchEvent(new Event("online")));
    await screen.findByText("Online");
    await waitFor(() => expect(bridge.pending()).toBe(0));
    expect(fetchFn).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("Nothing waiting")).toBeInTheDocument();
  });

  it("Send now explains itself when nothing is set up or nothing waits", async () => {
    const bridge = localBridge({ storage: new MemoryStorage() });
    render(<MobileShell bridge={bridge} pollMs={0} deliverEveryMs={0} />);
    fireEvent.click(await screen.findByRole("button", { name: "Send now" }));
    expect(await screen.findByText("Nothing is waiting.")).toBeInTheDocument();
  });

  it("phone settings save the handle, workspace, token and project chips, and warn that storage is not encrypted", async () => {
    const bridge = open();
    await screen.findByRole("heading", { name: /Fri 23 October 2026/ });
    fireEvent.click(screen.getByRole("button", { name: "Phone settings" }));
    const dlg = await screen.findByRole("dialog", { name: "Phone settings" });
    expect(within(dlg).getByText(/not encrypted/)).toBeInTheDocument();
    fireEvent.change(within(dlg).getByLabelText("Your handle"), {
      target: { value: "ali" },
    });
    fireEvent.change(within(dlg).getByLabelText("Your projects"), {
      target: { value: "web, data ops" },
    });
    fireEvent.click(within(dlg).getByRole("button", { name: "Save" }));
    expect(bridge.settings()).toMatchObject({
      handle: "@ali",
      projects: ["WEB", "DATA", "OPS"],
    });
  });

  it("has no accessibility violations (status bar and settings dialog)", async () => {
    open();
    await screen.findByRole("heading", { name: /Fri 23 October 2026/ });
    fireEvent.click(screen.getByRole("button", { name: "Phone settings" }));
    await screen.findByRole("dialog", { name: "Phone settings" });
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
