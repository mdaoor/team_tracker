import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { BrowserRouter, Navigate, Route, Routes, useParams } from "react-router-dom";
import { ConfirmProvider } from "./components/Confirm";
import { RequireAccess, Shell } from "./components/Shell";
import { ToastProvider } from "./components/Toasts";
import { HealthPage } from "./pages/HealthPage";
import { Login } from "./pages/Login";
import { ReviewQueue } from "./pages/ReviewQueue";
import { Setup } from "./pages/setup/Setup";
import { useQuery } from "@tanstack/react-query";
import { api, w, type Row } from "./api/http";
import { PoliciesRoute } from "./pages/governance/Policies";
import { Violations } from "./pages/governance/Violations";
import { Habits } from "./pages/governance/Habits";
import { Performance } from "./pages/governance/Performance";
import { Digest } from "./pages/governance/Digest";
import { Insights } from "./pages/governance/Insights";
import { Weights } from "./pages/governance/Weights";
import { ViewsRoute, PresentRoute } from "./routes";
import { PlanRoute } from "./pages/plan/PlanPage";

/** First screen after sign-in: the review queue for managers, Views for everyone else. */
function Landing() {
  const { ws = "" } = useParams();
  const me = useQuery({ queryKey: [ws, "me"], queryFn: () => api.get<Row>(w(ws, "/me")), retry: false });
  if (me.isLoading) return <p>Loading…</p>;
  return <Navigate to={me.data?.nav?.review === false ? "views" : "review"} replace />;
}

function Home() {
  const q = useQuery({
    queryKey: ["workspaces"],
    queryFn: () => api.get<Row>("/api/v1/me/workspaces"),
    retry: false,
  });
  if (q.isError) return <Navigate to="/login" replace />;
  const first = (q.data?.data as Row[] | undefined)?.[0];
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
              <Route path="review" element={<RequireAccess area="review"><ReviewQueue /></RequireAccess>} />
              <Route path="setup/:section?" element={<RequireAccess area="setup"><Setup /></RequireAccess>} />
              <Route path="views/*" element={<ViewsRoute />} />
              <Route path="present/*" element={<PresentRoute />} />
              <Route path="plan/*" element={<PlanRoute />} />
              <Route path="policies/*" element={<RequireAccess area="policies"><PoliciesRoute /></RequireAccess>} />
              <Route path="violations" element={<RequireAccess area="violations"><Violations /></RequireAccess>} />
              <Route path="habits" element={<Habits />} />
              <Route path="performance" element={<Performance />} />
              <Route path="digest" element={<RequireAccess area="digest"><Digest /></RequireAccess>} />
              <Route path="insights" element={<Insights />} />
              <Route path="weights" element={<RequireAccess area="weights"><Weights /></RequireAccess>} />
            </Route>
          </Routes>
        </BrowserRouter>
       </ConfirmProvider>
      </ToastProvider>
    </QueryClientProvider>
  );
}
