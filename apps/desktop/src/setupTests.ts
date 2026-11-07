import "@testing-library/jest-dom/vitest";

// axe probes <canvas> for colour checks; jsdom has no canvas and logs "not implemented" noise. Contrast is audited in a real browser (e2e).
HTMLCanvasElement.prototype.getContext = (() =>
  null) as unknown as HTMLCanvasElement["getContext"];
