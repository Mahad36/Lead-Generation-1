"use client";

import { PlatformInsights, StatusBadge, type PlatformIntel, type PlatformOpportunity } from "@/components/platform-insights";
import { SendFrom } from "@/components/send-from";
import { TrackingBadges, type Tracking } from "@/components/tracking-badges";
import { Badge, Button, Card, Input, Label, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/utils";
import { CheckCircle2, Circle, Download, FileText, Loader2, Mail, RefreshCw, Send, Square, XCircle } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

type Finding = {
  title: string;
  severity: string;
  evidence: string;
  recommendation: string;
  where?: string;
  status?: string; // confirmed_missing | improvement | not_detected | manual_review
  evidence_url?: string;
};

export type Audit = Tracking & {
  id: string;
  lead_id: string;
  status: string;
  step: string;
  failed_step: string;
  error_message: string;
  website_url: string;
  recipient_email: string;
  email_account_id: string | null;
  platform?: string; // detected: Shopify, WordPress, Wix...
  platform_confidence_label?: string;
  finding_types?: string[];
  outreach_angle?: string;
  platform_intel?: PlatformIntel;
  auto_send: boolean;
  overall_score: number | null;
  headline: string;
  has_pdf: boolean;
  has_report?: boolean;
  pdf_filename: string;
  pdf_size: number;
  ai_provider: string;
  ai_model: string;
  created_at: string;
  generated_at: string | null;
  sent_at: string | null;
  report?: {
    executive_summary?: string;
    first_impression?: string;
    findings?: Finding[];
    platform_opportunities?: PlatformOpportunity[];
    outreach_angle?: string;
  };
  resources?: {
    total_requests?: number;
    total_bytes?: number;
    by_type?: Record<string, { count: number; bytes: number }>;
    third_party_requests?: number;
  };
  email_subject?: string;
  email_html?: string;
};

const STEPS: [string, string][] = [
  ["fetching", "Fetch website & customer data"],
  ["measuring", "Measure speed & page weight"],
  ["capturing", "Take desktop & phone screenshots"],
  ["analyzing", "Claude analyses the website"],
  ["generating_pdf", "Build the PDF audit"],
  ["drafting_email", "Claude writes the email"],
  ["sending", "Send email with PDF attached"],
];
const ORDER = ["queued", "fetching", "measuring", "capturing", "analyzing", "generating_pdf", "drafting_email", "ready", "sending", "sent"];
const RESOURCE_LABELS: Record<string, string> = {
  script: "JavaScript",
  stylesheet: "CSS",
  image: "Images",
  font: "Fonts",
  media: "Media",
};

function formatBytes(bytes?: number) {
  if (!bytes) return "0 KB";
  return bytes >= 1024 * 1024 ? `${(bytes / (1024 * 1024)).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export const ACTIVE = new Set(["queued", "fetching", "measuring", "capturing", "analyzing", "generating_pdf", "drafting_email", "sending"]);

export function statusTone(status: string): "ok" | "warn" | "bad" | "default" {
  if (status === "sent" || status === "ready") return "ok";
  if (status === "failed" || status === "send_failed") return "bad";
  if (ACTIVE.has(status)) return "warn";
  return "default";
}

function scoreClass(score: number | null | undefined) {
  if (score === null || score === undefined) return "text-muted";
  if (score >= 90) return "text-emerald-600";
  if (score >= 50) return "text-amber-600";
  return "text-red-600";
}

function StepList({ audit }: { audit: Audit }) {
  const failedAt = audit.status === "failed" || audit.status === "cancelled" ? ORDER.indexOf(audit.failed_step) : -1;
  const sendFailed = audit.status === "send_failed";
  const current = ORDER.indexOf(audit.status);
  return (
    <ol className="space-y-1.5 text-sm">
      {STEPS.map(([key, label]) => {
        const index = ORDER.indexOf(key);
        let state: "done" | "active" | "failed" | "todo" = "todo";
        if ((failedAt >= 0 && index === failedAt) || (sendFailed && key === "sending")) state = "failed";
        else if (failedAt >= 0) state = index < failedAt ? "done" : "todo";
        else if (key === "sending" && audit.status === "ready") state = "todo";
        else if (index < current || audit.status === "sent") state = "done";
        else if (index === current) state = "active";
        return (
          <li key={key} className="flex items-center gap-2">
            {state === "done" ? <CheckCircle2 className="h-4 w-4 text-emerald-600" /> : null}
            {state === "active" ? <Loader2 className="h-4 w-4 animate-spin text-amber-600" /> : null}
            {state === "failed" ? <XCircle className="h-4 w-4 text-red-600" /> : null}
            {state === "todo" ? <Circle className="h-4 w-4 text-muted" /> : null}
            <span className={state === "todo" ? "text-muted" : ""}>{label}</span>
          </li>
        );
      })}
    </ol>
  );
}

/** Open (new tab) or download an audit PDF; the API needs the auth header, so it is fetched as a blob. */
export async function openAuditPdf(audit: { id: string; pdf_filename?: string }, download: boolean) {
  const popup = download ? null : window.open("", "_blank");
  try {
    const res = await api<Response>(`/api/audits/${audit.id}/pdf${download ? "?download=1" : ""}`, { raw: true });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "Could not load the PDF");
    const url = URL.createObjectURL(await res.blob());
    if (popup) {
      popup.location.href = url;
    } else {
      const a = document.createElement("a");
      a.href = url;
      a.download = audit.pdf_filename || "website-audit.pdf";
      a.click();
    }
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  } catch (e) {
    popup?.close();
    toast.error((e as Error).message);
  }
}

export function AuditPanel({
  leadId,
  defaultEmail,
  hasWebsite = true,
  onChange,
}: {
  leadId: string;
  defaultEmail?: string;
  hasWebsite?: boolean;
  /** Called after the user starts, saves, or sends an audit. */
  onChange?: () => void;
}) {
  const [audit, setAudit] = useState<Audit | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [recipient, setRecipient] = useState(defaultEmail || "");
  const [autoSend, setAutoSend] = useState(false);
  const [fromAccount, setFromAccount] = useState(""); // "" = default account
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [preview, setPreview] = useState(true);
  const [busy, setBusy] = useState("");
  const [stuck, setStuck] = useState(false);
  const draftKey = useRef("");
  const onChangeRef = useRef(onChange);
  useEffect(() => {
    onChangeRef.current = onChange;
  }, [onChange]);

  // Store the audit; fill the editable draft once per audit when it stops running.
  const applyAudit = useCallback((data: Audit) => {
    setAudit(data);
    if (ACTIVE.has(data.status) || draftKey.current === data.id) return;
    draftKey.current = data.id;
    setSubject(data.email_subject || "");
    setBody(data.email_html || "");
    if (data.recipient_email) setRecipient(data.recipient_email);
    setFromAccount(data.email_account_id || "");
  }, []);

  const loadAudit = useCallback(
    async (id: string) => {
      const data = await api<Audit>(`/api/audits/${id}`);
      applyAudit(data);
      return data;
    },
    [applyAudit]
  );

  useEffect(() => {
    api<Audit[]>(`/api/audits?lead_id=${leadId}`)
      .then((list) => (list[0] ? loadAudit(list[0].id) : null))
      .catch((e) => toast.error(e.message))
      .finally(() => setLoaded(true));
  }, [leadId, loadAudit]);

  // Poll while the workflow is running.
  useEffect(() => {
    if (!audit || !ACTIVE.has(audit.status)) return;
    const timer = setTimeout(
      () =>
        loadAudit(audit.id)
          .then((data) => {
            setStuck(data.status === "queued" && Date.now() - new Date(data.created_at).getTime() > 30_000);
            if (!ACTIVE.has(data.status)) onChangeRef.current?.();
          })
          .catch(() => undefined),
      2500
    );
    return () => clearTimeout(timer);
  }, [audit, loadAudit]);

  async function run() {
    setBusy("run");
    try {
      const data = await api<Audit>(`/api/leads/${leadId}/audits`, {
        method: "POST",
        body: JSON.stringify({ recipient_email: recipient || null, auto_send: autoSend, email_account_id: fromAccount || null }),
      });
      draftKey.current = "";
      applyAudit(data);
      toast.success("Audit started");
      onChange?.();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy("");
    }
  }

  async function openPdf(download: boolean) {
    if (audit) await openAuditPdf(audit, download);
  }

  async function stopAudit() {
    if (!audit) return;
    setBusy("stop");
    try {
      applyAudit(await api<Audit>(`/api/audits/${audit.id}/cancel`, { method: "POST" }));
      toast.success("Audit stopped. Nothing was sent; you can run a new audit any time.");
      onChange?.();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy("");
    }
  }

  async function regenerate(step: "pdf" | "email") {
    if (!audit) return;
    setBusy(step);
    try {
      const data = await api<Audit>(`/api/audits/${audit.id}/regenerate-${step}`, { method: "POST" });
      if (step === "email") draftKey.current = ""; // load the new draft into the editor when it is ready
      applyAudit(data);
      toast.success(step === "pdf" ? "PDF regenerated" : "Claude is writing a new email…");
      onChange?.();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy("");
    }
  }

  async function saveDraft() {
    if (!audit) return;
    setBusy("save");
    try {
      applyAudit(
        await api<Audit>(`/api/audits/${audit.id}`, {
          method: "PATCH",
          body: JSON.stringify({ email_subject: subject, email_html: body, recipient_email: recipient }),
        })
      );
      toast.success("Draft saved");
      onChange?.();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy("");
    }
  }

  async function send() {
    if (!audit) return;
    if (!window.confirm(`Send this email with the PDF audit attached to ${recipient}?`)) return;
    setBusy("send");
    try {
      const data = await api<Audit>(`/api/audits/${audit.id}/send`, {
        method: "POST",
        body: JSON.stringify({
          recipient_email: recipient,
          email_subject: subject,
          email_html: body,
          email_account_id: fromAccount, // "" = the default account
        }),
        signal: AbortSignal.timeout(120_000),
      });
      applyAudit(data);
      toast.success(`Email sent to ${data.recipient_email}`);
      onChange?.();
    } catch (e) {
      toast.error((e as Error).message);
      loadAudit(audit.id).catch(() => undefined);
    } finally {
      setBusy("");
    }
  }

  const running = audit ? ACTIVE.has(audit.status) : false;
  const reviewable = audit && ["ready", "send_failed", "sent"].includes(audit.status);
  const findings = audit?.report?.findings || [];

  return (
    <Card className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="font-medium">Website audit & outreach</h2>
          <p className="text-sm text-muted">
            Measures the website, has Claude write a PDF audit and a personalised email, then sends both to the customer.
          </p>
        </div>
        {audit ? <Badge tone={statusTone(audit.status)}>{audit.step}</Badge> : null}
      </div>

      {!hasWebsite ? <p className="text-sm text-red-600">Add a website URL to this lead to run an audit.</p> : null}

      {hasWebsite && !running ? (
        <div className="grid gap-3 rounded-lg border border-border p-3 md:grid-cols-[1fr_auto] md:items-end">
          <div>
            <Label>Customer email</Label>
            <Input value={recipient} onChange={(e) => setRecipient(e.target.value)} placeholder="owner@business.com" />
            <label className="mt-2 flex items-center gap-2 text-sm">
              <input type="checkbox" checked={autoSend} onChange={(e) => setAutoSend(e.target.checked)} />
              Send automatically when ready (skip review)
            </label>
            <SendFrom className="mt-3" value={fromAccount} onChange={setFromAccount} hint={false} />
          </div>
          <Button onClick={run} disabled={busy === "run" || !loaded}>
            {busy === "run" ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileText className="h-4 w-4" />}
            {audit ? "Run new audit" : "Run audit"}
          </Button>
        </div>
      ) : null}

      {audit ? (
        <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
          <div className="space-y-3">
            <StepList audit={audit} />
            <p className="text-xs text-muted">Started {formatDate(audit.created_at)}</p>
            {stuck && audit.status === "queued" ? (
              <p className="rounded-md bg-amber-50 p-2 text-sm text-amber-800 dark:bg-amber-900/30 dark:text-amber-200">
                No worker has picked this up. Start the Celery worker:{" "}
                <code className="text-xs">celery -A workers.celery_app.celery_app worker --pool=solo</code> (from the repo root).
              </p>
            ) : null}
            {audit.error_message ? (
              <p className="rounded-md bg-red-50 p-2 text-sm text-red-700 dark:bg-red-900/30 dark:text-red-200">
                {audit.error_message}
              </p>
            ) : null}
            {audit.has_report && !running ? (
              <div className="flex flex-col gap-2">
                <Button variant="secondary" onClick={() => regenerate("pdf")} disabled={!!busy}>
                  {busy === "pdf" ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                  Regenerate PDF
                </Button>
                <Button variant="secondary" onClick={() => regenerate("email")} disabled={!!busy}>
                  {busy === "email" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Mail className="h-4 w-4" />}
                  Regenerate email
                </Button>
              </div>
            ) : null}
          </div>

          {reviewable ? (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-4">
                <div className={`text-4xl font-semibold ${scoreClass(audit.overall_score)}`}>
                  {audit.overall_score ?? "—"}
                  <span className="text-base text-muted">/100</span>
                </div>
                <div className="min-w-0 flex-1">
                  <p className="font-medium">{audit.headline}</p>
                  <p className="text-xs text-muted">
                    {audit.ai_provider === "anthropic" ? `Claude · ${audit.ai_model}` : `${audit.ai_provider} · ${audit.ai_model}`}
                  </p>
                </div>
                <div className="flex gap-2">
                  <Button variant="secondary" onClick={() => openPdf(false)} disabled={!audit.has_pdf}>
                    <FileText className="h-4 w-4" /> Open PDF
                  </Button>
                  <Button variant="secondary" onClick={() => openPdf(true)} disabled={!audit.has_pdf}>
                    <Download className="h-4 w-4" />
                  </Button>
                </div>
              </div>

              {audit.resources?.total_requests ? (
                <div className="flex flex-wrap gap-2 text-xs">
                  <span className="rounded-md border border-border px-2 py-1">
                    Page weight: <b>{formatBytes(audit.resources.total_bytes)}</b> in {audit.resources.total_requests} files
                  </span>
                  {Object.entries(audit.resources.by_type || {}).map(([type, bucket]) => (
                    <span key={type} className="rounded-md border border-border px-2 py-1">
                      {RESOURCE_LABELS[type] || type}: {bucket.count} · {formatBytes(bucket.bytes)}
                    </span>
                  ))}
                  {audit.resources.third_party_requests ? (
                    <span className="rounded-md border border-border px-2 py-1">Third-party files: {audit.resources.third_party_requests}</span>
                  ) : null}
                </div>
              ) : null}

              {audit.report?.first_impression ? (
                <p className="text-sm">
                  <b>First impression:</b> {audit.report.first_impression}
                </p>
              ) : null}
              {audit.report?.executive_summary ? <p className="text-sm">{audit.report.executive_summary}</p> : null}
              {findings.length ? (
                <ul className="space-y-1 text-sm">
                  {findings.slice(0, 5).map((f) => (
                    <li key={f.title} className="flex gap-2">
                      <Badge tone={f.severity === "low" ? "default" : f.severity === "medium" ? "warn" : "bad"}>{f.severity}</Badge>
                      <StatusBadge status={f.status} />
                      <span>
                        <b>{f.title}</b>
                        {f.where ? <span className="text-muted"> ({f.where})</span> : null} — {f.evidence}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : null}

              <PlatformInsights
                key={`${audit.id}:${audit.outreach_angle || ""}`}
                auditId={audit.id}
                platform={audit.platform}
                intel={audit.platform_intel}
                opportunities={audit.report?.platform_opportunities}
                angle={audit.outreach_angle}
                onSaved={() => loadAudit(audit.id).catch(() => undefined)}
              />

              <div className="space-y-3 rounded-lg border border-border p-3">
                <div className="flex items-center justify-between">
                  <h3 className="font-medium">Email (PDF attached)</h3>
                  {audit.sent_at ? (
                    <span className="flex flex-wrap items-center justify-end gap-1">
                      <Badge tone="ok">Sent {formatDate(audit.sent_at)}</Badge>
                      <TrackingBadges t={audit} sent />
                    </span>
                  ) : null}
                </div>
                <div>
                  <Label>To</Label>
                  <Input value={recipient} onChange={(e) => setRecipient(e.target.value)} />
                </div>
                <div>
                  <Label>Subject</Label>
                  <Input value={subject} onChange={(e) => setSubject(e.target.value)} />
                </div>
                <div>
                  <div className="mb-1 flex items-center justify-between">
                    <Label>Body</Label>
                    <button className="text-xs text-primary" onClick={() => setPreview(!preview)}>
                      {preview ? "Edit HTML" : "Preview"}
                    </button>
                  </div>
                  {preview ? (
                    <iframe
                      title="Email preview"
                      sandbox=""
                      className="h-64 w-full rounded-md border border-border bg-white"
                      srcDoc={`<div style="font-family:Arial,sans-serif;font-size:14px;color:#111;padding:8px">${body}</div>`}
                    />
                  ) : (
                    <Textarea rows={10} value={body} onChange={(e) => setBody(e.target.value)} className="font-mono text-xs" />
                  )}
                  <p className="mt-1 text-xs text-muted">
                    Attachment: {audit.pdf_filename} ({Math.round(audit.pdf_size / 1024)} KB). An unsubscribe link is added automatically.
                  </p>
                </div>
                <SendFrom className="max-w-md" value={fromAccount} onChange={setFromAccount} hint={false} />
                <div className="flex flex-wrap gap-2">
                  <Button variant="secondary" onClick={saveDraft} disabled={!!busy}>
                    Save draft
                  </Button>
                  <Button onClick={send} disabled={!!busy || !recipient || !subject}>
                    {busy === "send" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                    {audit.status === "sent" ? "Send again" : "Send email with PDF"}
                  </Button>
                </div>
              </div>
            </div>
          ) : running ? (
            <div className="space-y-2">
              <p className="text-sm text-muted">This usually takes 1–3 minutes. You can leave this page; the audit keeps running.</p>
              {audit.status !== "sending" ? (
                <Button variant="danger" onClick={stopAudit} disabled={busy === "stop"}>
                  {busy === "stop" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Square className="h-4 w-4" />}
                  Stop audit
                </Button>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : loaded ? (
        <p className="text-sm text-muted">No audit yet for this lead.</p>
      ) : null}
    </Card>
  );
}
