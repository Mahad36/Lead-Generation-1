"use client";

import { ACTIVE, Audit, AuditPanel, statusTone } from "@/components/audit-panel";
import { TrackingBadges } from "@/components/tracking-badges";
import { Badge, Button, Card, Empty, Input, Label } from "@/components/ui";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/utils";
import { Loader2, Search, X } from "lucide-react";
import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

const EMPTY_FORM = { business_name: "", website_url: "", email: "", city: "", category: "", auto_send: false };

export default function WebsiteAuditsPage() {
  const [form, setForm] = useState(EMPTY_FORM);
  const [audits, setAudits] = useState<Audit[]>([]);
  const [selected, setSelected] = useState<{ leadId: string; email: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const [query, setQuery] = useState("");
  const [search, setSearch] = useState(""); // what was last sent to the API (typing is debounced)
  const loadList = useCallback(
    () => api<Audit[]>(`/api/audits${search ? `?q=${encodeURIComponent(search)}` : ""}`).then(setAudits),
    [search]
  );

  useEffect(() => {
    const timer = setTimeout(() => setSearch(query.trim()), 300);
    return () => clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    loadList().catch((e) => toast.error(e.message));
  }, [loadList]);

  // Keep the list fresh while anything is running.
  useEffect(() => {
    if (!audits.some((a) => ACTIVE.has(a.status))) return;
    const timer = setTimeout(() => loadList().catch(() => undefined), 4000);
    return () => clearTimeout(timer);
  }, [audits, loadList]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const audit = await api<Audit>("/api/audits/quick", { method: "POST", body: JSON.stringify(form) });
      setSelected({ leadId: audit.lead_id, email: audit.recipient_email });
      setForm(EMPTY_FORM);
      toast.success("Audit started");
      loadList();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const set = (key: keyof typeof EMPTY_FORM) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm({ ...form, [key]: key === "auto_send" ? e.target.checked : e.target.value });

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">Website audits</h1>
        <p className="text-sm text-muted">
          Enter a business website and the customer&apos;s email. Claude analyses the site&apos;s performance, writes a PDF
          audit and a personalised email, and you send it with the PDF attached.
        </p>
      </div>

      <Card className="text-sm text-muted">
        Automatic outreach is set per scrape file: start, pause or stop it (and set its daily limit) on the{" "}
        <Link href="/scraping" className="text-primary underline">
          Scraping
        </Link>{" "}
        page or in each lead file. Uploaded CSVs run through{" "}
        <Link href="/campaigns/new" className="text-primary underline">
          a campaign
        </Link>{" "}
        with &quot;Analyse website + personalised email&quot;.
      </Card>

      <Card>
        <form onSubmit={submit} className="grid gap-3 md:grid-cols-2">
          <div>
            <Label>Business name *</Label>
            <Input required value={form.business_name} onChange={set("business_name")} placeholder="ABC Plumbing" />
          </div>
          <div>
            <Label>Website URL *</Label>
            <Input required value={form.website_url} onChange={set("website_url")} placeholder="https://abc-plumbing.com" />
          </div>
          <div>
            <Label>Customer email *</Label>
            <Input required type="email" value={form.email} onChange={set("email")} placeholder="owner@abc-plumbing.com" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>City</Label>
              <Input value={form.city} onChange={set("city")} placeholder="Miami" />
            </div>
            <div>
              <Label>Category</Label>
              <Input value={form.category} onChange={set("category")} placeholder="Plumber" />
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm md:col-span-2">
            <input type="checkbox" checked={form.auto_send} onChange={set("auto_send")} />
            Send the email automatically when the audit is ready (otherwise you review it first)
          </label>
          <div className="md:col-span-2">
            <Button type="submit" disabled={busy}>
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              Run website audit
            </Button>
          </div>
        </form>
      </Card>

      {selected ? <AuditPanel key={selected.leadId} leadId={selected.leadId} defaultEmail={selected.email} onChange={() => loadList().catch(() => undefined)} /> : null}

      <Card>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <h2 className="font-medium">Recent audits</h2>
          <div className="relative w-full sm:w-80">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" />
            <Input
              className="pl-9 pr-8"
              value={query}
              placeholder="Search website, email or business"
              aria-label="Search audits"
              onChange={(e) => setQuery(e.target.value)}
            />
            {query ? (
              <button
                type="button"
                aria-label="Clear search"
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-muted hover:text-foreground"
                onClick={() => setQuery("")}
              >
                <X className="h-4 w-4" />
              </button>
            ) : null}
          </div>
        </div>
        {audits.length ? (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase text-muted">
                <tr>
                  <th className="py-2 pr-3">Website</th>
                  <th className="py-2 pr-3">Customer</th>
                  <th className="py-2 pr-3">Status</th>
                  <th className="py-2 pr-3">Score</th>
                  <th className="py-2 pr-3">Started</th>
                  <th className="py-2" />
                </tr>
              </thead>
              <tbody>
                {audits.map((a) => (
                  <tr key={a.id} className="border-t border-border">
                    <td className="max-w-xs truncate py-2 pr-3">{a.website_url}</td>
                    <td className="py-2 pr-3">{a.recipient_email || "—"}</td>
                    <td className="py-2 pr-3">
                      <span className="inline-flex flex-wrap gap-1">
                        <Badge tone={statusTone(a.status)}>{a.step}</Badge>
                        <TrackingBadges t={a} sent={Boolean(a.sent_at)} />
                      </span>
                    </td>
                    <td className="py-2 pr-3">{a.overall_score ?? "—"}</td>
                    <td className="py-2 pr-3">{formatDate(a.created_at)}</td>
                    <td className="space-x-3 py-2 text-right whitespace-nowrap">
                      <button className="text-primary" onClick={() => setSelected({ leadId: a.lead_id, email: a.recipient_email })}>
                        Open
                      </button>
                      <Link className="text-primary" href={`/leads/${a.lead_id}`}>
                        Lead
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          search ? (
            <Empty title={`No audits match "${search}"`} hint="Try part of the website address or the email." />
          ) : (
            <Empty title="No audits yet" hint="Run your first audit with the form above." />
          )
        )}
      </Card>
    </div>
  );
}
