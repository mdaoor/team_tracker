import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import axe from "axe-core";
import { describe, expect, it, vi } from "vitest";
import { CommandPalette } from "./CommandPalette";
import type {
  Command,
  CommandBridge,
  FormSpec,
  Outcome,
  RoleState,
} from "./types";

const cmd = (op: string, args: Record<string, unknown>): Command => ({
  client_id: `id-${op}`,
  actor: "@me",
  at: "2026-10-23T12:00:00Z",
  op,
  args: { ...args, _meta: { role_mode: "manager" } },
});
const outcome = (over: Partial<Outcome> & { op: string }): Outcome => ({
  client_id: `id-${over.op}`,
  args: {},
  status: "previewed",
  rows: [{ label: "Affects", value: "2 items" }],
  summary: "",
  errors: [],
  destructive: false,
  ...over,
});
const STATE: RoleState = {
  role: "manager",
  modes: [
    { mode: "reporter", label: "Reporter" },
    { mode: "manager", label: "Manager" },
  ],
  chips: [
    { label: "New epic", op: "item.create", text: "new epic " },
    { label: "Merge", op: "item.merge", text: "merge " },
  ],
  ops: [
    {
      op: "team.create",
      group: "teams",
      description: "Create a team",
      destructive: false,
      preview: true,
      first: true,
    },
    {
      op: "item.merge",
      group: "items",
      description: "Merge",
      destructive: true,
      preview: true,
      first: true,
    },
  ],
  review_queue_count: 2,
};
const TEAM_FORM: FormSpec = {
  op: "team.create",
  description: "Create a team (org, virtual or ad hoc)",
  destructive: false,
  preview: true,
  fields: [
    {
      name: "name",
      label: "Name",
      kind: "text",
      required: true,
      multi: false,
      description: "",
      picker: null,
      choices: [],
      strict: false,
      default: null,
      constraints: {},
    },
    {
      name: "kind",
      label: "Kind",
      kind: "enum",
      required: true,
      multi: false,
      description: "",
      picker: null,
      choices: [
        { value: "org", label: "org" },
        { value: "virtual", label: "virtual" },
      ],
      strict: true,
      default: null,
      constraints: {},
    },
    {
      name: "members",
      label: "Members",
      kind: "text",
      required: false,
      multi: true,
      description: "",
      picker: "person",
      choices: [
        { value: "@sara", label: "@sara  Sara" },
        { value: "@ali", label: "@ali  Ali" },
      ],
      strict: false,
      default: null,
      constraints: {},
    },
    {
      name: "dry",
      label: "Dry run",
      kind: "boolean",
      required: false,
      multi: false,
      description: "",
      picker: null,
      choices: [],
      strict: false,
      default: null,
      constraints: {},
    },
  ],
};

function bridge(over: Partial<CommandBridge> = {}): CommandBridge {
  return {
    state: vi.fn(async () => STATE),
    setRole: vi.fn(async (mode: string) => ({
      ...STATE,
      role: mode,
      chips: mode === "reporter" ? [] : STATE.chips,
    })),
    propose: vi.fn(async () => ({
      commands: [cmd("item.tag", { keys: ["WEB-1"], add: ["q4"] })],
      source: "parser",
      notes: [],
      unmatched: [],
      incomplete: [],
    })),
    fields: vi.fn(async () => TEAM_FORM),
    build: vi.fn(async () => ({
      command: cmd("team.create", { name: "p", kind: "virtual" }),
    })),
    preview: vi.fn(async (commands: Command[]) => ({
      outcomes: commands.map((c) =>
        outcome({ op: c.op, args: c.args, destructive: c.op === "item.merge" }),
      ),
    })),
    send: vi.fn(async () => ({ status: "sent" as const })),
    queue: vi.fn(async () => ({
      rows: [
        { id: "RV-3", summary: "Tag auth?", default_choice: "accept" },
        { id: "RV-4", summary: "Merge dupes?", default_choice: "accept" },
        { id: "RV-5", summary: "No default" },
      ],
    })),
    resolve: vi.fn(async () => ({ status: "sent" as const })),
    teamNow: vi.fn(async () => ({
      activities: [
        { person: { handle: "@sara" }, item: { key: "WEB-1", title: "Login" } },
      ],
    })),
    ...over,
  };
}

const AXE = {
  runOnly: {
    type: "tag" as const,
    values: ["wcag2a", "wcag2aa", "best-practice"],
  },
  rules: { "color-contrast": { enabled: false }, region: { enabled: false } },
};
async function expectNoViolations(container: HTMLElement) {
  const res = await axe.run(container, AXE);
  expect(res.passes.length).toBeGreaterThan(3); // the audit really ran
  expect(
    res.violations.map(
      (v) => `${v.id}: ${v.nodes.map((n) => n.html).join(" | ")}`,
    ),
  ).toEqual([]);
}

async function ready(b: CommandBridge) {
  const utils = render(<CommandPalette bridge={b} />);
  await screen.findByLabelText("Tell me what to do");
  return utils;
}
const say = (text: string) => {
  const input = screen.getByLabelText("Tell me what to do");
  fireEvent.change(input, { target: { value: text } });
  fireEvent.keyDown(input, { key: "Enter" });
};

describe("role switch and verb chips", () => {
  it("a reporter without other roles sees no management UI at all", async () => {
    const b = bridge({
      state: vi.fn(async () => ({
        ...STATE,
        role: "reporter",
        modes: [{ mode: "reporter", label: "Reporter" }],
        chips: [],
      })),
    });
    const { container } = render(<CommandPalette bridge={b} />);
    await waitFor(() => expect(b.state).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("lists only the allowed modes, switches through the bridge and swaps the chips", async () => {
    const b = bridge();
    await ready(b);
    const select = screen.getByLabelText("Role mode");
    expect(
      within(select)
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual(["Reporter", "Manager"]);
    expect(
      screen.getByRole("button", { name: "New epic" }),
    ).toBeInTheDocument();
    fireEvent.change(select, { target: { value: "reporter" } });
    await waitFor(() => expect(b.setRole).toHaveBeenCalledWith("reporter"));
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "New epic" })).toBeNull(),
    );
  });

  it("a chip prefills the input and sends nothing", async () => {
    const b = bridge();
    await ready(b);
    fireEvent.click(screen.getByRole("button", { name: "Merge" }));
    expect(screen.getByLabelText("Tell me what to do")).toHaveValue("merge ");
    expect(b.send).not.toHaveBeenCalled();
  });
});

describe("command card", () => {
  it("shows the Effect preview, then Confirm sends once (ordinary command)", async () => {
    const b = bridge();
    await ready(b);
    say("tag WEB-1 #q4");
    const card = await screen.findByRole("article", {
      name: "Command item.tag",
    });
    const preview = within(card).getByRole("region", {
      name: "Effect preview",
    });
    expect(within(preview).getByText("2 items")).toBeInTheDocument();
    expect(b.send).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() =>
      expect(b.send).toHaveBeenCalledWith(expect.any(Array), {
        confirmed: true,
        second_confirmed: false,
      }),
    );
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Sent item.tag",
    );
  });

  it("a destructive command needs a second, explicit confirmation", async () => {
    const b = bridge({
      propose: vi.fn(async () => ({
        commands: [
          cmd("item.merge", {
            sources: ["WEB-101", "WEB-105"],
            target: "WEB-101",
          }),
        ],
        source: "parser",
        notes: [],
        unmatched: [],
        incomplete: [],
      })),
    });
    await ready(b);
    say("merge WEB-101 and WEB-105");
    expect(await screen.findByText(/destructive/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(b.send).not.toHaveBeenCalled(); // the first click only arms it
    const yes = await screen.findByRole("button", { name: "Yes, do it" });
    await waitFor(() => expect(yes).toHaveFocus());
    expect(screen.getByRole("alert")).toHaveTextContent("cannot be undone");
    fireEvent.click(yes);
    await waitFor(() =>
      expect(b.send).toHaveBeenCalledWith(expect.any(Array), {
        confirmed: true,
        second_confirmed: true,
      }),
    );
  });

  it("Cancel on the second question sends nothing", async () => {
    const b = bridge({
      propose: vi.fn(async () => ({
        commands: [cmd("item.merge", {})],
        source: "parser",
        notes: [],
        unmatched: [],
        incomplete: [],
      })),
    });
    await ready(b);
    say("merge A-1 and A-2");
    fireEvent.click(await screen.findByRole("button", { name: "Confirm" }));
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(b.send).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Confirm" })).toBeInTheDocument();
  });

  it("a command the server would reject has no Confirm button and says why", async () => {
    const b = bridge({
      preview: vi.fn(async (commands: Command[]) => ({
        outcomes: commands.map((c) =>
          outcome({
            op: c.op,
            status: "rejected",
            rows: [],
            errors: [{ message: "no permission item.write_overlay" }],
          }),
        ),
      })),
    });
    await ready(b);
    say("tag WEB-1 #q4");
    expect(
      await screen.findByText(/no permission item.write_overlay/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
    expect(screen.getAllByRole("alert").length).toBeGreaterThan(0);
  });

  it("a server rejection after Confirm is reported", async () => {
    const b = bridge({
      send: vi.fn(async () => ({
        status: "rejected" as const,
        rejected: [
          { client_id: "x", errors: [{ message: "stage is closed" }] },
        ],
      })),
    });
    await ready(b);
    say("tag WEB-1 #q4");
    fireEvent.click(await screen.findByRole("button", { name: "Confirm" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Rejected: stage is closed",
    );
  });

  it("Esc discards the proposal", async () => {
    const b = bridge();
    await ready(b);
    say("tag WEB-1 #q4");
    await screen.findByRole("article");
    fireEvent.keyDown(screen.getByLabelText("Tell me what to do"), {
      key: "Escape",
    });
    expect(screen.queryByRole("article")).toBeNull();
    expect(screen.getByLabelText("Tell me what to do")).toHaveValue("");
  });
});

describe("forms generated from the catalogue", () => {
  it("renders required, enum, picker and boolean fields and builds through the bridge", async () => {
    const b = bridge();
    await ready(b);
    fireEvent.change(screen.getByLabelText("Or fill a form"), {
      target: { value: "team.create" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Open form" }));
    const form = await screen.findByRole("form", { name: "Form team.create" });
    expect(within(form).getByLabelText("Name (required)")).toBeRequired();
    const kind = within(form).getByLabelText(
      "Kind (required)",
    ) as HTMLSelectElement;
    expect(Array.from(kind.options).map((o) => o.value)).toEqual([
      "",
      "org",
      "virtual",
    ]);
    expect(within(form).getByLabelText("Members")).toHaveAttribute("list");
    expect(form.querySelectorAll("datalist option")).toHaveLength(2); // pack-backed picker suggestions
    expect(within(form).getByLabelText("Dry run")).toHaveAttribute(
      "type",
      "checkbox",
    );
    fireEvent.change(within(form).getByLabelText("Name (required)"), {
      target: { value: "payments" },
    });
    fireEvent.change(kind, { target: { value: "virtual" } });
    fireEvent.click(within(form).getByRole("button", { name: "Preview" }));
    await waitFor(() =>
      expect(b.build).toHaveBeenCalledWith("team.create", {
        name: "payments",
        kind: "virtual",
      }),
    );
    expect(
      await screen.findByRole("article", { name: "Command team.create" }),
    ).toBeInTheDocument();
  });

  it("shows the server-side validation per field, accessibly", async () => {
    const b = bridge({
      build: vi.fn(async () => ({
        errors: [
          { pointer: "/kind", message: "'kind' is a required property" },
        ],
      })),
    });
    await ready(b);
    fireEvent.change(screen.getByLabelText("Or fill a form"), {
      target: { value: "team.create" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Open form" }));
    const form = await screen.findByRole("form");
    fireEvent.submit(form);
    const kind = await within(form).findByLabelText("Kind (required)");
    expect(kind).toHaveAttribute("aria-invalid", "true");
    expect(within(form).getByRole("alert")).toHaveTextContent(
      "required property",
    );
    expect(kind.getAttribute("aria-describedby")).toBe(
      within(form).getByRole("alert").id,
    );
  });

  it("a recognised but incomplete phrase opens the form prefilled", async () => {
    const b = bridge({
      propose: vi.fn(async () => ({
        commands: [],
        source: "none",
        notes: [],
        unmatched: [],
        incomplete: [
          {
            op: "team.create",
            args: { name: "payments" },
            issues: ["/kind"],
            form: TEAM_FORM,
          },
        ],
      })),
    });
    await ready(b);
    say("create team payments");
    const form = await screen.findByRole("form", { name: "Form team.create" });
    expect(within(form).getByLabelText("Name (required)")).toHaveValue(
      "payments",
    );
  });
});

describe("My queue and Team now", () => {
  it("resolves rows with their defaults, one or all", async () => {
    const b = bridge();
    await ready(b);
    fireEvent.click(screen.getByRole("button", { name: "My queue" }));
    const panel = await screen.findByRole("region", { name: "My queue" });
    expect(within(panel).getByText(/no default/)).toBeInTheDocument();
    fireEvent.click(
      within(panel).getByRole("button", { name: "Accept accept for RV-3" }),
    );
    await waitFor(() =>
      expect(b.resolve).toHaveBeenCalledWith([
        { id: "RV-3", choice: "accept" },
      ]),
    );
    fireEvent.click(
      within(panel).getByRole("button", { name: "Accept all 2 defaults" }),
    );
    await waitFor(() =>
      expect(b.resolve).toHaveBeenLastCalledWith([
        { id: "RV-3", choice: "accept" },
        { id: "RV-4", choice: "accept" },
      ]),
    );
  });

  it("shows what my reports are doing", async () => {
    const b = bridge();
    await ready(b);
    fireEvent.click(screen.getByRole("button", { name: "Team now" }));
    const panel = await screen.findByRole("region", { name: "Team now" });
    expect(panel).toHaveTextContent("@sara · WEB-1 Login");
  });
});

describe("accessibility", () => {
  it("is axe-clean: header, card, destructive confirmation, form and panels", async () => {
    const b = bridge({
      propose: vi.fn(async () => ({
        commands: [cmd("item.merge", { sources: ["WEB-101", "WEB-105"] })],
        source: "parser",
        notes: ["parser"],
        unmatched: [],
        incomplete: [],
      })),
    });
    const { container } = await ready(b);
    await expectNoViolations(container);
    say("merge WEB-101 and WEB-105");
    fireEvent.click(await screen.findByRole("button", { name: "Confirm" }));
    await screen.findByRole("button", { name: "Yes, do it" });
    await expectNoViolations(container);
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    fireEvent.change(screen.getByLabelText("Or fill a form"), {
      target: { value: "team.create" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Open form" }));
    await screen.findByRole("form");
    await expectNoViolations(container);
    fireEvent.click(screen.getByRole("button", { name: "My queue" }));
    await screen.findByRole("region", { name: "My queue" });
    fireEvent.click(screen.getByRole("button", { name: "Team now" }));
    await screen.findByRole("region", { name: "Team now" });
    await expectNoViolations(container);
  });

  it("is operable from the keyboard: everything interactive is a native control in tab order", async () => {
    const b = bridge();
    const { container } = await ready(b);
    say("tag WEB-1 #q4");
    await screen.findByRole("article");
    const interactive = container.querySelectorAll(
      "button, input, select, textarea",
    );
    expect(interactive.length).toBeGreaterThan(5);
    interactive.forEach((el) =>
      expect(el.getAttribute("tabindex")).not.toBe("-1"),
    );
    expect(
      container.querySelectorAll("[onclick], div[role=button]"),
    ).toHaveLength(0);
  });
});
