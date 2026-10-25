import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { ConflictDialog } from "./ConflictDialog";
import { Shell } from "./Shell";
import { ConfidenceCue, Pill, ProvenanceChip } from "./ui";
import { ReviewQueue } from "../pages/ReviewQueue";
import { ToastProvider } from "./Toasts";

afterEach(() => vi.restoreAllMocks());

test("conflict dialog offers keep mine / take theirs / revert and shows both values", () => {
  const calls: string[] = [];
  render(
    <ConflictDialog
      field="priority"
      mine="low"
      theirs="high"
      changedBy="@sara"
      onKeepMine={() => calls.push("mine")}
      onTakeTheirs={() => calls.push("theirs")}
      onRevert={() => calls.push("revert")}
    />,
  );
  expect(screen.getByRole("alertdialog")).toHaveTextContent("Changed by @sara");
  expect(screen.getByTestId("theirs")).toHaveTextContent("high");
  expect(screen.getByTestId("mine")).toHaveTextContent("low");
  fireEvent.click(screen.getByText("Keep mine"));
  fireEvent.click(screen.getByText("Take theirs"));
  fireEvent.click(screen.getByText("Revert"));
  expect(calls).toEqual(["mine", "theirs", "revert"]);
});

test("pill, cue and provenance chip render their semantics", () => {
  render(
    <>
      <Pill severity="critical">late</Pill>
      <ConfidenceCue confidence={0.92} />
      <ProvenanceChip
        source="inherited"
        setBy="@sara"
        setAt={new Date(Date.now() - 2 * 86_400_000).toISOString()}
        inheritedFrom="Product X"
      />
    </>,
  );
  expect(screen.getByText("late")).toHaveAttribute("data-severity", "critical");
  expect(screen.getByLabelText("confidence: 92 %")).toHaveAttribute("data-cue", "percent");
  // UX §3.4: the chip text itself says who, when and where from (no UUID, no bare glyph)
  expect(screen.getByText("set by @sara · 2 d ago · inherited from Product X")).toBeInTheDocument();
  expect(screen.getByText("↳").closest(".prov")).toHaveAttribute(
    "title",
    "set by @sara · 2 d ago · inherited from Product X (inherited)",
  );
});

test("the workspace shell sends a signed-out user to the login page", async () => {
  vi.stubGlobal("fetch", async () =>
    Response.json({ error: { code: "unauthorized", message: "no" } }, { status: 401 }),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <MemoryRouter initialEntries={["/w/acme/review"]}>
          <Routes>
            <Route path="/login" element={<h1>login page</h1>} />
            <Route path="/w/:ws" element={<Shell />}>
              <Route path="review" element={<p>secret</p>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
  expect(await screen.findByText("login page")).toBeInTheDocument();
});

function renderQueue(rows: unknown[]) {
  vi.stubGlobal(
    "EventSource",
    class {
      addEventListener() {}
      close() {}
    },
  );
  const resolved: unknown[] = [];
  vi.stubGlobal("fetch", async (req: Request) => {
    const url = new URL(req.url, "http://x");
    if (url.pathname.endsWith("/review-queue")) return Response.json({ data: rows });
    if (url.pathname.endsWith("/review-queue/resolve")) {
      resolved.push(await req.json());
      return Response.json({ resolved: "ok" });
    }
    return Response.json({ data: [] });
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <MemoryRouter initialEntries={["/w/acme/review"]}>
          <Routes>
            <Route path="/w/:ws/review" element={<ReviewQueue />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
  return resolved;
}

test("review queue: empty state", async () => {
  renderQueue([]);
  expect(await screen.findByText("You're all caught up")).toBeInTheDocument();
});

test("review queue: keyboard j/k/Enter takes the suggestion of the focused row", async () => {
  const rows = [
    {
      ref: "review_item:1",
      kind: "stage_suggestion",
      item_key: "WEB-1",
      item_title: "a",
      suggestion: { stage: "test" },
      confidence: 0.9,
    },
    {
      ref: "review_item:2",
      kind: "stage_suggestion",
      item_key: "WEB-2",
      item_title: "b",
      suggestion: { stage: "build" },
      confidence: 0.8,
    },
  ];
  const resolved = renderQueue(rows);
  await screen.findByText("WEB-2");
  fireEvent.keyDown(window, { key: "j" });
  fireEvent.keyDown(window, { key: "Enter" });
  await vi.waitFor(() =>
    expect(resolved).toEqual([{ ref: "review_item:2", choice: { stage: "build" } }]),
  );
  // j moved real DOM focus onto the row so assistive tech announces it (UX review, a11y table)
  const row = screen.getByRole("group", { name: /WEB-2/ });
  expect(row).toHaveFocus();
});

test("review queue: rows say who and when, the heading counts them, Enter without a suggestion opens the picker", async () => {
  const rows = [
    {
      ref: "review_item:9",
      kind: "unclassified_project",
      item_key: "INBOX-1",
      item_title: "tune the retry",
      reporter: "@ali",
      created_at: new Date(Date.now() - 2 * 3600_000).toISOString(),
      suggestion: { phrase: "the retry" },
    },
  ];
  renderQueue(rows);
  await screen.findByText("INBOX-1");
  expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("(1)");
  expect(screen.getByText(/@ali · 2 h ago/)).toBeInTheDocument();
  fireEvent.keyDown(window, { key: "Enter" });
  expect(screen.getByLabelText("other project")).toHaveFocus();
});

test("review queue: no control is nested inside another interactive role", async () => {
  renderQueue([
    { ref: "review_item:1", kind: "stage_suggestion", item_key: "WEB-1", item_title: "a", suggestion: { stage: "test" } },
  ]);
  await screen.findByText("WEB-1");
  expect(document.querySelector('[role="option"]')).toBeNull();
  expect(document.querySelector('[role="listbox"]')).toBeNull();
});
