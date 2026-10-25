import { act, render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { ToastProvider, useToasts } from "./Toasts";

function Fire() {
  const t = useToasts();
  return (
    <>
      <button onClick={() => t.push("Saved", { key: "k" })}>a</button>
      <button onClick={() => t.push("Saved")}>b</button>
      <button onClick={() => t.push("Other")}>c</button>
    </>
  );
}

test("identical or same-key toasts do not stack (StrictMode double fire)", () => {
  render(
    <ToastProvider>
      <Fire />
    </ToastProvider>,
  );
  act(() => {
    screen.getByText("a").click();
    screen.getByText("a").click();
    screen.getByText("b").click();
    screen.getByText("b").click();
    screen.getByText("c").click();
  });
  expect(screen.getAllByText("Saved")).toHaveLength(2); // one keyed, one keyless: distinct keys
  expect(screen.getAllByText("Other")).toHaveLength(1);
});
