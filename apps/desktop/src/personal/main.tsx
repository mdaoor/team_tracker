import { createRoot } from "react-dom/client";
import { PersonalApp } from "./PersonalApp";
import { personalSidecarBridge } from "./sidecar";
import "./personal.css";

createRoot(document.getElementById("root")!).render(
  <PersonalApp bridge={personalSidecarBridge} />,
);
