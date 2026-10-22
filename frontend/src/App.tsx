import { TideCloakContextProvider } from "@tidecloak/react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AuthRedirect, RequireSession, SignInScreen } from "./auth/session";
import { Layout } from "./components/Layout";
import { Empty } from "./components/ui";
import { AdminPage } from "./pages/Admin";
import { AuditPage } from "./pages/Audit";
import { DashboardPage } from "./pages/Dashboard";
import { NewRequestPage } from "./pages/NewRequest";
import { PatientDetailPage, PatientsPage } from "./pages/Patients";
import { PolicyPage } from "./pages/Policy";
import { ReleasedPage } from "./pages/Released";
import { RequestDetailPage } from "./pages/RequestDetail";
import { RequestsPage } from "./pages/Requests";
import { ReviewQueuePage } from "./pages/ReviewQueue";
import { StatisticsPage } from "./pages/Statistics";

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/sign-in" element={<SignInScreen />} />
      <Route path="/auth/redirect" element={<AuthRedirect />} />
      <Route element={<RequireSession><Layout /></RequireSession>}>
        <Route index element={<DashboardPage />} />
        <Route path="requests" element={<RequestsPage />} />
        <Route path="requests/new" element={<NewRequestPage />} />
        <Route path="requests/:id" element={<RequestDetailPage />} />
        <Route path="reviews" element={<ReviewQueuePage />} />
        <Route path="released" element={<ReleasedPage />} />
        <Route path="patients" element={<PatientsPage />} />
        <Route path="patients/:id" element={<PatientDetailPage />} />
        <Route path="statistics" element={<StatisticsPage />} />
        <Route path="audit" element={<AuditPage />} />
        <Route path="policy" element={<PolicyPage />} />
        <Route path="admin" element={<AdminPage />} />
        <Route path="*" element={<Empty title="Page not found">Check the address, or use the menu.</Empty>} />
      </Route>
    </Routes>
  );
}

export default function App() {
  // The adapter file is downloaded from tide-console and served at /tidecloak.json.
  // It contains public client configuration only (no client secret).
  return (
    <TideCloakContextProvider configUrl="/tidecloak.json">
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </TideCloakContextProvider>
  );
}
