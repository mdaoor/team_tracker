import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import * as ui from "./ui.stories";

/** Stories are the component catalogue; this keeps them from rotting: every exported story must render. */
describe("stories", () => {
  for (const [name, Story] of Object.entries(ui)) {
    it(`${name} renders`, () => {
      const S = Story as () => JSX.Element;
      const { container } = render(<S />);
      expect(container.firstChild).not.toBeNull();
    });
  }
});
