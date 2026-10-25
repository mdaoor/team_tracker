import { createRoot } from "react-dom/client";
import { demoBridge } from "./demo";
import { PersonalApp } from "./PersonalApp";
import "./personal.css";

/** Dev and e2e only (`pnpm dev`, then /demo.html; add ?firstrun=1 for the privacy explainer): an in-memory bridge with a fixed clock. */
const q = new URLSearchParams(window.location.search);
createRoot(document.getElementById("root")!).render(
  <PersonalApp
    bridge={demoBridge({ firstRun: q.get("firstrun") === "1" })}
    pollMs={0}
  />,
);
