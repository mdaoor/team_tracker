import { createRoot } from "react-dom/client";
import { CommandPalette } from "./palette/commands/CommandPalette";
import { PaletteInput } from "./palette/PaletteInput";
import { commandBridge, sidecarBridge } from "./palette/sidecar";

createRoot(document.getElementById("root")!).render(
  <>
    <PaletteInput
      bridge={sidecarBridge}
      onSent={() => undefined}
      onManual={() => undefined}
      onDiscard={() => undefined}
    />
    <CommandPalette bridge={commandBridge} />
  </>,
);
