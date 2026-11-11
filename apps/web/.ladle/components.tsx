import type { GlobalProvider } from "@ladle/react";
import "../src/theme/tokens.css";
import "../src/theme/app.css";

export const Provider: GlobalProvider = ({ children }) => (
  <div style={{ padding: 16 }}>{children}</div>
);
