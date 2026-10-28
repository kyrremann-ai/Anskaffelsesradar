import { QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { HashRouter, MemoryRouter, Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import "./index.css";
import { STATIC, queryClient } from "./lib/api";
import { ScopeProvider } from "./lib/scope";
import Buyers from "./pages/Buyers";
import Doffin from "./pages/Doffin";
import Invoices from "./pages/Invoices";
import Method from "./pages/Method";
import Overview from "./pages/Overview";
import { SupplierPage, Suppliers } from "./pages/Suppliers";
import Workbench from "./pages/Workbench";

// Artifact-siden kan ikke styre adresselinjen, så der holdes navigasjonen i minnet
const Router = STATIC ? MemoryRouter : HashRouter;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ScopeProvider>
        <Router>
          <Layout>
            <Routes>
              <Route path="/" element={<Overview />} />
              <Route path="/arbeid" element={<Workbench />} />
              <Route path="/oppdragsgivere" element={<Buyers />} />
              <Route path="/leverandorer" element={<Suppliers />} />
              <Route path="/leverandorer/:orgnr" element={<SupplierPage />} />
              <Route path="/doffin" element={<Doffin />} />
              <Route path="/metode" element={<Method />} />
              <Route path="/fakturaer" element={<Invoices />} />
              {/* gamle lenker fra v1 */}
              <Route path="/register" element={<Navigate to="/arbeid" replace />} />
              <Route path="/saker" element={<Navigate to="/arbeid?sak=med" replace />} />
              <Route path="/modell" element={<Navigate to="/metode" replace />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </Layout>
        </Router>
      </ScopeProvider>
    </QueryClientProvider>
  </StrictMode>,
);
