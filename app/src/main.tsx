import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import "./index.css";
import { Layout } from "./components/Layout";
import { Jams } from "./pages/Jams";
import { MoodMap } from "./pages/MoodMap";
import { Sessions } from "./pages/Sessions";
import { Songs } from "./pages/Songs";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Navigate to="/songs" replace />} />
          <Route path="songs" element={<Songs />} />
          <Route path="jams" element={<Jams />} />
          <Route path="sessions" element={<Sessions />} />
          <Route path="mood-map">
            <Route index element={<Navigate to="segments" replace />} />
            <Route path="segments" element={<MoodMap kind="segments" />} />
            <Route path="passages" element={<MoodMap kind="recording-passage" />} />
          </Route>
        </Route>
      </Routes>
    </BrowserRouter>
  </StrictMode>
);
