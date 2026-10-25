import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { ErrorBoundary } from "./ErrorBoundary";

function Bomb({ boom }: { boom: boolean }) {
  if (boom) throw new Error("kaboom");
  return <p>fine</p>;
}

test("a render error shows the boundary instead of a blank page, and Try again recovers", () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  let boom = true;
  const { rerender } = render(
    <ErrorBoundary label="Views">
      <Bomb boom={boom} />
    </ErrorBoundary>,
  );
  expect(screen.getByRole("alert")).toHaveTextContent("Something went wrong in Views");
  expect(screen.getByText("kaboom")).toBeInTheDocument();
  boom = false;
  rerender(
    <ErrorBoundary label="Views">
      <Bomb boom={boom} />
    </ErrorBoundary>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(screen.getByText("fine")).toBeInTheDocument();
});

test("navigating (a new resetKey) clears the error by itself", () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  const { rerender } = render(
    <ErrorBoundary resetKey="/a">
      <Bomb boom />
    </ErrorBoundary>,
  );
  expect(screen.getByRole("alert")).toBeInTheDocument();
  rerender(
    <ErrorBoundary resetKey="/b">
      <Bomb boom={false} />
    </ErrorBoundary>,
  );
  expect(screen.getByText("fine")).toBeInTheDocument();
});
