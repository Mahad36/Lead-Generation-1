"use client";

import { Badge, Button } from "@/components/ui";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/utils";
import { Loader2, Upload } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

export type HealthSummary = {
  status: "healthy" | "monitoring" | "reduced" | "paused" | "reconnect";
  label: string;
  reason: string;
  recommended_today: number | null;
  your_daily_limit: number;
  limit_today: number;
  sent_today: number;
  remaining_today: number;
  provider_limit: number | null;
  sent_last_24h: number;
  sent_last_24h_source: string;
  provider_remaining_estimate: number | null;
  bounce_rate: number | null;
  bounces_in_last: [number, number] | null;
  unsubscribe_rate: number | null;
  failures_24h: Record<string, number>;
  restricted_until: string | null;
  checked_at: string | null;
  unavailable: string[];
  protection?: Protection;
};

/** Account health protection (apps/api/app/services/send_protection.py): may this inbox send right now? */
export type Protection = {
  eligible: boolean;
  stage: "" | "health" | "provider" | "safe_capacity" | "user_limit";
  reason: string;
  user_limit: number;
  safe_capacity: number | null;
  sent_today: number;
  in_flight: number;
  remaining_configured: number | null;
  remaining_safe: number | null;
  policy: "strict" | "balanced";
  blocked_since: string | null;
  held: { audits: number; campaign_emails: number; total: number };
  reassigned_today: number;
  events: { action: string; at: string; details: Record<string, string> }[];
};

const STAGE_LABEL: Record<string, string> = {
  health: "Paused \u2014 Account Health Protection",
  provider: "Paused \u2014 Gmail restriction",
  safe_capacity: "Safe capacity used for today",
  user_limit: "Your daily limit reached",
};
const EVENT_LABEL: Record<string, string> = {
  "sending.blocked": "Sending held",
  "sending.allowed": "Sending allowed again",
  "sending.reassigned": "Email moved to another inbox",
};

/** Can it send, and why not: health first, then Gmail restrictions, then safe capacity, then your limit. */
function ProtectionPanel({ p }: { p: Protection }) {
  const safety = p.stage === "health" || p.stage === "provider";
  return (
    <div className={`space-y-1 rounded-md p-2 text-xs ${safety ? "bg-red-50 dark:bg-red-950" : "bg-background"}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">Eligible to send:</span>
        {p.eligible ? <Badge tone="ok">Yes</Badge> : <Badge tone={safety ? "bad" : "warn"}>{STAGE_LABEL[p.stage] || "No"}</Badge>}
        {p.blocked_since && safety ? <span className="text-muted">since {formatDate(p.blocked_since)}</span> : null}
      </div>
      {p.reason ? <p className={safety ? "text-red-700 dark:text-red-300" : "text-muted"}>{p.reason}</p> : null}
      {safety ? (
        <p className="text-muted">
          Health comes before limits: nothing is sent from this inbox, even with capacity left. Emails wait (or go from
          another inbox only if that one passes every check itself) and sending resumes by itself once this clears.
        </p>
      ) : null}
      <div className="grid gap-x-6 gap-y-0.5 sm:grid-cols-2">
        <div>
          Your daily limit: <span className="font-medium">{p.user_limit || "none"}</span>
          {p.remaining_configured !== null ? ` · ${p.remaining_configured} left` : ""}
        </div>
        <div>
          Safe capacity today: <span className="font-medium">{p.safe_capacity ?? "no limit"}</span>
          {p.remaining_safe !== null ? ` · ${p.remaining_safe} left` : ""}
        </div>
        <div>
          Held by health protection: <span className="font-medium">{p.held.total}</span> email{p.held.total === 1 ? "" : "s"}
          {p.held.total ? ` (${p.held.audits} audit, ${p.held.campaign_emails} campaign; all inboxes)` : ""}
        </div>
        <div>
          Moved to another inbox today: <span className="font-medium">{p.reassigned_today}</span>
        </div>
      </div>
      {p.events.length ? (
        <details>
          <summary className="cursor-pointer text-muted">Sending blocks and moves ({p.events.length})</summary>
          <ul className="mt-1 space-y-0.5">
            {p.events.map((e) => (
              <li key={`${e.action}-${e.at}`}>
                <span className="text-muted">{formatDate(e.at)}</span> · {EVENT_LABEL[e.action] || e.action}
                {e.details.to ? ` to ${e.details.to}` : ""}
                {e.details.reason ? `: ${e.details.reason}` : ""}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

export const HEALTH_TONE = { healthy: "ok", monitoring: "default", reduced: "warn", paused: "bad", reconnect: "bad" } as const;

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
  health_summary?: HealthSummary;
  gmail_signature?: {
    found: boolean;
    html: string;
    text: string;
    from_email_dated: string | null;
    checked_at: string | null;
    error: string;
    source?: "gmail" | "uploaded" | "none";
  };
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
      {a.health_summary ? <AccountHealth h={a.health_summary} busy={busy} onRecheck={() => run("health/refresh", "")} /> : null}
      {a.paused_reason ? (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-red-300 bg-red-50 p-2 text-red-800 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200">
          <span>{a.paused_reason}</span>
          <Button variant="secondary" disabled={!!busy} onClick={() => run("resume", "Inbox resumed")}>
            Resume sending
          </Button>
        </div>
      ) : null}

      {smtp ? <GmailSignature account={a} busy={busy} onRefresh={() => run("signature/refresh", "")} onChanged={onChange} /> : null}

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

/** This Gmail's own signature, read from Gmail (read-only: edit it in Gmail's settings). */
function GmailSignature({
  account,
  busy,
  onRefresh,
  onChanged,
}: {
  account: InboxHealthAccount;
  busy: string;
  onRefresh: () => void;
  onChanged: () => void;
}) {
  const sig = account.gmail_signature;
  const source = sig?.source || "gmail";
  const [working, setWorking] = useState("");
  const file = useRef<HTMLInputElement>(null);
  const refreshing = busy === "signature/refresh";

  async function upload(f: File) {
    setWorking("upload");
    try {
      const fd = new FormData();
      fd.append("file", f);
      await api(`/api/email-accounts/${account.id}/signature/upload`, { method: "POST", body: fd });
      toast.success(`Signature uploaded for ${account.email_address}`);
      onChanged();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setWorking("");
      if (file.current) file.current.value = "";
    }
  }

  async function useSource(next: "gmail" | "none") {
    setWorking(next);
    try {
      await api(`/api/email-accounts/${account.id}/signature/source`, { method: "POST", body: JSON.stringify({ source: next }) });
      toast.success(next === "none" ? "This account now sends without a signature" : "Reading the signature from Gmail again");
      onChanged();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setWorking("");
    }
  }

  const disabled = !!busy || !!working;
  return (
    <div className="space-y-2 rounded-md border border-border p-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-2">
          <span className="font-medium">Signature</span>
          <Badge>{source === "uploaded" ? "Uploaded HTML" : source === "none" ? "No signature" : "Read from Gmail"}</Badge>
        </span>
        <span className="flex flex-wrap gap-2">
          <input
            ref={file}
            type="file"
            accept=".html,.htm,text/html"
            className="hidden"
            onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])}
          />
          <Button variant="secondary" disabled={disabled} onClick={() => file.current?.click()}>
            {working === "upload" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
            {source === "uploaded" ? "Replace HTML file" : "Upload HTML signature"}
          </Button>
          {source !== "gmail" ? (
            <Button variant="ghost" disabled={disabled} onClick={() => useSource("gmail")}>
              Read from Gmail instead
            </Button>
          ) : (
            <Button variant="secondary" disabled={disabled} onClick={onRefresh}>
              {refreshing ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              {refreshing ? "Reading from Gmail..." : "Refresh from Gmail"}
            </Button>
          )}
          {source !== "none" ? (
            <Button variant="ghost" disabled={disabled} onClick={() => useSource("none")}>
              No signature
            </Button>
          ) : null}
        </span>
      </div>
      {source === "none" ? (
        <p className="text-xs text-muted">Emails from this account go out without a signature (your name is added to the sign-off).</p>
      ) : sig?.found ? (
        <>
          <iframe
            title={`Signature of ${account.email_address}`}
            sandbox=""
            className="h-40 w-full rounded border border-border bg-white"
            srcDoc={`<div style="font-family:Arial,sans-serif;font-size:13px;color:#222;padding:6px">${sig.html}</div>`}
          />
          <p className="text-xs text-muted">
            {source === "uploaded"
              ? "From the HTML file you uploaded. Added under every email this account sends (only this account). It is never replaced automatically; upload a new file to change it."
              : `Read from an email written in this Gmail${sig.from_email_dated ? ` on ${formatDate(sig.from_email_dated)}` : ""}. If it isn't yours, upload your own HTML signature instead.`}
          </p>
          {sig.error ? <p className="text-xs text-amber-700 dark:text-amber-300">{sig.error}</p> : null}
        </>
      ) : (
        <p className="text-xs text-amber-700 dark:text-amber-300">
          {sig?.checked_at ? sig.error || "No signature found." : "Not checked yet."} Upload your HTML signature, or emails
          from this account go out without one.
        </p>
      )}
      <p className="text-xs text-muted">
        Tip: in Gmail, open a sent email with your signature, use &quot;Show original&quot; or copy the signature into an
        .html file. Images must be public https links (not attachments) so recipients can see them.
      </p>
    </div>
  );
}

const pct = (v: number | null) => (v === null || v === undefined ? "not enough data" : `${(v * 100).toFixed(1)}%`);
const FAILURE_LABEL: Record<string, string> = {
  temporary: "busy / try later",
  rate_limited: "rate-limited",
  quota: "daily quota reached",
  auth: "login rejected",
  recipient: "bad address",
  rejected: "blocked by policy",
  other: "other",
};

/** Sending health from the evidence the app can see, today's safe capacity, and what Gmail doesn't share. */
function AccountHealth({ h, busy, onRecheck }: { h: HealthSummary; busy: string; onRecheck: () => void }) {
  const failures = Object.entries(h.failures_24h || {}).filter(([, n]) => n);
  return (
    <div className="space-y-2 rounded-md border border-border p-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-2">
          <span className="font-medium">Sending health</span>
          <Badge tone={HEALTH_TONE[h.status] || "default"}>{h.label}</Badge>
        </span>
        <Button variant="secondary" disabled={!!busy} onClick={onRecheck}>
          {busy === "health/refresh" ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
          Recheck
        </Button>
      </div>
      {h.reason ? <p className="text-xs text-muted">{h.reason}</p> : null}
      {h.protection ? <ProtectionPanel p={h.protection} /> : null}
      <div className="grid gap-x-6 gap-y-1 text-xs sm:grid-cols-2">
        <div>
          Safe capacity today: <span className="font-medium">{h.recommended_today ?? h.limit_today}</span>
          {h.your_daily_limit ? ` (your limit ${h.your_daily_limit})` : ""}
        </div>
        <div>
          Sent today: <span className="font-medium">{h.sent_today}</span> · left today:{" "}
          <span className="font-medium">{h.remaining_today}</span>
        </div>
        <div>
          Last 24 hours: <span className="font-medium">{h.sent_last_24h}</span>
          {h.provider_limit ? ` of Gmail's ${h.provider_limit}` : ""} ({h.sent_last_24h_source})
        </div>
        <div>
          Gmail quota left (estimate):{" "}
          <span className="font-medium">{h.provider_remaining_estimate ?? "unknown for this provider"}</span>
        </div>
        <div>
          Bounce rate: {pct(h.bounce_rate)}
          {h.bounces_in_last ? ` (${h.bounces_in_last[0]} of last ${h.bounces_in_last[1]})` : ""}
        </div>
        <div>Unsubscribe requests: {pct(h.unsubscribe_rate)}</div>
        <div className="sm:col-span-2">
          Gmail errors (24h):{" "}
          {failures.length ? failures.map(([k, n]) => `${n} ${FAILURE_LABEL[k] || k}`).join(", ") : "none"}
          {h.restricted_until ? ` · paused until ${formatDate(h.restricted_until)}` : ""}
        </div>
      </div>
      <details className="text-xs text-muted">
        <summary className="cursor-pointer">What Gmail doesn&apos;t share</summary>
        <ul className="mt-1 list-disc space-y-0.5 pl-5">
          {h.unavailable.map((item) => (
            <li key={item}>{item}</li>
          ))}
          <li>The status summarises the evidence above; it is not a guarantee of how Gmail rates this account.</li>
        </ul>
      </details>
    </div>
  );
}
