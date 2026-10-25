import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Suspense, useEffect, useState, type ReactNode } from "react";
import { NavLink, Outlet, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ApiError } from "../api/http";
import { getMe, listMyWorkspaces } from "../api/session";
import type { Workspace } from "../api/types";
import { useList } from "../lib/hooks";
import { t } from "../lib/i18n";
import { NotificationBell } from "../pages/governance/NotificationBell";
import { CommandPalette } from "./CommandPalette";
import { ErrorBoundary } from "./ErrorBoundary";
import { ItemDrawer } from "./ItemDrawer";
import { Chip } from "./ui";

/** Direct URLs to areas the viewer cannot use show an explanation, not an empty page (UX 12). */
export function RequireAccess({ area, children }: { area: string; children: ReactNode }) {
  const { ws = "" } = useParams();
  const me = useQuery({
    queryKey: [ws, "me"],
    queryFn: () => getMe(ws),
    retry: false,
  });
  if (me.isLoading) return <p>Loading…</p>;
  const nav = (me.data?.nav ?? {}) as Record<string, boolean>;
  if (me.data && nav[area] === false)
    return (
      <section className="denied" role="alert" aria-labelledby="denied-h">
        <h1 id="denied-h">You don't have access to this page</h1>
        <p>
          Your roles in this workspace do not include {area}. Ask a workspace admin if you need it;
          everything else in the menu is available to you.
        </p>
      </section>
    );
  return <>{children}</>;
}

export function Shell({ topbar }: { topbar?: ReactNode }) {
  const { ws = "" } = useParams();
  const nav = useNavigate();
  const loc = useLocation();
  const [navOpen, setNavOpen] = useState(false);
  useEffect(() => setNavOpen(false), [loc.pathname]);
  const [sp, setSp] = useSearchParams();
  const qc = useQueryClient();
  const me = useQuery({
    queryKey: [ws, "me"],
    queryFn: () => getMe(ws),
    retry: false,
  });
  const mine = useQuery({
    queryKey: ["workspaces"],
    queryFn: () => listMyWorkspaces(),
    retry: false,
  });
  const scopes = useList("/me/scopes");
  useEffect(() => {
    const e = me.error;
    if (e instanceof ApiError && e.status === 401) nav("/login", { replace: true });
  }, [me.error, nav]);

  const base = `/w/${ws}`;
  const navAccess = (me.data?.nav ?? {}) as Record<string, boolean>;
  const allowed = (area: string) => navAccess[area] !== false;
  const item = sp.get("item");
  const commandsAll = [
    { label: "Setup · Projects & products", to: `${base}/setup/projects` },
    { label: "Setup · Process & stages", to: `${base}/setup/process` },
    { label: "Setup · Dimensions", to: `${base}/setup/dimensions` },
    { label: "Setup · People & teams", to: `${base}/setup/people` },
    { label: "Setup · Identity provider", to: `${base}/setup/identity` },
    { label: "Setup · Milestones & iterations", to: `${base}/setup/planning` },
    { label: "Setup · Risks", to: `${base}/setup/risks` },
    { label: "Setup · Tags & glossary", to: `${base}/setup/glossary` },
    { label: "Setup · Translator rules", to: `${base}/setup/rules` },
    { label: "Setup · Manual mode & context pack", to: `${base}/setup/manual` },
    { label: "Review queue", to: `${base}/review` },
    { label: "Views", to: `${base}/views` },
    { label: "Present", to: `${base}/present` },
    { label: "Project plans", to: `${base}/plan` },
  ];
  const commands = commandsAll.filter((c) => allowed(c.to.split("/")[3] ?? ""));
  return (
    <div className="shell" data-nav={navOpen ? "open" : "closed"}>
      <header className="topbar" role="banner">
        <button
          className="btn menu-btn"
          aria-label="Menu"
          aria-expanded={navOpen}
          aria-controls="main-nav"
          onClick={() => setNavOpen((o) => !o)}
        >
          ☰
        </button>
        <strong className="brand">Team Tracker</strong>
        <select
          aria-label="Workspace"
          value={ws}
          onChange={(e) => {
            qc.clear();
            nav(`/w/${e.target.value}/review`);
          }}
        >
          {((mine.data?.data as Workspace[] | undefined) ?? [{ slug: ws, name: ws }]).map((x) => (
            <option key={x.slug} value={x.slug}>
              {x.name}
            </option>
          ))}
        </select>
        <select
          aria-label="Scope"
          value={sp.get("scope") ?? ""}
          onChange={(e) => {
            const n = new URLSearchParams(sp);
            if (e.target.value) n.set("scope", e.target.value);
            else n.delete("scope");
            setSp(n);
          }}
        >
          <option value="">All I can read</option>
          {(scopes.data ?? []).map((s) => (
            <option key={`${s.type}:${s.id}`} value={`${s.type}:${s.key}`}>
              {s.type} · {s.name}
            </option>
          ))}
        </select>
        {sp.get("scope") && (
          <Chip onRemove={() => setSp((p) => (p.delete("scope"), p))}>{sp.get("scope")}</Chip>
        )}
        <span className="grow" />
        {topbar}
        <NotificationBell />
        <kbd title="Command palette">⌘K</kbd>
        <span aria-label="Signed in as">{me.data?.handle}</span>
      </header>
      <nav className="sidenav" id="main-nav" aria-label="Main" onClick={() => setNavOpen(false)}>
        {allowed("setup") && (
          <NavLink to={`${base}/setup/projects`}>{t("nav.setup")}</NavLink>
        )}
        {allowed("review") && (
          <NavLink to={`${base}/review`}>{t("nav.review")}</NavLink>
        )}
        {allowed("views") && (
          <NavLink to={`${base}/views`}>{t("nav.views")}</NavLink>
        )}
        {allowed("plan") && (
          <NavLink to={`${base}/plan`}>{t("nav.plan")}</NavLink>
        )}
        {allowed("present") && (
          <NavLink to={`${base}/present`}>{t("nav.present")}</NavLink>
        )}
        {allowed("policies") && (
          <NavLink to={`${base}/policies`}>{t("nav.policies")}</NavLink>
        )}
        {allowed("violations") && (
          <NavLink to={`${base}/violations`}>{t("nav.violations")}</NavLink>
        )}
        {allowed("habits") && (
          <NavLink to={`${base}/habits`}>{t("nav.habits")}</NavLink>
        )}
        {allowed("performance") && (
          <NavLink to={`${base}/performance`}>{t("nav.performance")}</NavLink>
        )}
        {allowed("digest") && (
          <NavLink to={`${base}/digest`}>{t("nav.digest")}</NavLink>
        )}
        {allowed("insights") && (
          <NavLink to={`${base}/insights`}>{t("nav.insights")}</NavLink>
        )}
        {allowed("weights") && (
          <NavLink to={`${base}/weights`}>{t("nav.weights")}</NavLink>
        )}
      </nav>
      <main className="content" id="main">
        {me.isError ? (
          <p role="alert">Could not load your session.</p>
        ) : (
          <ErrorBoundary resetKey={loc.pathname}>
            <Suspense fallback={<p aria-busy="true">Loading…</p>}>
              <Outlet />
            </Suspense>
          </ErrorBoundary>
        )}
      </main>
      <CommandPalette commands={commands} />
      {item && (
        <ItemDrawer ws={ws} itemKey={item} onClose={() => setSp((p) => (p.delete("item"), p))} />
      )}
    </div>
  );
}
