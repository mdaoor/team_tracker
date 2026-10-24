import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, type ReactNode } from "react";
import { NavLink, Outlet, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, ApiError, w, type Row } from "../api/http";
import { useList } from "../lib/hooks";
import { t } from "../lib/i18n";
import { CommandPalette } from "./CommandPalette";
import { ItemDrawer } from "./ItemDrawer";
import { Chip } from "./ui";

export function Shell({ topbar }: { topbar?: ReactNode }) {
  const { ws = "" } = useParams();
  const nav = useNavigate();
  const [sp, setSp] = useSearchParams();
  const qc = useQueryClient();
  const me = useQuery({
    queryKey: [ws, "me"],
    queryFn: () => api.get<Row>(w(ws, "/me")),
    retry: false,
  });
  const mine = useQuery({
    queryKey: ["workspaces"],
    queryFn: () => api.get<Row>("/api/v1/me/workspaces"),
    retry: false,
  });
  const scopes = useList("/me/scopes");
  useEffect(() => {
    const e = me.error;
    if (e instanceof ApiError && e.status === 401) nav("/login", { replace: true });
  }, [me.error, nav]);

  const base = `/w/${ws}`;
  const item = sp.get("item");
  const commands = [
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
  ];
  return (
    <div className="shell">
      <header className="topbar" role="banner">
        <strong className="brand">Team Tracker</strong>
        <select
          aria-label="Workspace"
          value={ws}
          onChange={(e) => {
            qc.clear();
            nav(`/w/${e.target.value}/review`);
          }}
        >
          {((mine.data?.data as Row[] | undefined) ?? [{ slug: ws, name: ws }]).map((x) => (
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
        <kbd title="Command palette">⌘K</kbd>
        <span aria-label="Signed in as">{me.data?.handle}</span>
      </header>
      <nav className="sidenav" aria-label="Main">
        <NavLink to={`${base}/setup/projects`}>{t("nav.setup")}</NavLink>
        <NavLink to={`${base}/review`}>{t("nav.review")}</NavLink>
        <NavLink to={`${base}/views`}>{t("nav.views")}</NavLink>
        <NavLink to={`${base}/present`}>{t("nav.present")}</NavLink>
        <NavLink to={`${base}/policies`}>{t("nav.policies")}</NavLink>
        <NavLink to={`${base}/weights`}>{t("nav.weights")}</NavLink>
      </nav>
      <main className="content" id="main">
        {me.isError ? <p role="alert">Could not load your session.</p> : <Outlet />}
      </main>
      <CommandPalette commands={commands} />
      {item && (
        <ItemDrawer ws={ws} itemKey={item} onClose={() => setSp((p) => (p.delete("item"), p))} />
      )}
    </div>
  );
}
