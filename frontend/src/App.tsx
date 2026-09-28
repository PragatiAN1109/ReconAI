import { Navigate, Route, Routes } from "react-router-dom";

import { DashboardPage } from "./pages/DashboardPage";
import { InvestigationDetailPage } from "./pages/InvestigationDetailPage";

export function App() {
  return (
    <>
      <header className="app-header">
        <div className="app-header-inner">
          <div className="app-mark">
            Recon<span>AI</span>
          </div>
          <div className="app-tagline">
            Deterministic systems detect · AI investigates · Humans authorize
          </div>
          <div className="app-env">Operations Console</div>
        </div>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/investigations" replace />} />
          <Route path="/investigations" element={<DashboardPage />} />
          <Route path="/investigations/:investigationId" element={<InvestigationDetailPage />} />
          <Route path="*" element={<Navigate to="/investigations" replace />} />
        </Routes>
      </main>
    </>
  );
}
