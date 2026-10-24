import { createRoot } from "react-dom/client";
import { PaletteInput } from "./palette/PaletteInput";
import { sidecarBridge } from "./palette/sidecar";

createRoot(document.getElementById("root")!).render(
  <PaletteInput
    bridge={sidecarBridge}
    onSent={() => undefined}
    onManual={() => undefined}
    onDiscard={() => undefined}
  />,
);
