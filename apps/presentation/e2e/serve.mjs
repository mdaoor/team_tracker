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
createServer((req, res) => {
  const path = new URL(req.url, "http://x").pathname;
  const f = files[path];
  if (!f) return res.writeHead(404).end();
  res.writeHead(200, { "content-type": f[1] }).end(readFileSync(f[0]));
}).listen(4173, () => console.log("deck server on 4173"));
