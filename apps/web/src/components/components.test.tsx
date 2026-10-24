import { render, screen, fireEvent, within } from "@testing-library/react";
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
      <ProvenanceChip source="inherited" setBy="rule" inheritedFrom="project WEB" />
    </>,
  );
  expect(screen.getByText("late")).toHaveAttribute("data-severity", "critical");
  expect(screen.getByLabelText("confidence: 92 %")).toHaveAttribute("data-cue", "percent");
  expect(screen.getByText("↳").closest(".prov")).toHaveAttribute(
    "title",
    "inherited · rule · from project WEB",
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
  const list = screen.getByRole("listbox", { name: "Review rows" });
  expect(within(list).getAllByRole("option", { selected: true })).toHaveLength(1);
});
