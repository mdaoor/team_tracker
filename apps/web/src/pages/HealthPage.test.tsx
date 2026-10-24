import { render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { HealthPage } from "./HealthPage";

afterEach(() => vi.restoreAllMocks());

test("shows readiness from the API", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockImplementation(async () =>
        Response.json({ status: "ready", checks: { database: "ok", procrastinate: "ok" } }),
      ),
  );
  render(<HealthPage />);
  expect(await screen.findByText("API ready")).toBeInTheDocument();
  expect(screen.getByText("database: ok")).toBeInTheDocument();
});

test("shows an error when the API is down", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("boom")));
  render(<HealthPage />);
  expect(await screen.findByText(/API unreachable/)).toBeInTheDocument();
});
