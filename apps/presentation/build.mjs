// Bundle src/ into ONE self-contained HTML file (no external requests): dist/presentation.html
import { build } from "esbuild";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";

const js = await build({
  entryPoints: ["src/main.js"],
  bundle: true,
  minify: true,
  format: "iife",
  write: false,
  target: "es2020",
});
const css = await build({
  entryPoints: ["src/styles.css"],
  bundle: true,
  minify: true,
  write: false,
  loader: { ".css": "css" },
});
const code = js.outputFiles[0].text.replaceAll("</script", "<\\/script");
const html = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Team Tracker — Presentation</title>
<style>${css.outputFiles[0].text}</style>
</head>
<body>
<div id="app"></div>
<script>${code}</script>
</body>
</html>
`;
mkdirSync("dist", { recursive: true });
writeFileSync("dist/presentation.html", html);
console.log(
  `dist/presentation.html ${(Buffer.byteLength(html) / 1024).toFixed(1)} KB`,
);
