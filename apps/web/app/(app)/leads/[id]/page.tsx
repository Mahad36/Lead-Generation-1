"use client";

import { AuditPanel } from "@/components/audit-panel";
import { LeadConversations, LeadFollowups } from "@/components/conversation";
import { OutreachStepper } from "@/components/outreach";
import { Badge, Button, Card, Input } from "@/components/ui";
import { api } from "@/lib/api";
import { LEAD_SOURCE, LEAD_STATUS } from "@/lib/constants";
import { formatDate, formatScore } from "@/lib/utils";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";

export default function LeadDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [lead, setLead] = useState<any>(null);
  const [contact, setContact] = useState("");

  async function load() {
    const data = await api<any>(`/api/leads/${id}`);
    setLead(data);
    setContact(data.contact_name || "");
  }

  async function saveContact() {
    await api(`/api/leads/${id}`, { method: "PATCH", body: JSON.stringify({ contact_name: contact.trim() }) });
    toast.success("Contact name saved. It is used in the next email Claude writes.");
    await load();
  }

  useEffect(() => {
    load().catch((e) => toast.error(e.message));
  }, [id]);

  if (!lead) return <p className="text-muted">Loading…</p>;
  const analysis = lead.website?.analysis;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">{lead.business_name}</h1>
          <p className="text-sm text-muted">
            {lead.category} · {lead.city}, {lead.country}
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            <Badge tone={lead.status === "ready" ? "ok" : lead.status === "processing" ? "warn" : lead.status === "failed" ? "bad" : "default"}>
              {LEAD_STATUS[lead.status as keyof typeof LEAD_STATUS] || lead.status}
            </Badge>
            <Badge>{LEAD_SOURCE[lead.source as keyof typeof LEAD_SOURCE] || lead.source}</Badge>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {lead.file_kind && lead.file_id ? (
            <Button variant="secondary" onClick={() => router.push(`/leads?kind=${lead.file_kind}&id=${lead.file_id}`)}>
              View file
            </Button>
          ) : null}
          <Button onClick={async () => { await api(`/api/leads/${id}/analyze`, { method: "POST" }); toast.success("Analysis queued"); }}>
            Analyze website
          </Button>
          <Button
            variant="secondary"
            disabled={lead.status === "processing" || !lead.primary_email || lead.status === "no_email"}
            onClick={() =>
              router.push(
                lead.import_batch_id
                  ? `/campaigns/new?importId=${lead.import_batch_id}`
                  : lead.scraping_job_id
                    ? `/campaigns/new?scrapeId=${lead.scraping_job_id}`
                    : "/campaigns/new?manual=1"
              )
            }
          >
            Email this lead
          </Button>
          <Button
            variant="danger"
            onClick={async () => {
              await api(`/api/leads/${id}/do-not-contact`, { method: "POST" });
              toast.success("Marked do not contact");
              load();
            }}
          >
            Do not contact
          </Button>
        </div>
      </div>
      <LeadFollowups leadId={id} />
      <LeadConversations leadId={id} />
      {lead.outreach ? (
        <Card className="space-y-2">
          <h2 className="font-medium">Outreach workflow</h2>
          <OutreachStepper o={lead.outreach} />
        </Card>
      ) : null}
      {lead.file_name ? (
        <Card className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="font-medium">File</h2>
            <p className="text-sm text-muted">{lead.file_name}</p>
          </div>
          {lead.file_kind && lead.file_id ? (
            <Button variant="secondary" onClick={() => router.push(`/leads?kind=${lead.file_kind}&id=${lead.file_id}`)}>
              Open file
            </Button>
          ) : null}
        </Card>
      ) : null}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <h2 className="mb-3 font-medium">Business</h2>
          <dl className="space-y-2 text-sm">
            <div className="flex items-center gap-2">
              <span className="whitespace-nowrap">Contact name:</span>
              <Input className="h-8 max-w-xs" value={contact} placeholder="e.g. Sarah Khan" onChange={(e) => setContact(e.target.value)} />
              <Button
                variant="secondary"
                disabled={contact.trim() === (lead.contact_name || "")}
                onClick={() => saveContact().catch((e) => toast.error(e.message))}
              >
                Save
              </Button>
            </div>
            <div>Address: {lead.address || "—"}</div>
            <div>Phone: {lead.primary_phone || "—"}</div>
            <div>Job: {lead.file_name || "—"}</div>
            <div>Location: {[lead.address, lead.city, lead.country].filter(Boolean).join(", ") || "—"}</div>
            <div>Category: {lead.category || "—"}</div>
            <div>Email: {lead.primary_email || "—"}</div>
            <div>Email status: {lead.email_status || "—"}</div>
            <div>Campaign status: {lead.campaign_status || "not in a campaign"}</div>
            <div>Analysis status: {lead.website?.analysis?.status || (lead.website ? "completed" : lead.status)}</div>
            <div>Platform: {lead.platform || lead.website?.platform_name || "—"}</div>
            <div>
              Website: {lead.website_url ? <a className="text-primary" href={lead.website_url} target="_blank">{lead.website_url}</a> : "—"}
            </div>
            <div>
              Maps: {lead.google_maps_url ? <a className="text-primary" href={lead.google_maps_url} target="_blank">Open</a> : "—"}
            </div>
            <div>
              Lead score: {formatScore(lead.lead_score)} · Website score: {formatScore(lead.website_score)}
            </div>
          </dl>
        </Card>
        <Card>
          <h2 className="mb-3 font-medium">Website</h2>
          {lead.website ? (
            <div className="space-y-2 text-sm">
              <div>Platform: {lead.website.platform_name} ({Math.round((lead.website.platform_confidence || 0) * 100)}%)</div>
              <div>HTTPS: {lead.website.https ? "Yes" : "No"} · Reachable: {lead.website.reachable ? "Yes" : "No"}</div>
              <div>Analyzed: {formatDate(lead.website.last_analyzed_at)}</div>
              <div>Title: {lead.website.title || "—"}</div>
            </div>
          ) : (
            <p className="text-sm text-muted">No website processed yet.</p>
          )}
        </Card>
      </div>
      <AuditPanel
        leadId={id}
        defaultEmail={lead.primary_email}
        hasWebsite={Boolean(lead.website_url)}
        onChange={() => load().catch(() => undefined)}
      />
      <Card>
        <h2 className="mb-3 font-medium">Analysis</h2>
        {analysis ? (
          <div className="space-y-3 text-sm">
            <p>{analysis.summary || analysis.business_summary}</p>
            <div>
              <strong>Issues</strong>
              <ul className="list-disc pl-5">
                {(analysis.issues || []).map((i: any) => (
                  <li key={i.code || i.title}>{i.title} — {i.evidence}</li>
                ))}
              </ul>
            </div>
            <div>
              <strong>Opportunities</strong>
              <ul className="list-disc pl-5">
                {(analysis.opportunities || []).map((i: any) => (
                  <li key={i.code || i.title}>{i.title}</li>
                ))}
              </ul>
            </div>
            <div>Recommended: {(analysis.recommended_services || []).join(", ") || "—"}</div>
          </div>
        ) : (
          <p className="text-sm text-muted">No analysis stored.</p>
        )}
      </Card>
      <Card>
        <h2 className="mb-3 font-medium">Emails</h2>
        <div className="space-y-1 text-sm">
          {(lead.emails || []).map((e: any) => (
            <div key={e.id}>
              {e.email} {e.is_primary ? <Badge tone="ok">primary</Badge> : null}
            </div>
          ))}
          {!lead.emails?.length ? <p className="text-muted">None found</p> : null}
        </div>
      </Card>
    </div>
  );
}
