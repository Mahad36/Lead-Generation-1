"use client";

import { api, authApi } from "@/lib/api";
import { clearTokens, getRefreshToken, isAuthenticated } from "@/lib/auth";
import { cn } from "@/lib/utils";
import {
  Bell,
  FileSearch,
  FileText,
  LayoutDashboard,
  LogOut,
  Mail,
  MailOpen,
  Map,
  Megaphone,
  Moon,
  Settings,
  ShieldAlert,
  Sun,
  UserCheck,
  Users,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";
import { toast } from "sonner";

const NAV = [
  { href: "/dashboard", label: "Home", icon: LayoutDashboard },
  { href: "/scraping", label: "Scraping / Leads", icon: Map },
  { href: "/leads", label: "Lead files", icon: Users },
  { href: "/replies", label: "Replies", icon: MailOpen },
  { href: "/replied-leads", label: "Replied leads", icon: UserCheck },
  { href: "/audits", label: "Website audits", icon: FileSearch },
  { href: "/email-templates", label: "Templates", icon: FileText },
  { href: "/campaigns", label: "Campaigns", icon: Megaphone },
  { href: "/email-accounts", label: "Email setup", icon: Mail },
  { href: "/email-health", label: "Email health", icon: ShieldAlert },
  { href: "/settings", label: "AI & settings", icon: Settings },
];

type HealthAlert = { id: string; severity: "warning" | "critical"; subject: string; title: string; message: string };
type AlertSummary = { open: number; critical: number; unread: number; to_notify: HealthAlert[]; top_critical: HealthAlert | null };

/** New email-health alerts: a pop-up in the app and, when allowed, a desktop notification. */
function announce(alerts: HealthAlert[], open: (href: string) => void) {
  for (const a of alerts.slice(0, 3)) {
    const text = `${a.subject}: ${a.message}`;
    const action = { label: "View", onClick: () => open("/email-health") };
    if (a.severity === "critical") toast.error(a.title, { description: text, duration: 20_000, action });
    else toast.warning(a.title, { description: text, duration: 12_000, action });
    try {
      if (typeof Notification !== "undefined" && Notification.permission === "granted") {
        const note = new Notification(`${a.severity === "critical" ? "⚠ " : ""}${a.title}`, { body: text, tag: a.id });
        note.onclick = () => {
          window.focus();
          open("/email-health");
        };
      }
    } catch {
      /* desktop notifications unavailable */
    }
  }
  if (alerts.length > 3) toast.warning(`${alerts.length - 3} more email health alerts`, { action: { label: "View", onClick: () => open("/email-health") } });
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { theme, setTheme } = useTheme();
  const [email, setEmail] = useState("");
  const [unread, setUnread] = useState(0);
  const [alerts, setAlerts] = useState<AlertSummary | null>(null);

  // Email health alerts: the bell, the banner and pop-ups for new ones (checked every minute).
  useEffect(() => {
    if (!isAuthenticated()) return;
    const load = () =>
      api<AlertSummary>("/api/health-alerts/summary")
        .then((data) => {
          setAlerts(data);
          if (data.to_notify.length) {
            announce(data.to_notify, (href) => router.push(href));
            api("/api/health-alerts/notified", {
              method: "POST",
              body: JSON.stringify({ ids: data.to_notify.map((a) => a.id) }),
            }).catch(() => undefined);
          }
        })
        .catch(() => undefined);
    load();
    const timer = setInterval(load, 60_000);
    return () => clearInterval(timer);
  }, [pathname, router]);

  // Unread lead replies, shown on the "Replies" menu item.
  useEffect(() => {
    if (!isAuthenticated()) return;
    const load = () =>
      api<{ unread: number }>("/api/conversations/unread-count")
        .then((data) => setUnread(data.unread || 0))
        .catch(() => undefined);
    load();
    const timer = setInterval(load, 60_000);
    return () => clearInterval(timer);
  }, [pathname]);

  useEffect(() => {
    if (!isAuthenticated()) {
      router.replace("/login");
      return;
    }
    authApi
      .me()
      .then((u) => setEmail((u as { email?: string }).email || ""))
      .catch(() => router.replace("/login"));
  }, [router]);

  async function logout() {
    try {
      await authApi.logout(getRefreshToken());
    } catch {
      /* ignore */
    }
    clearTokens();
    router.replace("/login");
  }

  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-64 shrink-0 bg-sidebar text-sidebar-foreground md:sticky md:top-0 md:block md:h-screen md:self-start md:overflow-y-auto">
        <div className="px-5 py-6 text-lg font-semibold">Lead Generator</div>
        <nav className="space-y-1 px-3">
          {NAV.map((item) => {
            const Icon = item.icon;
            const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "flex items-center gap-3 rounded-md px-3 py-2 text-sm",
                  active ? "bg-white/10 text-white" : "text-slate-300 hover:bg-white/5"
                )}
              >
                <Icon size={16} />
                {item.label}
                {item.href === "/replies" && unread ? (
                  <span className="ml-auto rounded-full bg-yellow-400 px-2 text-xs font-semibold text-black">{unread}</span>
                ) : null}
                {item.href === "/email-health" && alerts?.open ? (
                  <span
                    className={cn(
                      "ml-auto rounded-full px-2 text-xs font-semibold",
                      alerts.critical ? "bg-red-500 text-white" : "bg-yellow-400 text-black"
                    )}
                  >
                    {alerts.open}
                  </span>
                ) : null}
              </Link>
            );
          })}
        </nav>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex gap-2 overflow-x-auto border-b border-border bg-card px-3 py-2 md:hidden">
          {NAV.slice(0, 5).map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "shrink-0 rounded-md px-3 py-1.5 text-sm",
                pathname === item.href || pathname.startsWith(`${item.href}/`)
                  ? "bg-primary text-primary-foreground"
                  : "bg-background"
              )}
            >
              {item.label}
              {item.href === "/replies" && unread ? ` (${unread})` : ""}
            </Link>
          ))}
        </div>
        <header className="flex items-center justify-between border-b border-border bg-card px-4 py-3">
          <div className="font-medium md:hidden">Lead Generator</div>
          <div className="ml-auto flex items-center gap-3 text-sm">
            <Link
              href="/email-health"
              title={alerts?.open ? `${alerts.open} email health alert(s)` : "Email health: no alerts"}
              className="relative rounded-md p-2 hover:bg-background"
            >
              <Bell size={16} className={alerts?.critical ? "text-red-500" : alerts?.open ? "text-yellow-500" : ""} />
              {alerts?.unread ? (
                <span
                  className={cn(
                    "absolute -right-0.5 -top-0.5 min-w-4 rounded-full px-1 text-center text-[10px] font-semibold leading-4",
                    alerts.critical ? "bg-red-500 text-white" : "bg-yellow-400 text-black"
                  )}
                >
                  {alerts.unread}
                </span>
              ) : null}
            </Link>
            <span className="hidden text-muted sm:inline">{email}</span>
            <button
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
              className="rounded-md p-2 hover:bg-background"
              type="button"
            >
              {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
            </button>
            <button
              onClick={logout}
              className="inline-flex items-center gap-1 rounded-md px-2 py-1 hover:bg-background"
              type="button"
            >
              <LogOut size={14} /> Logout
            </button>
          </div>
        </header>
        {alerts?.top_critical && pathname !== "/email-health" ? (
          <Link
            href="/email-health"
            className="flex flex-wrap items-center gap-2 border-b border-red-300 bg-red-50 px-4 py-2 text-sm text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200"
          >
            <ShieldAlert size={16} />
            <span className="font-medium">{alerts.top_critical.title}</span>
            <span>· {alerts.top_critical.subject}</span>
            {alerts.critical > 1 ? <span>· +{alerts.critical - 1} more</span> : null}
            <span className="ml-auto underline">What to do</span>
          </Link>
        ) : null}
        <main className="flex-1 p-4 md:p-6">{children}</main>
      </div>
    </div>
  );
}

export function useHealth() {
  const [health, setHealth] = useState<{ google_oauth_configured?: boolean; ai_configured?: boolean } | null>(
    null
  );
  useEffect(() => {
    api<{ google_oauth_configured?: boolean; ai_configured?: boolean }>("/api/settings")
      .then(setHealth)
      .catch(() => setHealth(null));
  }, []);
  return health;
}
