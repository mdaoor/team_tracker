import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { toFilter, type Chip } from "../../lib/viewState";
import { Card } from "./Card";
import { Views } from "./testable";

afterEach(() => vi.restoreAllMocks());

const chip = (id: string, clause: Chip["clause"], negate = false): Chip => ({
  id,
  label: id,
  clause,
  negate,
});

test("toFilter merges dimension chips, ANDs repeated keys and wraps negations", () => {
  const f = toFilter([
    chip("a", { dims: { team: { in: ["x"] } } }),
    chip("b", { dims: { region: { in: ["eu"] } } }),
    chip("c", { project: { in: ["P"] } }),
    chip("d", { project: { in: ["Q"] } }),
    chip("e", { tags: { any: ["t"] } }, true),
  ]);
  expect(f.dims).toEqual({ team: { in: ["x"] }, region: { in: ["eu"] } });
  expect(f.project).toEqual({ in: ["P"] });
  expect(f.and).toEqual([{ project: { in: ["Q"] } }, { not: { tags: { any: ["t"] } } }]);
});

test("a card shows key, weight, risk and stage age", () => {
  render(
    <MemoryRouter>
      <Card
        item={{
          key: "WEB-1",
          title: "Do it",
          priority: "high",
          weight: 3.5,
          top_risk_score: 12,
          assignee: "@sara",
          stage_age_days: 4,
          expected_duration_days: 5,
          hours_logged: 1.5,
          tags: ["a"],
        }}
      />
    </MemoryRouter>,
  );
  expect(screen.getByText("WEB-1")).toBeInTheDocument();
  expect(screen.getByText(/risk 12/)).toBeInTheDocument();
  expect(screen.getByText("4d/5")).toBeInTheDocument();
  expect(screen.getByText("#a")).toBeInTheDocument();
});

test("clicking a matrix cell drills down and Backspace pops back", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = (input as Request).url;
    const j = (v: unknown) => Response.json(v);
    if (url.endsWith("/query/aggregate"))
      return j({
        measure: "count",
        rows: [{ value: "WEB", count: 3, total: 3 }],
        cols: ["build"],
        cells: [[3]],
        totals: {},
      });
    return j({ data: [] });
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/w/acme/views?layout=matrix"]}>
        <Routes>
          <Route path="/w/:ws/views" element={<Views />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const cell = await waitFor(() => {
    const c = document.querySelector('[data-cell="WEB|build"]') as HTMLElement | null;
    expect(c).not.toBeNull();
    return c!;
  });
  fireEvent.click(cell);
  expect(await screen.findByLabelText("Drill-down")).toHaveTextContent("WEB × build");
  fireEvent.keyDown(window, { key: "Backspace" });
  await waitFor(() => expect(screen.queryByLabelText("Drill-down")).toBeNull());
});
