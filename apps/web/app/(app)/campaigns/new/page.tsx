"use client";

import { GmailSignatureStatus, SendFrom } from "@/components/send-from";
import { Badge, Button, Card, Input, Label, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { SAMPLE_LEADS_CSV } from "@/lib/constants";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

type LeadFile = {
  id: string;
  kind: "scrape" | "import" | "manual";
  name: string;
  status: string;
  ready_leads: number;
  detail: string;
};

function fileKey(file: LeadFile) {
  return `${file.kind}:${file.id}`;
}

function NewCampaignForm() {
  const router = useRouter();
  const params = useSearchParams();
  const fileRef = useRef<HTMLInputElement>(null);
  const queryImport = params.get("importId") || "";
  const queryScrape = params.get("scrapeId") || "";
  const queryManual = params.get("manual") === "1";

  const [mode, setMode] = useState<"leads" | "upload">(queryImport ? "upload" : "leads");
  const [name, setName] = useState("Outreach campaign");
  const [accounts, setAccounts] = useState<any[]>([]);
  const [templates, setTemplates] = useState<any[]>([]);
  const [files, setFiles] = useState<LeadFile[]>([]);
  const [fileKeyValue, setFileKeyValue] = useState("");
  const [accountId, setAccountId] = useState("");
  const [templateId, setTemplateId] = useState("");
  const [subject, setSubject] = useState("Quick idea for {{business_name}}");
  const [body, setBody] = useState(
    "Hi {{business_name}},\n\nI was looking at your {{category}} business in {{city}} and wanted to reach out.\n\n{{personalized_message}}\n\nWould you be open to a short conversation this week?\n\n{{sender_name}}\n{{sender_company}}"
  );
  const [copyMode, setCopyMode] = useState<"template" | "custom">("template");
  // "audit": analyse each website, PDF audit + Claude email from the findings. "template": same email for everyone.
  const [outreachMode, setOutreachMode] = useState<"audit" | "template">("audit");
  // Audit campaigns: send each email as soon as its audit is ready, or keep it for review first.
  const [autoSend, setAutoSend] = useState(true);
  // Put the sending Gmail's own signature (read from Gmail) under each email of this campaign.
  const [addSignature, setAddSignature] = useState(true);
  const [followups, setFollowups] = useState(true);
  const [scheduledAt, setScheduledAt] = useState("");
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [preview, setPreview] = useState<any>(null);
  const [filesError, setFilesError] = useState("");

  const selected = files.find((file) => fileKey(file) === fileKeyValue) || null;
  const connected = accounts.filter((a) => a.status === "connected");
  const fileBusy = Boolean(selected && selected.kind === "import" && ["running", "queued", "processing"].includes(selected.status));
  const audit = outreachMode === "audit";
  const hasEmailCopy = audit || (copyMode === "template" ? Boolean(templateId) : Boolean(subject.trim() && body.trim()));
  const canDraft = Boolean(selected && selected.ready_leads > 0 && !fileBusy && hasEmailCopy);
  const canSend = Boolean(connected.length && canDraft);

  async function loadFiles(preferKey?: string) {
    const rows = await api<LeadFile[]>("/api/leads/files");
    setFilesError("");
    setFiles(rows);
    if (preferKey && rows.some((file) => fileKey(file) === preferKey)) {
      setFileKeyValue(preferKey);
      return;
    }
    if (queryImport) setFileKeyValue(`import:${queryImport}`);
    else if (queryScrape) setFileKeyValue(`scrape:${queryScrape}`);
    else if (queryManual) setFileKeyValue("manual:manual");
    else setFileKeyValue((current) => current || "");
  }

  useEffect(() => {
    api<any[]>("/api/email-accounts").then((rows) => {
      setAccounts(rows);
      // Send from stays on "Automatic" (rotation across connected inboxes) unless you pick one.
    });
    api<any[]>("/api/templates").then((rows) => {
      setTemplates(rows);
      if (rows[0]) {
        setTemplateId(rows[0].id);
        applyTemplate(rows[0]);
      }
    });
    loadFiles().catch((e) => {
      setFilesError(e.message || "Could not load lead files");
      toast.error(e.message);
    });
  }, [queryImport, queryScrape, queryManual]);

  useEffect(() => {
    if (selected) setName((cur) => (cur === "Outreach campaign" || cur.startsWith("Campaign · ") ? `Campaign · ${selected.name}` : cur));
  }, [selected?.id]);

  function payload() {
    return {
      name,
      email_account_id: accountId || undefined, // none: the default account
      outreach_mode: outreachMode,
      add_signature: addSignature,
      followups,
      template_id: !audit && copyMode === "template" ? templateId || undefined : undefined,
      lead_ids: [],
      import_batch_id: selected?.kind === "import" ? selected.id : undefined,
      scraping_job_id: selected?.kind === "scrape" ? selected.id : undefined,
      use_manual: selected?.kind === "manual",
      require_approval: outreachMode === "audit" ? !autoSend : false,
      skip_previously_contacted: mode !== "leads",
      subject: !audit && copyMode === "custom" ? subject.trim() : undefined,
      html_body: !audit && copyMode === "custom" ? body.trim() : undefined,
      text_body: !audit && copyMode === "custom" ? body.trim() : undefined,
      scheduled_at: scheduledAt ? new Date(scheduledAt).toISOString() : undefined,
    };
  }

  function applyTemplate(template: any) {
    if (!template) return;
    setSubject(template.subject || "");
    const raw = String(template.html_body || "")
      .replace(/<br\s*\/?>/gi, "\n")
      .replace(/<\/p>/gi, "\n")
      .replace(/<[^>]+>/g, "")
      .replace(/&nbsp;/g, " ")
      .trim();
    setBody(raw || template.text_body || "");
  }

  useEffect(() => {
    if (!canSend || !selected) {
      setPreview(null);
      return;
    }
    api("/api/campaigns/preview", { method: "POST", body: JSON.stringify(payload()) })
      .then(setPreview)
      .catch(() => setPreview(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canSend, selected?.id, selected?.ready_leads, mode]);

  async function importCsv(file: File) {
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await api<{ import_id: string; filename: string; new: number; duplicates: number; invalid: number }>(
        "/api/leads/import",
        { method: "POST", body: fd }
      );
      const parts = [`${res.new} new`];
      if (res.duplicates) parts.push(`${res.duplicates} already in your leads (included)`);
      if (res.invalid) parts.push(`${res.invalid} skipped (no business name or email)`);
      toast.success(`Uploaded ${res.filename}: ${parts.join(", ")}`);
      setMode("upload");
      await loadFiles(`import:${res.import_id}`);
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  function downloadSample() {
    const blob = new Blob([SAMPLE_LEADS_CSV], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "sample-leads.csv";
    a.click();
  }

  async function create(sendNow: boolean) {
    setSaving(true);
    try {
      const campaign = await api<{ id: string }>("/api/campaigns", {
        method: "POST",
        body: JSON.stringify({ ...payload(), send_now: sendNow }),
      });
      toast.success(sendNow ? (audit ? "Analysing websites and sending" : "Writing emails and sending") : "Campaign saved");
      router.push(`/campaigns/${campaign.id}`);
    } finally {
      setSaving(false);
    }
  }

  const choosable = files.filter((file) => (mode === "upload" ? file.kind === "import" : true));

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">Send a campaign</h1>
        <p className="text-sm text-muted">
          Choose existing leads for a follow-up, or upload a CSV. New scrapes still send on their own.
        </p>
      </div>

      {!connected.length ? (
        <Card className="space-y-3 border-amber-300">
          <h2 className="font-medium">{accounts.some((a) => a.status === "expired") ? "Reconnect Gmail" : "Connect Gmail first"}</h2>
          <p className="text-sm text-muted">
            {accounts.some((a) => a.status === "expired")
              ? "The saved Gmail login stopped working. Replace its App Password, then you can schedule or send this campaign."
              : "Campaigns need a connected Gmail (App Password)."}
          </p>
          <Link href="/email-accounts">
            <Button>{accounts.some((a) => a.status === "expired") ? "Fix it in Email setup" : "Connect Gmail in Email setup"}</Button>
          </Link>
        </Card>
      ) : null}

      <Card className="space-y-4">
        <div className="flex flex-wrap gap-2">
          <Button variant={mode === "leads" ? "primary" : "secondary"} onClick={() => setMode("leads")}>
            Choose from leads
          </Button>
          <Button
            variant={mode === "upload" ? "primary" : "secondary"}
            onClick={() => {
              setMode("upload");
              if (selected?.kind !== "import") setFileKeyValue("");
            }}
          >
            Upload CSV / Excel
          </Button>
        </div>

        {mode === "leads" ? (
          <div>
            <Label>Lead file</Label>
            <select
              className="mt-1 w-full rounded-md border border-border bg-card px-3 py-2 text-sm"
              value={fileKeyValue}
              onChange={(e) => setFileKeyValue(e.target.value)}
            >
              <option value="">Select a file…</option>
              {choosable.map((file) => (
                <option key={fileKey(file)} value={fileKey(file)}>
                  {file.name} · {file.ready_leads} ready · {file.kind === "scrape" ? "Scrape" : file.kind === "import" ? "Upload" : "Manual"}
                </option>
              ))}
            </select>
            {filesError ? (
              <p className="mt-2 text-sm text-danger">
                {filesError}{" "}
                <button className="text-primary" onClick={() => loadFiles().catch((e) => setFilesError(e.message))}>
                  Retry
                </button>
              </p>
            ) : selected ? (
              <p className="mt-2 text-sm text-muted">
                Follow-up to {selected.ready_leads} leads in this file
                {selected.detail ? ` · ${selected.detail}` : ""}.
              </p>
            ) : choosable.length === 0 ? (
              <p className="mt-2 text-sm text-muted">No lead files yet. Upload a CSV or start a scrape first.</p>
            ) : (
              <p className="mt-2 text-sm text-muted">Pick the file you want to follow up with.</p>
            )}
          </div>
        ) : (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-2">
              <label className="inline-flex cursor-pointer items-center rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground">
                {uploading ? "Uploading…" : "Choose CSV or Excel"}
                <input
                  ref={fileRef}
                  type="file"
                  accept=".csv,text/csv,.xlsx,.xls,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                  className="hidden"
                  disabled={uploading}
                  onChange={(e) => e.target.files?.[0] && importCsv(e.target.files[0]).catch((err) => toast.error(err.message))}
                />
              </label>
              <Button variant="ghost" onClick={downloadSample}>
                Sample CSV
              </Button>
            </div>
            {files.some((file) => file.kind === "import") ? (
              <div>
                <Label>Or use an uploaded file</Label>
                <select
                  className="mt-1 w-full rounded-md border border-border bg-card px-3 py-2 text-sm"
                  value={selected?.kind === "import" ? fileKeyValue : ""}
                  onChange={(e) => setFileKeyValue(e.target.value)}
                >
                  <option value="">Select an upload…</option>
                  {files
                    .filter((file) => file.kind === "import")
                    .map((file) => (
                      <option key={fileKey(file)} value={fileKey(file)}>
                        {file.name} · {file.ready_leads} ready
                      </option>
                    ))}
                </select>
              </div>
            ) : (
              <p className="text-sm text-muted">Upload a CSV to send from those rows only.</p>
            )}
            {selected?.kind === "import" ? (
              <div className="rounded-xl border border-border px-4 py-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{selected.name}</span>
                  <Badge>Upload</Badge>
                </div>
                <div className="text-sm text-muted">{selected.ready_leads} ready</div>
              </div>
            ) : null}
          </div>
        )}

        <div>
          <Label>Campaign name</Label>
          <Input value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <Label>Website analysis</Label>
          <div className="mt-1 grid gap-2 sm:grid-cols-2">
            {(
              [
                ["audit", "Analyse website + personalised email", "Each lead's website is audited (screenshots, Claude CRO review), a PDF report is made, and Claude writes an email from that site's own findings. Sent with the PDF attached."],
                ["template", "Template email, no analysis", "The same subject and body for every lead (with an optional AI-written paragraph). Faster; no PDF."],
              ] as const
            ).map(([value, title, hint]) => (
              <button
                key={value}
                type="button"
                onClick={() => setOutreachMode(value)}
                className={`rounded-xl border px-4 py-3 text-left text-sm ${outreachMode === value ? "border-primary ring-2 ring-primary/30" : "border-border"}`}
              >
                <div className="font-medium">{title}</div>
                <div className="mt-1 text-xs text-muted">{hint}</div>
              </button>
            ))}
          </div>
          {audit ? (
            <div className="mt-3 space-y-2 rounded-xl border border-border p-3">
              <div className="text-sm font-medium">Automation</div>
              {(
                [
                  [true, "Audit and send automatically", "Each lead is audited, gets its PDF and personalised email, and the email is sent right away. No clicks needed."],
                  [false, "Audit automatically, I review each email", "Audits, PDFs and emails are prepared automatically; you read each email on the campaign page and send it (one by one or all at once)."],
                ] as const
              ).map(([value, title, hint]) => (
                <label key={title} className="flex cursor-pointer items-start gap-2 text-sm">
                  <input type="radio" className="mt-1" checked={autoSend === value} onChange={() => setAutoSend(value)} />
                  <span>
                    <span className="font-medium">{title}</span>
                    <span className="block text-xs text-muted">{hint}</span>
                  </span>
                </label>
              ))}
              <p className="text-xs text-muted">
                Leads without a website are skipped. Audits run one after another (about 2–3 minutes each) within the daily limit.
              </p>
            </div>
          ) : null}
          <div className="mt-3 rounded-xl border border-border p-3">
            <label className="flex cursor-pointer items-start gap-2 text-sm">
              <input type="checkbox" className="mt-1" checked={addSignature} onChange={(e) => setAddSignature(e.target.checked)} />
              <span>
                <span className="font-medium">Add my Gmail signature</span>
                <span className="block text-xs text-muted">
                  Each email gets the signature you set in the sending Gmail itself (read from Gmail, never another
                  account&apos;s).
                </span>
                <GmailSignatureStatus accountId={accountId} />
              </span>
            </label>
            <label className="mt-3 flex cursor-pointer items-start gap-2 text-sm">
              <input type="checkbox" className="mt-1" checked={followups} onChange={(e) => setFollowups(e.target.checked)} />
              <span>
                <span className="font-medium">Send follow-ups</span>
                <span className="block text-xs text-muted">
                  If a lead doesn&apos;t reply, short follow-ups go out in the same thread (timing in AI &amp; settings →
                  Sending). They stop as soon as the lead replies.
                </span>
              </span>
            </label>
          </div>
        </div>
        {accounts.length ? (
          <div className="grid gap-3 sm:grid-cols-2">
            <SendFrom value={accountId} onChange={setAccountId} />
            {audit ? null : (
              <div>
                <Label>Email</Label>
                <div className="mb-2 flex flex-wrap gap-2">
                  <Button type="button" variant={copyMode === "template" ? "primary" : "secondary"} onClick={() => setCopyMode("template")}>
                    Use existing template
                  </Button>
                  <Button type="button" variant={copyMode === "custom" ? "primary" : "secondary"} onClick={() => setCopyMode("custom")}>
                    Write custom email
                  </Button>
                </div>
                {copyMode === "template" ? (
                  <select
                    className="w-full rounded-md border border-border bg-card px-3 py-2 text-sm"
                    value={templateId}
                    onChange={(e) => {
                      const next = e.target.value;
                      setTemplateId(next);
                      applyTemplate(templates.find((t) => t.id === next));
                    }}
                  >
                    <option value="">Select template</option>
                    {templates.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.name}
                      </option>
                    ))}
                  </select>
                ) : (
                  <p className="text-xs text-muted">Write your own subject and body below. It is saved as a reusable campaign email.</p>
                )}
              </div>
            )}
          </div>
        ) : null}

        <div>
          <Label>Start date and time</Label>
          <Input type="datetime-local" value={scheduledAt} onChange={(e) => setScheduledAt(e.target.value)} />
          <p className="mt-1 text-xs text-muted">Leave empty to start immediately. Scheduled campaigns wait until this time.</p>
        </div>
        {audit ? null : copyMode === "custom" || !templateId ? (
          <>
            <div>
              <Label>Email subject</Label>
              <Input value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="Quick idea for {{business_name}}" />
            </div>
            <div>
              <Label>Write the email</Label>
              <Textarea rows={10} value={body} onChange={(e) => setBody(e.target.value)} />
              <p className="mt-1 text-xs text-muted">
                {mode === "leads"
                  ? "Follow-up: leads in this file can be emailed again."
                  : "This goes to every ready lead in the uploaded file, up to the daily send limit."}
              </p>
            </div>
          </>
        ) : (
          <div className="rounded-xl border border-border px-4 py-3 text-sm text-muted">
            Using template: {templates.find((t) => t.id === templateId)?.name || "selected template"}
          </div>
        )}
      </Card>

      <Card className="space-y-3">
        {preview ? (
          <p className="text-sm">
            This will {audit ? "audit and email" : "send"} <span className="font-medium">{preview.will_send}</span>{" "}
            {audit ? "leads (those with a website)" : "emails"}
            {preview.previously_contacted ? ` · ${preview.previously_contacted} already contacted` : ""}
            {preview.daily_limit ? ` · daily limit ${preview.daily_limit}` : ""}.
          </p>
        ) : (
          <p className="text-sm text-muted">
            {mode === "leads" ? "Choose a lead file to follow up." : "Upload or select a CSV with ready leads."}
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          <Button disabled={!canSend || saving} onClick={() => create(true).catch((e) => toast.error(e.message))}>
            {saving
              ? "Starting…"
              : scheduledAt
                ? "Schedule campaign"
                : audit
                  ? autoSend
                    ? "Analyse websites and send"
                    : "Analyse websites (I'll review)"
                  : "Write emails and send"}
          </Button>
          <Button variant="secondary" disabled={!canDraft || saving} onClick={() => create(false).catch((e) => toast.error(e.message))}>
            Save as draft
          </Button>
        </div>
      </Card>
    </div>
  );
}

export default function NewCampaignPage() {
  return (
    <Suspense>
      <NewCampaignForm />
    </Suspense>
  );
}
