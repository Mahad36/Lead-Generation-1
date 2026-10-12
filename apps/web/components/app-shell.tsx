"use client";

import { api, authApi } from "@/lib/api";
import { clearTokens, getRefreshToken, isAuthenticated } from "@/lib/auth";
import { cn } from "@/lib/utils";
import {
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
  Sun,
  UserCheck,
  Users,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";

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
  { href: "/settings", label: "AI & settings", icon: Settings },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { theme, setTheme } = useTheme();
  const [email, setEmail] = useState("");
  const [unread, setUnread] = useState(0);

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
