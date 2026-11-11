import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { lazy, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes, useParams } from "react-router-dom";
import { ConfirmProvider } from "./components/Confirm";
import { RequireAccess, Shell } from "./components/Shell";
import { ToastProvider } from "./components/Toasts";
import { HealthPage } from "./pages/HealthPage";
import { Login } from "./pages/Login";
import { ReviewQueue } from "./pages/ReviewQueue";
import { useQuery } from "@tanstack/react-query";
import { getMe, listMyWorkspaces } from "./api/session";
import type { Workspace } from "./api/types";

// One chunk per route group: the main bundle carries only the shell and the review queue (Phase 13 R5.2).
const lazyNamed = <T extends Record<string, React.ComponentType>>(
  load: () => Promise<T>,
  name: keyof T & string,
) => lazy(async () => ({ default: (await load())[name] }));
const Setup = lazyNamed(() => import("./pages/setup/Setup"), "Setup");
const ViewsRoute = lazyNamed(() => import("./pages/views/Views"), "ViewsPage");
const PresentRoute = lazyNamed(() => import("./pages/present/Present"), "PresentRoute");
const PlanRoute = lazyNamed(() => import("./pages/plan/PlanPage"), "PlanRoute");
const PoliciesRoute = lazyNamed(() => import("./pages/governance/Policies"), "PoliciesRoute");
const Violations = lazyNamed(() => import("./pages/governance/Violations"), "Violations");
const Habits = lazyNamed(() => import("./pages/governance/Habits"), "Habits");
const Performance = lazyNamed(() => import("./pages/governance/Performance"), "Performance");
const Digest = lazyNamed(() => import("./pages/governance/Digest"), "Digest");
const Insights = lazyNamed(() => import("./pages/governance/Insights"), "Insights");
const Weights = lazyNamed(() => import("./pages/governance/Weights"), "Weights");

/** First screen after sign-in: the review queue for managers, Views for everyone else. */
function Landing() {
  const { ws = "" } = useParams();
  const me = useQuery({ queryKey: [ws, "me"], queryFn: () => getMe(ws), retry: false });
  if (me.isLoading) return <p>Loading…</p>;
  return <Navigate to={me.data?.nav?.review === false ? "views" : "review"} replace />;
}

function Home() {
  const q = useQuery({
    queryKey: ["workspaces"],
    queryFn: () => listMyWorkspaces(),
    retry: false,
  });
  if (q.isError) return <Navigate to="/login" replace />;
  const first = (q.data?.data as Workspace[] | undefined)?.[0];
  return first ? <Navigate to={`/w/${first.slug}`} replace /> : <p>Loading…</p>;
}

export function App() {
  const [qc] = useState(
    () =>
      new QueryClient({
        defaultOptions: { queries: { staleTime: 5_000, refetchOnWindowFocus: false } },
      }),
  );
  return (
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <ConfirmProvider>
          <BrowserRouter>
            <Routes>
              <Route path="/" element={<Home />} />
              <Route path="/login" element={<Login />} />
              <Route path="/status" element={<HealthPage />} />
              <Route path="/w/:ws" element={<Shell />}>
                <Route index element={<Landing />} />
                <Route
                  path="review"
                  element={
                    <RequireAccess area="review">
                      <ReviewQueue />
                    </RequireAccess>
                  }
                />
                <Route
                  path="setup/:section?"
                  element={
                    <RequireAccess area="setup">
                      <Setup />
                    </RequireAccess>
                  }
                />
                <Route path="views/*" element={<ViewsRoute />} />
                <Route path="present/*" element={<PresentRoute />} />
                <Route path="plan/*" element={<PlanRoute />} />
                <Route
                  path="policies/*"
                  element={
                    <RequireAccess area="policies">
                      <PoliciesRoute />
                    </RequireAccess>
                  }
                />
                <Route
                  path="violations"
                  element={
                    <RequireAccess area="violations">
                      <Violations />
                    </RequireAccess>
                  }
                />
                <Route path="habits" element={<Habits />} />
                <Route path="performance" element={<Performance />} />
                <Route
                  path="digest"
                  element={
                    <RequireAccess area="digest">
                      <Digest />
                    </RequireAccess>
                  }
                />
                <Route path="insights" element={<Insights />} />
                <Route
                  path="weights"
                  element={
                    <RequireAccess area="weights">
                      <Weights />
                    </RequireAccess>
                  }
                />
              </Route>
            </Routes>
          </BrowserRouter>
        </ConfirmProvider>
      </ToastProvider>
    </QueryClientProvider>
  );
}
