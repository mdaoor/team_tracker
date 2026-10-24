import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Shell } from "./components/Shell";
import { ToastProvider } from "./components/Toasts";
import { HealthPage } from "./pages/HealthPage";
import { Login } from "./pages/Login";
import { ReviewQueue } from "./pages/ReviewQueue";
import { Setup } from "./pages/setup/Setup";
import { useQuery } from "@tanstack/react-query";
import { api, type Row } from "./api/http";
import { PoliciesRoute } from "./pages/governance/Policies";
import { Violations } from "./pages/governance/Violations";
import { Habits } from "./pages/governance/Habits";
import { Performance } from "./pages/governance/Performance";
import { Digest } from "./pages/governance/Digest";
import { Insights } from "./pages/governance/Insights";
import { Weights } from "./pages/governance/Weights";
import { ViewsRoute, PresentRoute } from "./routes";

function Home() {
  const q = useQuery({
    queryKey: ["workspaces"],
    queryFn: () => api.get<Row>("/api/v1/me/workspaces"),
    retry: false,
  });
  if (q.isError) return <Navigate to="/login" replace />;
  const first = (q.data?.data as Row[] | undefined)?.[0];
  return first ? <Navigate to={`/w/${first.slug}/review`} replace /> : <p>Loading…</p>;
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
        <BrowserRouter>
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/login" element={<Login />} />
            <Route path="/status" element={<HealthPage />} />
            <Route path="/w/:ws" element={<Shell />}>
              <Route index element={<Navigate to="review" replace />} />
              <Route path="review" element={<ReviewQueue />} />
              <Route path="setup/:section?" element={<Setup />} />
              <Route path="views/*" element={<ViewsRoute />} />
              <Route path="present/*" element={<PresentRoute />} />
              <Route path="policies/*" element={<PoliciesRoute />} />
              <Route path="violations" element={<Violations />} />
              <Route path="habits" element={<Habits />} />
              <Route path="performance" element={<Performance />} />
              <Route path="digest" element={<Digest />} />
              <Route path="insights" element={<Insights />} />
              <Route path="weights" element={<Weights />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </ToastProvider>
    </QueryClientProvider>
  );
}
