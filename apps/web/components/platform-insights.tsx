"use client";

import { Badge, Button, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { useState } from "react";
import { toast } from "sonner";

export type FindingStatus = "confirmed_missing" | "improvement" | "not_detected" | "manual_review" | "present";

export type PlatformCheck = {
  id: string;
  feature: string;
  category: string;
  status: FindingStatus;
  severity: string;
  evidence: { url: string; observed: string }[];
  recommendation: string;
  business_relevance: string;
};

export type PlatformIntel = {
  primary?: string;
  confidence?: number;
  confidence_label?: string;
  secondary?: { name: string; category: string }[];
  evidence?: { signal: string; source: string; platform: string }[];
  checks?: PlatformCheck[];
  counts?: Record<string, number>;
  unreadable?: string;
  detected_at?: string;
};

export type PlatformOpportunity = {
  title: string;
  status: FindingStatus;
  severity: string;
  evidence: string;
  evidence_url: string;
  recommendation: string;
  business_relevance: string;
};

export const STATUS_INFO: Record<FindingStatus, { label: string; tone: "ok" | "warn" | "bad" | "default"; hint: string }> = {
  confirmed_missing: { label: "Confirmed missing", tone: "bad", hint: "Checked where it must appear, and it isn't there." },
  improvement: { label: "Improvement", tone: "warn", hint: "It exists, but there is a visible or measurable weakness." },
  not_detected: { label: "Not detected", tone: "default", hint: "Not visible to the audit; it may still exist through a script or app. Confirm before mentioning it." },
  manual_review: { label: "Needs manual review", tone: "default", hint: "Worth a human look; not a confirmed problem." },
  present: { label: "Present", tone: "ok", hint: "Seen on the site." },
};

const CONFIDENCE_TONE: Record<string, "ok" | "warn" | "bad" | "default"> = { High: "ok", Medium: "warn", Low: "default", Unknown: "default" };

export function StatusBadge({ status }: { status?: string }) {
  const info = STATUS_INFO[(status || "") as FindingStatus];
  if (!info) return null;
  return (
    <span title={info.hint}>
      <Badge tone={info.tone}>{info.label}</Badge>
    </span>
  );
}

function short(url: string) {
  return url.replace(/^https?:\/\/(www\.)?/, "").slice(0, 60);
}

/** Platform, its evidence, platform-specific opportunities, feature checks and the editable outreach angle. */
export function PlatformInsights({
  auditId,
  platform,
  intel,
  opportunities,
  angle,
  onSaved,
}: {
  auditId: string;
  platform?: string;
  intel?: PlatformIntel;
  opportunities?: PlatformOpportunity[];
  angle?: string;
  onSaved?: () => void;
}) {
  const [draft, setDraft] = useState(angle || "");
  const [saving, setSaving] = useState(false);

  if (!intel?.primary && !platform) {
    return (
      <p className="text-xs text-muted">
        Platform details aren&apos;t available for this audit (made before platform checks). Use &quot;Update older audits&quot;
        on the Website audits page to add them.
      </p>
    );
  }
  const checks = (intel?.checks || []).filter((c) => c.status !== "present");
  const present = (intel?.checks || []).filter((c) => c.status === "present");
  const label = intel?.confidence_label || "Unknown";

  async function saveAngle() {
    setSaving(true);
    try {
      await api(`/api/audits/${auditId}/outreach-angle`, { method: "POST", body: JSON.stringify({ text: draft }) });
      toast.success("Outreach angle saved. Redraft the email to use it.");
      onSaved?.();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-3 rounded-lg border border-border p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">Detected platform:</span>
        <Badge tone="default">{intel?.primary || platform}</Badge>
        <span className="text-xs text-muted">confidence</span>
        <Badge tone={CONFIDENCE_TONE[label] || "default"}>{label}</Badge>
        {intel?.secondary?.length ? (
          <span className="text-xs text-muted">
            also uses: {intel.secondary.slice(0, 10).map((s) => s.name).join(", ")}
          </span>
        ) : null}
      </div>
      {intel?.unreadable ? <p className="text-xs text-amber-700 dark:text-amber-300">Checks skipped: {intel.unreadable}.</p> : null}
      {intel?.evidence?.length ? (
        <details className="text-xs">
          <summary className="cursor-pointer text-muted">How it was detected</summary>
          <ul className="mt-1 list-disc pl-5 text-muted">
            {intel.evidence.slice(0, 12).map((e, i) => (
              <li key={i}>
                {e.platform}: &quot;{e.signal}&quot; in the {e.source}
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      {opportunities?.length ? (
        <div className="space-y-2">
          <div className="font-medium">Platform-specific opportunities</div>
          {opportunities.map((o) => (
            <div key={o.title} className="rounded-md bg-background p-2">
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge status={o.status} />
                <span className="font-medium">{o.title}</span>
              </div>
              {o.evidence ? (
                <div className="mt-1 text-xs text-muted">
                  Evidence: {o.evidence}
                  {o.evidence_url ? (
                    <a className="ml-1 underline" href={o.evidence_url} target="_blank" rel="noreferrer">
                      ({short(o.evidence_url)})
                    </a>
                  ) : null}
                </div>
              ) : null}
              {o.recommendation ? <div className="mt-1 text-xs">{o.recommendation}</div> : null}
              {o.business_relevance ? <div className="mt-1 text-xs text-muted">Why it matters: {o.business_relevance}</div> : null}
            </div>
          ))}
        </div>
      ) : null}

      {checks.length || present.length ? (
        <details className="text-xs">
          <summary className="cursor-pointer font-medium">
            Feature checks: {checks.length} to look at, {present.length} in place
          </summary>
          <table className="mt-2 w-full text-left">
            <tbody>
              {[...checks, ...present].map((c) => (
                <tr key={c.id} className="border-t border-border align-top">
                  <td className="py-1 pr-2">
                    <StatusBadge status={c.status} />
                  </td>
                  <td className="py-1 pr-2 font-medium">{c.feature}</td>
                  <td className="py-1 text-muted">
                    {c.evidence.map((e, i) => (
                      <div key={i}>
                        {e.observed}{" "}
                        <a className="underline" href={e.url} target="_blank" rel="noreferrer">
                          ({short(e.url)})
                        </a>
                      </div>
                    ))}
                    {c.recommendation ? <div className="text-foreground">{c.recommendation}</div> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      ) : null}

      <div>
        <div className="font-medium">Suggested outreach angle</div>
        <Textarea
          rows={3}
          value={draft}
          placeholder="No angle yet (made from the strongest confirmed finding)."
          onChange={(e) => setDraft(e.target.value)}
        />
        <div className="mt-1 flex flex-wrap items-center justify-between gap-2">
          <span className="text-xs text-muted">
            Used as the opening when the email is written or redrafted, and as {"{{outreach_angle}}"} in campaign templates.
            Nothing is sent just because an audit finished.
          </span>
          <Button variant="secondary" disabled={saving || draft === (angle || "")} onClick={saveAngle}>
            Save angle
          </Button>
        </div>
      </div>
    </div>
  );
}
