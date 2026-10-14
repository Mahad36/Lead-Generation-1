"use client";

import { automaticLabel, useEmailAccounts } from "@/components/send-from";
import { Badge, Button, Input } from "@/components/ui";
import { api } from "@/lib/api";
import { useEffect, useState } from "react";
import { toast } from "sonner";

/** One scrape file's automatic outreach (apps/api/app/services/auto_audits.py::job_outreach_status). */
export type FileOutreach = {
  status: "off" | "running" | "paused" | "stopped";
  auto_send: boolean;
  daily_limit: number;
  audits_today: number;
  sent_today: number;
  sent_total: number;
  running: number;
  failed_today: number;
  waiting_leads: number;
  poll_seconds: number;
  // Whole-file progress
  audited_total?: number;
  failed_total?: number;
  send_failed_total?: number;
  waiting_to_send?: number;
  // The account this file sends from (null = your default account)
  email_account_id: string | null;
  email_account: string;
  email_account_issue: string;
};

export type OutreachMode = "auto" | "review" | "off";

const TONE = { running: "ok", paused: "warn", stopped: "bad", off: "default" } as const;
const LABEL = { running: "Outreach running", paused: "Outreach paused", stopped: "Outreach stopped", off: "Outreach off" };

/** Account-wide problems (AI not set up, no email account) that stop outreach for every file. */
export function OutreachIssues() {
  const [issues, setIssues] = useState<string[]>([]);
  useEffect(() => {
    api<{ issues: string[] }>("/api/audits/auto-status")
      .then((data) => setIssues(data.issues || []))
      .catch(() => setIssues([]));
  }, []);
  if (!issues.length) return null;
  return (
    <ul className="list-disc space-y-1 pl-5 text-sm text-amber-700 dark:text-amber-300">
      {issues.map((issue) => (
        <li key={issue}>{issue}</li>
      ))}
    </ul>
  );
}

/** Choose the outreach for a new scrape file: audit + send, audit + review, or off; and its daily limit. */
export function OutreachModePicker({
  mode,
  limit,
  onMode,
  onLimit,
}: {
  mode: OutreachMode;
  limit: number;
  onMode: (mode: OutreachMode) => void;
  onLimit: (limit: number) => void;
}) {
  const options: [OutreachMode, string, string][] = [
    ["auto", "Audit and send automatically", "Each lead with a website and email is audited and emailed with its PDF."],
    ["review", "Audit automatically, I review", "Audits and emails are prepared; you send them from the lead page."],
    ["off", "Off", "Only collect leads. You can start outreach for this file later."],
  ];
  return (
    <div className="space-y-2 rounded-xl border border-border p-3">
      <div className="text-sm font-medium">Automatic outreach for this file</div>
      <div className="grid gap-2 md:grid-cols-3">
        {options.map(([value, title, hint]) => (
          <label key={value} className="flex cursor-pointer items-start gap-2 text-sm">
            <input type="radio" className="mt-1" checked={mode === value} onChange={() => onMode(value)} />
            <span>
              <span className="font-medium">{title}</span>
              <span className="block text-xs text-muted">{hint}</span>
            </span>
          </label>
        ))}
      </div>
      {mode !== "off" ? (
        <div className="space-y-1">
          <label className="flex items-center gap-2 text-sm">
            Max audits per day for this file
            <Input
              className="h-8 w-24 py-1"
              type="number"
              min={0}
              placeholder="No limit"
              value={limit || ""}
              onChange={(e) => onLimit(Math.max(0, Number(e.target.value) || 0))}
            />
          </label>
          <p className="text-xs text-muted">
            Empty = no limit. Audits don&apos;t depend on how many emails your inboxes can send: finished emails wait
            for an inbox with room. The real limits are your Claude plan&apos;s usage and 2 audits at a time (each one
            runs Claude and a browser on this PC, about 2-3 minutes).
          </p>
        </div>
      ) : null}
      {mode !== "off" ? <OutreachIssues /> : null}
    </div>
  );
}

/** Status, numbers and Start / Pause / Resume / Stop for one scrape file's outreach. */
export function FileOutreachControls({
  jobId,
  outreach,
  onChange,
}: {
  jobId: string;
  outreach?: FileOutreach | null;
  onChange?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [confirmStop, setConfirmStop] = useState(false);
  // What is being typed in the daily-limit box; null = show the saved limit.
  const [draft, setDraft] = useState<string | null>(null);
  const accounts = useEmailAccounts();

  if (!outreach) return null;
  const limit = draft ?? String(outreach.daily_limit);
  const o = outreach;

  async function send(action: string, extra: Record<string, unknown> = {}, message = "") {
    setBusy(true);
    try {
      await api(`/api/scraping/jobs/${jobId}/outreach`, { method: "POST", body: JSON.stringify({ action, ...extra }) });
      if (message) toast.success(message);
      setConfirmStop(false);
      onChange?.();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function saveLimit() {
    const value = Math.max(0, Number(limit) || 0);
    setDraft(null);
    if (value !== o.daily_limit)
      send("update", { daily_limit: value }, value ? `Daily audit limit for this file: ${value}` : "No daily audit limit for this file");
  }

  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <Badge tone={TONE[o.status]}>{LABEL[o.status]}</Badge>
      {o.status !== "off" ? (
        <span className="text-xs text-muted">
          {o.audits_today}
          {o.daily_limit ? `/${o.daily_limit}` : ""} audited today · {o.audited_total ?? 0} audited in total ·{" "}
          {o.sent_total} sent · {o.waiting_to_send ? `${o.waiting_to_send} waiting to send · ` : ""}
          {o.waiting_leads} leads left
          {o.running ? ` · ${o.running} running` : ""}
          {o.failed_total ? ` · ${o.failed_total} audits failed` : ""}
          {o.send_failed_total ? ` · ${o.send_failed_total} sends failed` : ""}
          {o.auto_send ? "" : " · review mode"}
        </span>
      ) : (
        <span className="text-xs text-muted">{o.waiting_leads} leads ready</span>
      )}
      {o.status === "running" || o.status === "paused" ? (
        <label className="flex items-center gap-1 text-xs text-muted">
          per day
          <Input
            className="h-7 w-20 py-0.5"
            type="number"
            min={0}
            placeholder="no limit"
            value={limit === "0" ? "" : limit}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={saveLimit}
            onKeyDown={(e) => {
              if (e.key === "Enter") (e.target as HTMLInputElement).blur();
            }}
          />
        </label>
      ) : null}
      {o.failed_total ? (
        <Button variant="secondary" disabled={busy} onClick={() => send("retry_failed", {}, "Failed audits will be tried again")}>
          Retry failed audits
        </Button>
      ) : null}
      <label className="flex items-center gap-1 text-xs text-muted">
        from
        <select
          className="h-7 max-w-[220px] rounded-md border border-border bg-card px-2 text-xs text-foreground"
          value={o.email_account_id || ""}
          disabled={busy || accounts === null}
          onChange={(e) => {
            const id = e.target.value;
            const address = accounts?.find((a) => a.id === id)?.email_address;
            send("update", { email_account_id: id }, `This file now sends from ${address || "your default account"}`);
          }}
        >
          <option value="">{automaticLabel(accounts)}</option>
          {(accounts || [])
            .filter((a) => a.status === "connected" || a.id === o.email_account_id)
            .map((a) => (
              <option key={a.id} value={a.id}>
                {a.email_address}
                {a.status === "connected" ? "" : " (not connected)"}
              </option>
            ))}
        </select>
      </label>
      {o.email_account_issue ? <span className="w-full text-xs text-amber-700 dark:text-amber-300">{o.email_account_issue}</span> : null}
      {o.status === "off" || o.status === "stopped" ? (
        <>
          <Button disabled={busy || !o.waiting_leads} onClick={() => send("start", { auto_send: true }, "Outreach started for this file")}>
            Start outreach
          </Button>
          <Button
            variant="secondary"
            disabled={busy || !o.waiting_leads}
            onClick={() => send("start", { auto_send: false }, "Audits started; emails wait for your review")}
          >
            Start (I review)
          </Button>
        </>
      ) : null}
      {o.status === "running" ? (
        <Button variant="secondary" disabled={busy} onClick={() => send("pause", {}, "Paused. Emails being prepared wait until you resume.")}>
          Pause
        </Button>
      ) : null}
      {o.status === "paused" ? (
        <Button disabled={busy} onClick={() => send("resume", {}, "Outreach resumed")}>
          Resume
        </Button>
      ) : null}
      {o.status === "running" || o.status === "paused" ? (
        confirmStop ? (
          <span className="flex items-center gap-1">
            <Button variant="danger" disabled={busy} onClick={() => send("stop", {}, "Stopped. Nothing more is sent from this file.")}>
              Yes, stop
            </Button>
            <Button variant="ghost" onClick={() => setConfirmStop(false)}>
              Keep
            </Button>
          </span>
        ) : (
          <Button variant="danger" disabled={busy} onClick={() => setConfirmStop(true)}>
            Stop
          </Button>
        )
      ) : null}
    </div>
  );
}
