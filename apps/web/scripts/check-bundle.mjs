// Phase 13 R5.2: the entry chunk stays under 250 kB gzip and CodeMirror lives only in the policy chunk.
import { readdirSync, readFileSync } from "node:fs";
import { gzipSync } from "node:zlib";

const dir = new URL("../dist/assets/", import.meta.url).pathname;
const files = readdirSync(dir).filter((f) => f.endsWith(".js"));
const size = (f) => gzipSync(readFileSync(dir + f)).length / 1024;
const entry = files.find((f) => f.startsWith("index-"));
const kb = size(entry);
console.log(`entry chunk ${entry}: ${kb.toFixed(1)} kB gzip (budget 250)`);
let bad = 0;
if (kb > 250) (console.error("entry chunk over budget"), bad++);
for (const f of files.filter((f) => f !== entry && !f.startsWith("Policies-"))) {
  if (/@codemirror|EditorView/.test(readFileSync(dir + f, "utf8"))) (console.error(`CodeMirror leaked into ${f}`), bad++);
}
if (/@codemirror|EditorView/.test(readFileSync(dir + entry, "utf8"))) (console.error("CodeMirror in the entry chunk"), bad++);
process.exit(bad ? 1 : 0);
