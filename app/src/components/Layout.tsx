import { NavLink, Outlet } from "react-router-dom";
import { PlayerProvider } from "../lib/player";
import { NowPlayingBar } from "./NowPlayingBar";

const NAV = [
  { to: "/songs", label: "Songs" },
  { to: "/jams", label: "Jams" },
  { to: "/sessions", label: "Sessions" },
  { to: "/mood-map/segments", label: "Segment Map" },
  { to: "/mood-map/passages", label: "Passage Map" },
  { to: "/review", label: "Review" },
];

export function Layout() {
  return (
    <PlayerProvider>
      <div className="flex flex-col h-screen bg-cream text-warm-900 overflow-hidden">
        <div className="flex flex-1 overflow-hidden">
          <nav className="w-48 flex-shrink-0 border-r border-warm-200 flex flex-col pt-8 pb-4">
            <div className="px-5 mb-8">
              <span className="text-xs font-medium tracking-widest text-accent uppercase">
                TMW Archive
              </span>
            </div>
            <ul className="space-y-0.5 px-3">
              {NAV.map(({ to, label }) => (
                <li key={to}>
                  <NavLink
                    to={to}
                    className={({ isActive }) =>
                      `block px-3 py-2 rounded-sm text-sm transition-colors ${
                        isActive
                          ? "bg-warm-100 text-warm-900 font-medium"
                          : "text-warm-600 hover:text-warm-900 hover:bg-warm-100/70"
                      }`
                    }
                  >
                    {label}
                  </NavLink>
                </li>
              ))}
            </ul>
          </nav>
          <main className="flex-1 overflow-hidden">
            <Outlet />
          </main>
        </div>
        <NowPlayingBar />
      </div>
    </PlayerProvider>
  );
}
