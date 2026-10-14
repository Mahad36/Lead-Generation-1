"use client";

import { Badge, Button, Card } from "@/components/ui";
import { api } from "@/lib/api";
import { Loader2, Search } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

type Contact = {
  id: string;
  email: string;
  contact_name: string;
  job_title: string;
  type: string;
  type_key: "personal" | "department" | "generic";
  verification: string;
  source_url: string;
  score: number;
  reason: string;
  primary: boolean;
  suggested_cc: boolean;
};

type Contacts = {
  company: string;
  domain: string;
  primary: string;
  primary_reason: string;
  suggested_cc: string[];
  manual_override: boolean;
  contacts: Contact[];
};

const VERIFICATION: Record<string, { label: string; tone: "ok" | "warn" | "bad" | "default"; hint: string }> = {
  domain_ok: { label: "Domain accepts mail", tone: "ok", hint: "Published on the site and its domain receives mail. The mailbox itself can't be checked." },
  published: { label: "Published", tone: "default", hint: "Found on the website; the domain couldn't be checked." },
  not_checked: { label: "Not checked", tone: "default", hint: "Not checked yet." },
  domain_rejects: { label: "Domain can't receive mail", tone: "bad", hint: "Emails to this domain would bounce." },
  bounced: { label: "Bounced", tone: "bad", hint: "An email to it bounced." },
  suppressed: { label: "Unsubscribed / suppressed", tone: "bad", hint: "On your suppression list." },
};
const TYPE_TONE = { personal: "ok", department: "default", generic: "default" } as const;

/** The business's contacts: every address found on its site, who gets the email (and why), CC suggestions. */
export function LeadContacts({ leadId, onChange }: { leadId: string; onChange?: () => void }) {
  const [data, setData] = useState<Contacts | null>(null);
  const [busy, setBusy] = useState("");

  const load = useCallback(
    () =>
      api<Contacts>(`/api/leads/${leadId}/contacts`)
        .then(setData)
        .catch(() => setData(null)),
    [leadId]
  );

  useEffect(() => {
    load();
  }, [load]);

  async function act(key: string, path: string, body?: unknown, done = "") {
    setBusy(key);
    try {
      setData(await api<Contacts>(`/api/leads/${leadId}/contacts/${path}`, { method: "POST", body: body ? JSON.stringify(body) : undefined }));
      if (done) toast.success(done);
      onChange?.();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy("");
    }
  }

  if (!data) return null;
  return (
    <Card className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="font-medium">Contacts at {data.company}</h2>
          <p className="text-xs text-muted">
            {data.domain || "no website domain"} · addresses published on the business&apos;s own website (team, about,
            contact and imprint pages). Nothing is guessed.
          </p>
        </div>
        <Button variant="secondary" disabled={!!busy} onClick={() => act("discover", "discover", undefined, "Website checked for contacts")}>
          {busy === "discover" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
          Find contacts
        </Button>
      </div>

      {data.primary ? (
        <p className="text-sm">
          Emails go to <span className="font-medium">{data.primary}</span>
          <span className="text-muted"> ({data.primary_reason})</span>
          {data.manual_override ? (
            <Button variant="ghost" disabled={!!busy} onClick={() => act("auto", "primary", { email: null }, "Back to the automatic choice")}>
              Use automatic choice
            </Button>
          ) : null}
          {data.suggested_cc.length ? (
            <span className="block text-xs text-muted">
              Could be CC&apos;d when a campaign turns on Additional Business Recipients: {data.suggested_cc.join(", ")}
            </span>
          ) : null}
        </p>
      ) : (
        <p className="text-sm text-amber-700 dark:text-amber-300">No eligible address for this business.</p>
      )}

      {data.contacts.length ? (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-muted">
              <tr>
                <th className="py-1 pr-2">Email</th>
                <th className="py-1 pr-2">Name / title</th>
                <th className="py-1 pr-2">Type</th>
                <th className="py-1 pr-2">Status</th>
                <th className="py-1 pr-2">Why</th>
                <th className="py-1" />
              </tr>
            </thead>
            <tbody>
              {data.contacts.map((c) => {
                const v = VERIFICATION[c.verification] || VERIFICATION.not_checked;
                return (
                  <tr key={c.id} className="border-t border-border align-top">
                    <td className="py-1.5 pr-2">
                      <div className="font-medium">{c.email}</div>
                      {c.primary ? <Badge tone="ok">Primary</Badge> : c.suggested_cc ? <Badge>CC suggestion</Badge> : null}
                    </td>
                    <td className="py-1.5 pr-2">
                      {[c.contact_name, c.job_title].filter(Boolean).join(", ") || <span className="text-muted">not stated</span>}
                    </td>
                    <td className="py-1.5 pr-2">
                      <Badge tone={TYPE_TONE[c.type_key] || "default"}>{c.type}</Badge>
                    </td>
                    <td className="py-1.5 pr-2" title={v.hint}>
                      <Badge tone={v.tone}>{v.label}</Badge>
                    </td>
                    <td className="max-w-xs py-1.5 pr-2 text-muted">
                      {c.reason}
                      {c.source_url ? (
                        <a className="block truncate underline" href={c.source_url} target="_blank" rel="noreferrer">
                          found on {c.source_url.replace(/^https?:\/\/(www\.)?/, "")}
                        </a>
                      ) : null}
                    </td>
                    <td className="py-1.5 text-right">
                      {!c.primary && c.score > 0 ? (
                        <Button variant="secondary" disabled={!!busy} onClick={() => act(c.id, "primary", { email: c.email }, `${c.email} is now the primary contact`)}>
                          Make primary
                        </Button>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-xs text-muted">No addresses stored yet. Press Find contacts to read the website.</p>
      )}
    </Card>
  );
}
