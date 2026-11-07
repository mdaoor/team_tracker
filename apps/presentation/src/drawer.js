import { esc } from "./util.js";

/** The item drawer from the prototype, reachable from any slide (keyboard: Enter on a row, Esc closes). */
export function itemDrawerHtml(D, key) {
  const i = D.items.find((x) => x.key === key);
  if (!i) return null;
  const v = D.violations.filter((x) => x.target === key);
  const rs = D.risks.filter((r) => i.risks.includes(r.key));
  const epic = D.epics.find((e) => e.key === i.epic);
  return `<h3><span class="mono">${esc(i.key)}</span> ${esc(i.title)}</h3>
  <div class="meta"><span>${esc(i.project)} › ${epic ? esc(epic.title) : "—"}</span><span>${esc(i.type ?? "")}</span><span class="pill">${esc(i.priority)}</span><span>w ${i.weight}</span></div>
  <div class="meta"><span>owner <b>${esc(i.assignee ?? "—")}</b></span><span>team <b>${esc(i.team ?? "—")}</b></span><span>product <b>${esc(i.product ?? "—")}</b></span></div>
  <div class="meta"><span>stage <b>${esc(i.stage ?? "—")}</b> ${i.stage_age_days ?? 0}d</span><span>state <b>${esc(i.state)}</b></span></div>
  <div class="meta"><span>planned ${esc(i.planned_start ?? "—")} → ${esc(i.planned_end ?? "—")}</span>${i.actual_end ? `<span>actual end ${esc(i.actual_end)}</span>` : ""}</div>
  <div class="meta"><span>estimate <b>${i.estimate_h ?? "—"}</b> h</span><span>logged <b>${i.hours_logged}</b> h</span><span>evidence <b>${i.evidence_count}</b></span><span>touched ${esc(i.last_touched ?? "—")}</span></div>
  <div class="meta">${i.tags.map((t) => `<span class="pill">#${esc(t)}</span>`).join(" ")}</div>
  <h4>Risks</h4>${rs.map((r) => `<div class="hab"><span class="mono">${esc(r.key)}</span><span>${esc(r.title)}</span><span class="pill ${r.p * r.i >= 15 ? "crit" : "serious"}">${r.p}×${r.i}=${r.p * r.i} · ${esc(r.status)}</span></div>`).join("") || '<div style="color:var(--text-3)">none</div>'}
  <h4>Violations</h4>${v.map((x) => `<div class="hab"><span>${esc(x.policy.replace(/_/g, " "))}</span><span style="color:var(--text-2)">${esc(x.detail)}</span></div>`).join("") || '<div style="color:var(--text-3)">none</div>'}
  <h4>Dimensions</h4><div class="meta"><span>project=${esc(i.project)}</span><span>product=${esc(i.product ?? "—")}</span><span>stage=${esc(i.stage ?? "—")}</span><span>team=${esc(i.team ?? "—")}</span><span>priority=${esc(i.priority)}</span><span>epic=${esc(i.epic ?? "—")}</span></div>`;
}

export function mountDrawer(root, D) {
  const el = document.createElement("aside");
  el.className = "drawer";
  el.setAttribute("role", "dialog");
  el.setAttribute("aria-label", "Item details");
  el.innerHTML = `<button class="close ghost" aria-label="Close">✕</button><div class="drawer-body"></div>`;
  root.appendChild(el);
  const close = () => el.classList.remove("open");
  el.querySelector(".close").onclick = close;
  return {
    open(key) {
      const h = itemDrawerHtml(D, key);
      if (!h) return false;
      el.querySelector(".drawer-body").innerHTML = h;
      el.classList.add("open");
      el.querySelector(".close").focus();
      return true;
    },
    close,
    isOpen: () => el.classList.contains("open"),
  };
}
