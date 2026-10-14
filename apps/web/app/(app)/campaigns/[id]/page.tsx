"use client";

import { OutreachTable } from "@/components/outreach";
import { useEmailAccounts } from "@/components/send-from";
import { Badge, Button, Card, Input, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { CAMPAIGN_LEAD_STATUS, CAMPAIGN_STATUS } from "@/lib/constants";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";

function tone(status: string): "ok" | "warn" | "bad" | "default" {
  if (status === "running" || status === "sent" || status === "completed") return "ok";
  if (status === "paused" || status === "generating" || status === "pending" || status === "auditing") return "warn";
  if (status === "failed") return "bad";
  return "default";
}

export default function CampaignDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [campaign, setCampaign] = useState<any>(null);
  const [leads, setLeads] = useState<any[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState({ subject: "", html_body: "" });
  const [busy, setBusy] = useState(false);
  const accounts = useEmailAccounts();

  async function load() {
    const [c, l] = await Promise.all([api(`/api/campaigns/${id}`), api<any[]>(`/api/campaigns/${id}/leads`)]);
    setCampaign(c);
    setLeads(l);
  }

  useEffect(() => {
    load().catch((e) => toast.error(e.message));
    const t = setInterval(() => load().catch(() => null), 4000);
    return () => clearInterval(t);
  }, [id]);

  async function sendNow() {
    setBusy(true);
    try {
      await api(`/api/campaigns/${id}/send`, { method: "POST" });
      toast.success("Writing emails and sending");
      await load();
    } finally {
      setBusy(false);
    }
  }

  async function act(path: string, label: string) {
    setBusy(true);
    try {
      await api(`/api/campaigns/${id}/${path}`, { method: "POST" });
      toast.success(label);
      await load();
    } finally {
      setBusy(false);
    }
  }

  async function changeAccount(choice: string) {
    // "mode:auto" / "mode:health" = rotation; an account id = everything from that account
    const mode = choice.startsWith("mode:") ? choice.slice(5) : "single";
    setBusy(true);
    try {
      await api(`/api/campaigns/${id}/email-account`, {
        method: "POST",
        body: JSON.stringify(mode === "single" ? { email_account_id: choice, mode } : { mode }),
      });
      const address = accounts?.find((a) => a.id === choice)?.email_address;
      toast.success(
        mode === "single" ? `The rest of this campaign sends from ${address}` : "The rest of this campaign rotates across your inboxes"
      );
      await load();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!campaign) return <p className="text-muted">Loading…</p>;

  const writing = leads.some((item) => ["pending", "generating"].includes(item.status));
  const sent = leads.filter((item) => item.status === "sent").length;
  const failed = leads.filter((item) => item.status === "failed").length;
  const sending = campaign.status === "running";
  const audit = campaign.outreach_mode === "audit";
  const auditing = leads.filter((item) => item.status === "auditing").length;
  const reviewable = leads.filter((item) => item.status === "ready" && item.outreach?.audit_id);

  async function sendReviewed(items: any[]) {
    setBusy(true);
    let ok = 0;
    try {
      for (const item of items) {
        try {
          await api(`/api/audits/${item.outreach.audit_id}/send`, { method: "POST", body: "{}", signal: AbortSignal.timeout(120_000) });
          ok += 1;
        } catch (e) {
          toast.error(`${item.business_name}: ${(e as Error).message}`);
        }
      }
      if (ok) toast.success(`Sent ${ok} email${ok === 1 ? "" : "s"} with the PDF attached`);
      await load();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">{campaign.name}</h1>
          <p className="text-sm text-muted">
            {campaign.file_name ? `${campaign.file_name} · ` : ""}
            {audit
              ? `Website analysis + personalised emails · ${campaign.require_approval ? "you review each email" : "sent automatically"} · `
              : ""}
            {sent} sent{audit ? ` · ${auditing} auditing` : ""} · {campaign.pending_count} pending · {failed} failed
            {campaign.daily_limit ? ` · ${campaign.sent_today}/${campaign.daily_limit} today` : ""}
            {campaign.scheduled_at ? ` · starts ${new Date(campaign.scheduled_at).toLocaleString()}` : ""}
          </p>
        </div>
        <Badge tone={tone(campaign.status)}>{CAMPAIGN_STATUS[campaign.status] || campaign.status}</Badge>
      </div>

      <div className="flex flex-wrap gap-2">
        {sending ? (
          <Button variant="secondary" disabled={busy} onClick={() => act("pause", "Paused")}>
            Pause
          </Button>
        ) : campaign.status === "scheduled" ? (
          <Button disabled={busy} onClick={() => sendNow().catch((e) => toast.error(e.message))}>
            {busy ? "Starting…" : "Start now"}
          </Button>
        ) : campaign.status !== "completed" && campaign.status !== "stopped" ? (
          <Button disabled={busy} onClick={() => sendNow().catch((e) => toast.error(e.message))}>
            {busy ? "Starting…" : audit ? "Analyse websites and send" : writing ? "Keep writing and send" : "Write emails and send"}
          </Button>
        ) : null}
        {campaign.status === "paused" ? (
          <Button variant="secondary" disabled={busy} onClick={() => act("resume", "Resumed")}>
            Resume
          </Button>
        ) : null}
        {audit && reviewable.length ? (
          <Button disabled={busy} onClick={() => sendReviewed(reviewable)}>
            Send all reviewed ({reviewable.length})
          </Button>
        ) : null}
        {["running", "paused", "draft", "scheduled"].includes(campaign.status) ? (
          <Button variant="danger" disabled={busy} onClick={() => act("stop", "Stopped")}>
            Stop
          </Button>
        ) : null}
      </div>
      <label className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-muted">Sending from</span>
        <select
          className="rounded-md border border-border bg-card px-2 py-1 text-sm"
          value={
            campaign.rotation?.mode && campaign.rotation.mode !== "single"
              ? `mode:${campaign.rotation.mode}`
              : campaign.email_account_id || ""
          }
          disabled={busy || accounts === null || ["completed", "stopped"].includes(campaign.status)}
          onChange={(e) => changeAccount(e.target.value)}
        >
          <option value="mode:auto">Automatic rotation (by each inbox&apos;s room left)</option>
          <option value="mode:health">Health-based rotation (healthier inboxes first)</option>
          {campaign.rotation?.mode === "selected" ? (
            <option value="mode:selected">Selected accounts ({campaign.rotation.account_ids.length})</option>
          ) : null}
          {(accounts || [])
            .filter((a) => a.status === "connected" || a.id === campaign.email_account_id)
            .map((a) => (
              <option key={a.id} value={a.id}>
                {a.email_address} · {a.remaining_today} of {a.limit_today ?? a.daily_limit} left today
                {a.status === "connected" ? "" : " (not connected)"}
              </option>
            ))}
        </select>
        <span className="text-xs text-muted">Emails not sent yet switch to the account you pick.</span>
      </label>
      {campaign.paused_reason ? (
        <p className="text-sm text-danger">
          {campaign.paused_reason === "no_eligible_inbox"
            ? campaign.error_message || "Paused: no eligible inbox can send right now."
            : campaign.paused_reason.replaceAll("_", " ")}
        </p>
      ) : null}
      <AccountReport campaignId={id} refreshKey={campaign.sent_count} />
      {writing && sending && !audit ? <p className="text-sm text-muted">Writing personalized emails, then sending them one by one.</p> : null}
      {audit && sending ? (
        <p className="text-sm text-muted">
          Auditing each website, building its PDF and writing its email, one lead after another within the daily limit.
          {campaign.require_approval ? " Emails wait here for you: open a lead (View) to read and edit its email, then Send." : ""}
        </p>
      ) : null}

      {audit ? (
        <OutreachTable
          leads={leads.map((item) => ({
            id: item.lead_id,
            business_name: item.business_name || "—",
            contact_name: item.contact_name,
            primary_email: item.recipient_email || "",
            website_url: item.website_url || "",
            city: item.city,
            outreach: item.outreach,
          }))}
          actions={(lead) => {
            const item = leads.find((row) => row.lead_id === lead.id);
            if (!item) return null;
            return (
              <span className="mr-2 inline-flex items-center gap-2 align-middle">
                <span title={item.skip_reason || item.error_message || ""}>
                  <Badge tone={tone(item.status)}>
                    {CAMPAIGN_LEAD_STATUS[item.status] || item.status}
                    {item.skip_reason ? `: ${item.skip_reason.replaceAll("_", " ")}` : ""}
                  </Badge>
                </span>
                {item.status === "ready" && item.outreach?.audit_id ? (
                  <Button disabled={busy} onClick={() => sendReviewed([item])}>
                    Send
                  </Button>
                ) : null}
                {["pending", "queued", "approved"].includes(item.status) ? (
                  <Button variant="ghost" onClick={() => api(`/api/campaigns/${id}/leads/${item.id}/skip`, { method: "POST" }).then(load)}>
                    Skip
                  </Button>
                ) : null}
              </span>
            );
          }}
        />
      ) : null}

      <div className={audit ? "hidden" : "space-y-3"}>
        {leads.map((item) => (
          <Card key={item.id} className="space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="font-medium">{item.business_name}</div>
                <div className="text-sm text-muted">
                  {item.recipient_email || "no email"}
                  {campaign.file_name ? ` · ${campaign.file_name}` : ""}
                </div>
              </div>
              <div className="flex items-center gap-2">
                <Badge tone={tone(item.status)}>{CAMPAIGN_LEAD_STATUS[item.status] || item.status}</Badge>
                {item.lead_id ? (
                  <Link href={`/leads/${item.lead_id}`}>
                    <Button variant="secondary">View</Button>
                  </Link>
                ) : null}
              </div>
            </div>
            {editing === item.id ? (
              <>
                <Input value={draft.subject} onChange={(e) => setDraft({ ...draft, subject: e.target.value })} />
                <Textarea rows={6} value={draft.html_body} onChange={(e) => setDraft({ ...draft, html_body: e.target.value })} />
                <Button
                  onClick={async () => {
                    await api(`/api/campaigns/${id}/leads/${item.id}`, { method: "PATCH", body: JSON.stringify(draft) });
                    setEditing(null);
                    load();
                  }}
                >
                  Save
                </Button>
              </>
            ) : (
              <>
                <div className="text-sm font-medium">{item.subject || "Email is being written…"}</div>
                {item.html_body ? (
                  <div className="prose max-w-none text-sm" dangerouslySetInnerHTML={{ __html: item.html_body }} />
                ) : null}
              </>
            )}
            {item.status !== "sent" ? (
              <div className="flex gap-2">
                <Button
                  variant="secondary"
                  onClick={() => {
                    setEditing(item.id);
                    setDraft({ subject: item.subject, html_body: item.html_body });
                  }}
                >
                  Edit
                </Button>
                <Button variant="ghost" onClick={() => api(`/api/campaigns/${id}/leads/${item.id}/skip`, { method: "POST" }).then(load)}>
                  Skip
                </Button>
              </div>
            ) : null}
            {item.error_message ? <p className="text-sm text-danger">{item.error_message}</p> : null}
          </Card>
        ))}
        {leads.length === 0 ? <p className="text-muted">No leads in this campaign.</p> : null}
      </div>
    </div>
  );
}

type Report = {
  rotation: { mode: string; account_ids: string[] };
  accounts: {
    account_id: string;
    email_address: string;
    status: string;
    attempted: number;
    sent: number;
    failed: number;
    followups_sent: number;
    bounced: number;
    replied: number;
    failures: Record<string, number>;
  }[];
  overall: { leads: number; sent: number; failed: number; skipped: number; pending: number };
};

/** Which account sent what for this campaign, and the overall progress. */
function AccountReport({ campaignId, refreshKey }: { campaignId: string; refreshKey: number }) {
  const [report, setReport] = useState<Report | null>(null);
  useEffect(() => {
    api<Report>(`/api/campaigns/${campaignId}/accounts`)
      .then(setReport)
      .catch(() => setReport(null));
  }, [campaignId, refreshKey]);
  if (!report) return null;
  const o = report.overall;
  return (
    <Card className="space-y-2 text-sm">
      <div className="font-medium">Sending by account</div>
      <p className="text-xs text-muted">
        {o.leads} leads · {o.sent} sent · {o.failed} failed · {o.skipped} skipped · {o.pending} still pending
      </p>
      {report.accounts.length ? (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-muted">
              <tr>
                <th className="py-1 pr-2">Account</th>
                <th className="py-1 pr-2">Status</th>
                <th className="py-1 pr-2">Attempted</th>
                <th className="py-1 pr-2">Sent</th>
                <th className="py-1 pr-2">Follow-ups</th>
                <th className="py-1 pr-2">Failed</th>
                <th className="py-1 pr-2">Bounced</th>
                <th className="py-1">Replied</th>
              </tr>
            </thead>
            <tbody>
              {report.accounts.map((a) => (
                <tr key={a.account_id} className="border-t border-border">
                  <td className="py-1 pr-2">{a.email_address}</td>
                  <td className="py-1 pr-2">{a.status}</td>
                  <td className="py-1 pr-2">{a.attempted}</td>
                  <td className="py-1 pr-2">{a.sent}</td>
                  <td className="py-1 pr-2">{a.followups_sent}</td>
                  <td className="py-1 pr-2" title={Object.entries(a.failures).map(([k, n]) => `${n} ${k}`).join(", ")}>
                    {a.failed}
                  </td>
                  <td className="py-1 pr-2">{a.bounced}</td>
                  <td className="py-1">{a.replied}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-xs text-muted">Nothing sent yet.</p>
      )}
    </Card>
  );
}
