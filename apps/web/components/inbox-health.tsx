"use client";

import { Badge, Button } from "@/components/ui";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/utils";
import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

type Placement = { at: string; running: boolean; subject?: string; error?: string; results: { to: string; landed: string }[] };
type DomainCheck = {
  at: string;
  domain: string;
  managed_by_google: boolean;
  summary: string;
  checks: { name: string; ok: boolean; detail: string }[];
};

export type InboxHealthAccount = {
  id: string;
  email_address: string;
  provider: string;
  status: string;
  daily_limit: number;
  limit_today?: number;
  ramping_up?: boolean;
  paused_reason?: string;
  paused_at?: string | null;
  warmup_enabled?: boolean;
  warmup?: { sent_today: number; daily_target: number; last_7_days: Record<string, number> } | null;
  health?: { domain?: DomainCheck; placement?: Placement };
};

const LANDED: Record<string, { label: string; tone: "ok" | "warn" | "bad" | "default" }> = {
  inbox: { label: "Primary inbox", tone: "ok" },
  promotions: { label: "Promotions", tone: "warn" },
  spam: { label: "Spam", tone: "bad" },
  missing: { label: "Not delivered", tone: "bad" },
  error: { label: "Couldn't check", tone: "default" },
};

/** Ramp-up, bounce pause, warm-up, domain check and spam test for one inbox (Email setup page). */
export function InboxHealth({ account, inboxes, onChange }: { account: InboxHealthAccount; inboxes: number; onChange: () => void }) {
  const [busy, setBusy] = useState("");
  const a = account;
  const placement = a.health?.placement;
  const domain = a.health?.domain;

  // While a spam test runs (about 2 minutes), refresh until its result arrives.
  useEffect(() => {
    if (!placement?.running) return;
    const timer = setInterval(onChange, 15_000);
    return () => clearInterval(timer);
  }, [placement?.running, onChange]);

  async function run(path: string, label: string, body?: unknown) {
    setBusy(path);
    try {
      await api(`/api/email-accounts/${a.id}/${path}`, { method: "POST", body: body ? JSON.stringify(body) : undefined });
      if (label) toast.success(label);
      onChange();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy("");
    }
  }

  const smtp = a.provider === "smtp";
  const w = a.warmup;

  return (
    <div className="mt-3 space-y-2 border-t border-border pt-3 text-sm">
      {a.paused_reason ? (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-red-300 bg-red-50 p-2 text-red-800 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200">
          <span>{a.paused_reason}</span>
          <Button variant="secondary" disabled={!!busy} onClick={() => run("resume", "Inbox resumed")}>
            Resume sending
          </Button>
        </div>
      ) : null}

      {a.ramping_up ? (
        <p className="text-xs text-muted">
          Ramping up as a new inbox: {a.limit_today} emails today, rising by 2 a day to {a.daily_limit}.
        </p>
      ) : null}

      {smtp ? (
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={!!a.warmup_enabled}
              disabled={!!busy}
              onChange={(e) => run("warmup", e.target.checked ? "Warm-up on for this inbox" : "Warm-up off for this inbox", { enabled: e.target.checked })}
            />
            Warm-up
          </label>
          <span className="text-xs text-muted">
            {inboxes < 2
              ? "Starts when you connect a second Gmail."
              : a.warmup_enabled && w
                ? `${w.sent_today}/${w.daily_target} today · last 7 days: ${w.last_7_days.inbox || 0} inbox, ${w.last_7_days.promotions || 0} promotions, ${w.last_7_days.spam || 0} rescued from spam`
                : ""}
          </span>
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        <Button variant="secondary" disabled={!!busy} onClick={() => run("domain-check", "")}>
          {busy === "domain-check" ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
          Check domain
        </Button>
        {smtp ? (
          <Button
            variant="secondary"
            disabled={!!busy || !!placement?.running}
            onClick={() => run("placement-test", "Spam test started. The result appears here in about 2 minutes.")}
          >
            {placement?.running ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            {placement?.running ? "Testing inbox placement..." : "Spam test"}
          </Button>
        ) : null}
      </div>

      {domain ? (
        <div className="space-y-1 text-xs">
          <div className="text-muted">
            Domain {domain.domain}: {domain.summary} ({formatDate(domain.at)})
          </div>
          {domain.checks.map((c) => (
            <div key={c.name} className="flex items-start gap-2">
              <Badge tone={c.ok ? "ok" : "bad"}>{c.name}</Badge>
              <span className="break-all text-muted">{c.detail}</span>
            </div>
          ))}
        </div>
      ) : null}

      {placement && !placement.running ? (
        <div className="space-y-1 text-xs">
          <div className="text-muted">
            Spam test ({formatDate(placement.at)}){placement.subject ? `, using "${placement.subject}"` : ""}:
          </div>
          {placement.error ? <div className="text-danger">{placement.error}</div> : null}
          {placement.results.map((r) => (
            <div key={r.to} className="flex items-center gap-2">
              <Badge tone={(LANDED[r.landed] || LANDED.error).tone}>{(LANDED[r.landed] || LANDED.error).label}</Badge>
              <span className="text-muted">at {r.to}</span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
