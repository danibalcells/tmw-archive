import { NavLink, Outlet } from "react-router-dom";

const NAV = [
  { to: "/songs", label: "Songs" },
  { to: "/jams", label: "Jams" },
  { to: "/sessions", label: "Sessions" },
];

export function Layout() {
  return (
    <div className="flex h-screen bg-zinc-950 text-zinc-100 overflow-hidden">
      <nav className="w-48 flex-shrink-0 border-r border-zinc-800 flex flex-col pt-8 pb-4">
        <div className="px-5 mb-8">
          <span className="text-sm font-bold tracking-wide text-green-400 uppercase">
            TMW Archive
          </span>
        </div>
        <ul className="space-y-0.5 px-3">
          {NAV.map(({ to, label }) => (
            <li key={to}>
              <NavLink
                to={to}
                className={({ isActive }) =>
                  `block px-3 py-2 rounded-md text-sm transition-colors ${
                    isActive
                      ? "bg-zinc-800 text-white font-medium"
                      : "text-zinc-400 hover:text-white hover:bg-zinc-800/50"
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
  );
}
