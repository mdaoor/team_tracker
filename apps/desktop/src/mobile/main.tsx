import { createRoot } from "react-dom/client";
import "../personal/personal.css";
import "./mobile.css";
import { localBridge } from "./localBridge";
import { MobileShell } from "./MobileShell";
import { registerServiceWorker } from "./register";

/** Entry of the installable phone app ("Mobile Lite", ADR-0057): the personal planner views against a browser-local bridge. */
createRoot(document.getElementById("root")!).render(
  <MobileShell bridge={localBridge()} />,
);
registerServiceWorker();
