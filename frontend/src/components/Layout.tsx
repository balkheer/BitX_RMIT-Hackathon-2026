import { useTideCloak } from "@tidecloak/react";
import {
  BarChart3, ClipboardCheck, FilePlus2, FileStack, FolderOpen, Gauge, LogOut, Menu, ScrollText, Settings2,
  ShieldCheck, Users, X,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { BrandMark, can, useMe } from "../auth/session";
import { ROLE_LABEL } from "../labels";

interface Item { to: string; label: string; icon: ReactNode; show: boolean }

export function Layout() {
  const me = useMe();
  const { logout } = useTideCloak();
  const [open, setOpen] = useState(false);

  const items: Item[] = [
    { to: "/", label: "Overview", icon: <Gauge size={18} />, show: true },
    { to: "/requests", label: "Requests", icon: <FileStack size={18} />, show: true },
    { to: "/requests/new", label: "New request", icon: <FilePlus2 size={18} />, show: can(me, "requests:create") },
    { to: "/reviews", label: "Review queue", icon: <ClipboardCheck size={18} />, show: can(me, "reviews:decide") },
    { to: "/released", label: "Released data", icon: <FolderOpen size={18} />,
      show: me.role === "insurance_officer" || me.role === "hospital_staff" },
    { to: "/patients", label: "Patient records", icon: <Users size={18} />, show: can(me, "patients:read_own_org") },
    { to: "/statistics", label: "Statistics", icon: <BarChart3 size={18} />, show: can(me, "stats:query") },
    { to: "/audit", label: "Audit trail", icon: <ScrollText size={18} />, show: true },
    { to: "/policy", label: "Policy", icon: <ShieldCheck size={18} />, show: true },
    { to: "/admin", label: "Administration", icon: <Settings2 size={18} />, show: can(me, "admin:users") },
  ];

  return (
    <div className="shell">
      <a className="skip-link" href="#main">Skip to content</a>
      <aside className={`rail ${open ? "rail-open" : ""}`}>
        <div className="rail-top">
          <div className="brand"><BrandMark /><span>ConsentBridge</span></div>
          <button type="button" className="icon-btn rail-toggle" aria-label={open ? "Close menu" : "Open menu"}
            aria-expanded={open} aria-controls="primary-nav" onClick={() => setOpen((o) => !o)}>
            {open ? <X size={20} /> : <Menu size={20} />}
          </button>
        </div>
        <nav id="primary-nav" aria-label="Primary">
          <ul>
            {items.filter((i) => i.show).map((i) => (
              <li key={i.to}>
                <NavLink to={i.to} end={i.to === "/" || i.to === "/requests"} onClick={() => setOpen(false)}>
                  <span aria-hidden>{i.icon}</span>{i.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
        <div className="rail-user">
          <p className="user-name">{me.display_name}</p>
          <p className="user-meta">{ROLE_LABEL[me.role]}</p>
          <p className="user-meta">{me.organization.name}</p>
          <button type="button" className="btn btn-ghost" onClick={() => void logout()}>
            <LogOut aria-hidden size={16} /> Sign out
          </button>
        </div>
      </aside>
      <main id="main" className="content" tabIndex={-1}>
        <Outlet />
      </main>
    </div>
  );
}
