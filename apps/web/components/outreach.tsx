"use client";

import { openAuditPdf } from "@/components/audit-panel";
import { TrackingBadges, type Tracking } from "@/components/tracking-badges";
import { Badge, Button } from "@/components/ui";
import { formatDate } from "@/lib/utils";
import { CheckCircle2, Circle, Download, FileText, Loader2, XCircle } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

/** Per-lead pipeline status from the API (apps/api/app/services/lead_outreach.py). */
export type Outreach = Tracking & {
  stage: string;
  stage_label: string;
  note: string;
  audit_id: string | null;
  audit_status: string;
  audit_step: string;
  failed_step: string;
  error: string;
  attempts: number;
  score: number | null;
  has_pdf: boolean;
  pdf_filename: string;
  has_email: boolean;
  email_subject: string;
  sent_at: string | null;
};

export type OutreachLead = {
  id: string;
  business_name: string;
  contact_name?: string;
  primary_email: string;
  website_url: string;
  city?: string;
  country?: string;
  category?: string;
  outreach?: Outreach | null;
};

const STAGES: [string, string][] = [
  ["scraped", "Scraped"],
  ["website_found", "Website found"],
  ["auditing", "Auditing website"],
  ["audited", "Website audited"],
  ["pdf_generated", "PDF generated"],
  ["email_generated", "Email generated"],
  ["sent", "Email sent"],
  ["opened", "Opened"],
  ["clicked", "Link clicked"],
  ["replied", "Replied"],
];
const ORDER = STAGES.map(([key]) => key);
const RUNNING = new Set(["auditing", "audited", "pdf_generated", "sending"]);
// While a stage is running, the step being worked on is the next one (e.g. "audited" = building the PDF).
const ACTIVE_AT: Record<string, string> = {
  auditing: "auditing",
  audited: "pdf_generated",
  pdf_generated: "email_generated",
  sending: "sent",
};
// Where a failed audit step sits in the stepper.
const FAILED_AT: Record<string, string> = { generating_pdf: "pdf_generated", drafting_email: "email_generated" };

export function stageTone(stage: string): "ok" | "warn" | "bad" | "default" {
  if (["failed", "send_failed", "bounced"].includes(stage)) return "bad";
  if (stage === "audit_stopped") return "default";
  if (["replied", "clicked", "opened", "sent", "email_generated"].includes(stage)) return "ok";
  if (RUNNING.has(stage)) return "warn";
  return "default";
}

export function StageBadge({ o }: { o?: Outreach | null }) {
  if (!o) return null;
  const busy = RUNNING.has(o.stage);
  return (
    <span className="inline-flex items-center gap-1" title={o.error || o.note || o.audit_step}>
      {busy ? <Loader2 className="h-3 w-3 animate-spin text-amber-600" /> : null}
      <Badge tone={stageTone(o.stage)}>
        {o.stage_label}
        {o.stage === "failed" && o.failed_step ? ` at ${o.failed_step.replace("_", " ")}` : ""}
      </Badge>
    </span>
  );
}

/** Scraped -> ... -> Replied, with the current position and any failure. */
export function OutreachStepper({ o }: { o: Outreach }) {
  let at = ORDER.indexOf(o.stage);
  let mode: "done" | "active" | "failed" = "done";
  if (o.stage === "failed") [at, mode] = [ORDER.indexOf(FAILED_AT[o.failed_step] || "auditing"), "failed"];
  else if (o.stage === "send_failed" || o.stage === "bounced") [at, mode] = [ORDER.indexOf("sent"), "failed"];
  else if (ACTIVE_AT[o.stage]) [at, mode] = [ORDER.indexOf(ACTIVE_AT[o.stage]), "active"];
  else if (at < 0) at = ORDER.indexOf("scraped"); // no website / no email / do not contact
  return (
    <div className="space-y-2">
      <ol className="flex flex-wrap gap-x-3 gap-y-2 text-sm">
        {STAGES.map(([key, label], index) => {
          const state = index < at ? "done" : index === at ? mode : "todo";
          return (
            <li key={key} className="flex items-center gap-1">
              {state === "done" ? <CheckCircle2 className="h-4 w-4 text-emerald-600" /> : null}
              {state === "active" ? <Loader2 className="h-4 w-4 animate-spin text-amber-600" /> : null}
              {state === "failed" ? <XCircle className="h-4 w-4 text-red-600" /> : null}
              {state === "todo" ? <Circle className="h-4 w-4 text-muted" /> : null}
              <span className={state === "todo" ? "text-muted" : ""}>{label}</span>
            </li>
          );
        })}
      </ol>
      {o.stage === "bounced" ? <p className="text-sm text-red-600">The email bounced; the address is now on the suppression list.</p> : null}
      {o.error ? <p className="text-sm text-red-600">{o.error}</p> : null}
      {o.note ? <p className="text-sm text-muted">{o.note}</p> : null}
      {["no_website", "no_email", "do_not_contact"].includes(o.stage) ? (
        <p className="text-sm text-muted">{o.stage_label}: this lead can&apos;t be audited and emailed automatically.</p>
      ) : null}
    </div>
  );
}

function EmailCell({ o }: { o?: Outreach | null }) {
  if (!o?.sent_at) return <span className="text-muted">{o?.has_email ? "Draft ready" : "—"}</span>;
  return (
    <div className="space-y-1">
      <div className="text-xs text-muted">Sent {formatDate(o.sent_at)}</div>
      <TrackingBadges t={o} sent />
      {o.open_count ? <div className="text-xs text-muted">Last opened {formatDate(o.last_opened_at)}</div> : null}
    </div>
  );
}

/** Leads with their audit / PDF / email / tracking status. */
export function OutreachTable({ leads, actions }: { leads: OutreachLead[]; actions?: (lead: OutreachLead) => ReactNode }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-border bg-card">
      <table className="min-w-full text-left text-sm">
        <thead className="text-xs uppercase text-muted">
          <tr>
            {["Company", "Email", "Website", "Workflow", "Score", "PDF", "Email status", ""].map((h) => (
              <th key={h} className="whitespace-nowrap px-3 py-2">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {leads.map((lead) => {
            const o = lead.outreach;
            return (
              <tr key={lead.id} className="border-t border-border align-top">
                <td className="px-3 py-2">
                  <div className="font-medium">{lead.business_name}</div>
                  <div className="text-xs text-muted">
                    {lead.contact_name || "No contact name"}
                    {lead.city ? ` · ${lead.city}` : ""}
                  </div>
                </td>
                <td className="px-3 py-2">{lead.primary_email || "—"}</td>
                <td className="max-w-[14rem] truncate px-3 py-2">
                  {lead.website_url ? (
                    <a className="text-primary" href={lead.website_url} target="_blank" rel="noreferrer">
                      {lead.website_url.replace(/^https?:\/\/(www\.)?/, "")}
                    </a>
                  ) : (
                    "—"
                  )}
                </td>
                <td className="px-3 py-2">
                  <StageBadge o={o} />
                  {o?.attempts && o.attempts > 1 ? <div className="text-xs text-muted">{o.attempts} attempts</div> : null}
                </td>
                <td className="px-3 py-2">{o?.score ?? "—"}</td>
                <td className="whitespace-nowrap px-3 py-2">
                  {o?.has_pdf && o.audit_id ? (
                    <span className="flex gap-1">
                      <Button variant="secondary" onClick={() => openAuditPdf({ id: o.audit_id!, pdf_filename: o.pdf_filename }, false)}>
                        <FileText className="h-4 w-4" />
                      </Button>
                      <Button variant="secondary" onClick={() => openAuditPdf({ id: o.audit_id!, pdf_filename: o.pdf_filename }, true)}>
                        <Download className="h-4 w-4" />
                      </Button>
                    </span>
                  ) : (
                    <span className="text-muted">—</span>
                  )}
                </td>
                <td className="px-3 py-2">
                  <EmailCell o={o} />
                </td>
                <td className="whitespace-nowrap px-3 py-2 text-right">
                  {actions ? actions(lead) : null}
                  <Link href={`/leads/${lead.id}`}>
                    <Button variant="secondary">View</Button>
                  </Link>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
