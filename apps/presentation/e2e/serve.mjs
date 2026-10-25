// Static server for the built deck and the golden fixture: `/` -> dist/presentation.html, `/golden.json` -> fixtures/golden.json
import { createServer } from "node:http";
import { readFileSync } from "node:fs";

const files = {
  "/": ["dist/presentation.html", "text/html"],
  "/golden.json": ["fixtures/golden.json", "application/json"],
  "/prototype.html": [
    "../../docs/spec/team_tracker_presentation.html",
    "text/html",
  ],
};
/** `/many.json?n=9&fit=shrink|paginate`: the golden deck with `n` projects (one item each) and the Projects slide's `fit` set, so the
 *  e2e can check that long content continues ("(cont.)") or shrinks instead of overflowing. */
function many(url) {
  const n = Number(url.searchParams.get("n") ?? 9);
  const fit = url.searchParams.get("fit");
  const d = JSON.parse(readFileSync("fixtures/golden.json", "utf8"));
  d.snapshot.projects = Array.from({ length: n }, (_, i) => ({
    ...d.snapshot.projects[0],
    key: `P${i}`,
    name: `Project ${i}`,
  }));
  d.snapshot.items = d.snapshot.projects.map((p, i) => ({
    ...d.snapshot.items[0],
    key: `${p.key}-1`,
    project: p.key,
    epic: null,
    title: `Item ${i}`,
  }));
  d.snapshot.epics = [];
  d.snapshot.milestones = [];
  d.snapshot.risks = [];
  d.snapshot.violations = [];
  for (const s of d.presentation.slides) if (s.kind === "projects") s.fit = fit;
  return JSON.stringify(d);
}

createServer((req, res) => {
  const url = new URL(req.url, "http://x");
  const path = url.pathname;
  if (path === "/many.json")
    return res
      .writeHead(200, { "content-type": "application/json" })
      .end(many(url));
  const f = files[path];
  if (!f) return res.writeHead(404).end();
  res.writeHead(200, { "content-type": f[1] }).end(readFileSync(f[0]));
}).listen(4173, () => console.log("deck server on 4173"));
