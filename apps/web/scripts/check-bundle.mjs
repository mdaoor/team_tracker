// Phase 13 R5.2: the entry chunk stays under 250 kB gzip, every route group is its own chunk,
// and CodeMirror lives only in the policy chunk. Runs after `vite build` (see the build script) and fails it.
import { readdirSync, readFileSync } from "node:fs";
import { gzipSync } from "node:zlib";

const BUDGET_KB = 250;
const GROUPS = ["Setup-", "Views-", "Present-", "Policies-", "Violations-", "Habits-"];
const dir = new URL("../dist/assets/", import.meta.url).pathname;
const files = readdirSync(dir).filter((f) => f.endsWith(".js"));
const text = (f) => readFileSync(dir + f, "utf8");
const kbOf = (f) => gzipSync(readFileSync(dir + f)).length / 1024;
const entry = files.find((f) => f.startsWith("index-"));
let bad = 0;
const fail = (m) => (console.error(`FAIL ${m}`), bad++);

if (!entry) fail("no entry chunk found: run `vite build` first");
else {
  const kb = kbOf(entry);
  console.log(`entry chunk ${entry}: ${kb.toFixed(1)} kB gzip (budget ${BUDGET_KB})`);
  if (kb >= BUDGET_KB) fail(`entry chunk is ${kb.toFixed(1)} kB gzip, budget ${BUDGET_KB}`);
  if (/@codemirror|EditorView/.test(text(entry))) fail("CodeMirror in the entry chunk");
}
for (const g of GROUPS)
  if (!files.some((f) => f.startsWith(g))) fail(`route group chunk ${g}* is missing`);
for (const f of files.filter((f) => f !== entry && !f.startsWith("Policies-")))
  if (/@codemirror|EditorView/.test(text(f))) fail(`CodeMirror leaked into ${f}`);
const total = files.reduce((n, f) => n + kbOf(f), 0);
console.log(`all ${files.length} chunks: ${total.toFixed(1)} kB gzip`);
process.exit(bad ? 1 : 0);
