"use client";

import { SendFrom } from "@/components/send-from";
import { ACTIVE, Audit, AuditPanel, statusTone } from "@/components/audit-panel";
import { TrackingBadges } from "@/components/tracking-badges";
import { Badge, Button, Card, Empty, Input, Label } from "@/components/ui";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/utils";
import { Loader2, Search, X } from "lucide-react";
import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

const PAGE_SIZE = 25;

/** "Unknown" / empty platforms show as a dash. */
function platformLabel(platform?: string) {
  const name = (platform || "").trim();
  return !name || /^(unknown|none|custom\/unknown)$/i.test(name) ? "—" : name;
}

const EMPTY_FORM = { business_name: "", website_url: "", email: "", city: "", category: "", auto_send: false, email_account_id: "" };

export default function WebsiteAuditsPage() {
  const [form, setForm] = useState(EMPTY_FORM);
  const [audits, setAudits] = useState<Audit[]>([]);
  const [selected, setSelected] = useState<{ leadId: string; email: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const [query, setQuery] = useState("");
  const [search, setSearch] = useState(""); // what was last sent to the API (typing is debounced)
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [platform, setPlatform] = useState("");
  const [findingType, setFindingType] = useState("");
  const [platforms, setPlatforms] = useState<{ platform: string; count: number }[]>([]);
  const loadList = useCallback(
    () =>
      api<{ items: Audit[]; total: number }>(
        `/api/audits?page=${page}&page_size=${PAGE_SIZE}${search ? `&q=${encodeURIComponent(search)}` : ""}` +
          `${platform ? `&platform=${encodeURIComponent(platform)}` : ""}${findingType ? `&finding_type=${findingType}` : ""}`
      ).then((data) => {
        setAudits(data.items);
        setTotal(data.total);
      }),
    [search, page, platform, findingType]
  );

  useEffect(() => {
    api<{ platform: string; count: number }[]>("/api/audits/platforms")
      .then(setPlatforms)
      .catch(() => setPlatforms([]));
  }, []);
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(query.trim());
      setPage(1);
    }, 300);
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
          <SendFrom
            className="md:col-span-2 md:max-w-md"
            value={form.email_account_id}
            onChange={(id) => setForm({ ...form, email_account_id: id })}
          />
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
        <OlderAuditsUpdate onDone={() => loadList().catch(() => undefined)} />
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="font-medium">Recent audits</h2>
            <select
              className="rounded-md border border-border bg-card px-2 py-1 text-sm"
              value={platform}
              aria-label="Filter by platform"
              onChange={(e) => {
                setPlatform(e.target.value);
                setPage(1);
              }}
            >
              <option value="">All platforms</option>
              {platforms.map((p) => (
                <option key={p.platform} value={p.platform}>
                  {platformLabel(p.platform) === "—" ? "Unknown" : p.platform} ({p.count})
                </option>
              ))}
            </select>
            <select
              className="rounded-md border border-border bg-card px-2 py-1 text-sm"
              value={findingType}
              aria-label="Filter by finding type"
              onChange={(e) => {
                setFindingType(e.target.value);
                setPage(1);
              }}
            >
              <option value="">All findings</option>
              <option value="confirmed_missing">Has confirmed-missing items</option>
              <option value="improvement">Has improvement opportunities</option>
              <option value="not_detected">Has not-detected items</option>
              <option value="manual_review">Needs manual review</option>
            </select>
          </div>
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
                  <th className="py-2 pr-3">Platform</th>
                  <th className="py-2 pr-3">Status</th>
                  <th className="py-2 pr-3">Sent from</th>
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
                    <td className="py-2 pr-3 whitespace-nowrap">{platformLabel(a.platform)}</td>
                    <td className="py-2 pr-3">
                      <span className="inline-flex flex-wrap gap-1">
                        <Badge tone={statusTone(a.status)}>{a.step}</Badge>
                        <TrackingBadges t={a} sent={Boolean(a.sent_at)} />
                      </span>
                    </td>
                    <td className="py-2 pr-3 whitespace-nowrap">{a.sent_from || "—"}</td>
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
            <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-sm text-muted">
              <span>
                {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, total)} of {total} audits
              </span>
              <div className="flex items-center gap-2">
                <Button variant="secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>
                  Previous
                </Button>
                <span>
                  Page {page} of {pages}
                </span>
                <Button variant="secondary" disabled={page >= pages} onClick={() => setPage(page + 1)}>
                  Next
                </Button>
              </div>
            </div>
          </div>
        ) : (
          search || platform || findingType ? (
            <Empty title="No audits match these filters" hint="Try another platform, finding type or search." />
          ) : (
            <Empty title="No audits yet" hint="Run your first audit with the form above." />
          )
        )}
      </Card>
    </div>
  );
}

type ReprocessStatus = {
  running: boolean;
  done?: number;
  failed?: number;
  total?: number;
  current?: string;
  older_audits_without_platform: number;
};

/** Add platform details to audits made before platform checks, a batch at a time in the background. */
function OlderAuditsUpdate({ onDone }: { onDone: () => void }) {
  const [state, setState] = useState<ReprocessStatus | null>(null);

  const refresh = useCallback(
    () =>
      api<ReprocessStatus>("/api/audits/reprocess-platform")
        .then((s) => {
          setState((prev) => {
            if (prev?.running && !s.running) onDone();
            return s;
          });
        })
        .catch(() => undefined),
    [onDone]
  );

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (!state?.running) return;
    const timer = setInterval(refresh, 5000);
    return () => clearInterval(timer);
  }, [state?.running, refresh]);

  if (!state || (!state.running && !state.older_audits_without_platform)) return null;
  async function start() {
    try {
      setState(await api<ReprocessStatus>("/api/audits/reprocess-platform", { method: "POST", body: JSON.stringify({ limit: 25, with_ai: true }) }));
      toast.success("Updating older audits in the background");
    } catch (e) {
      toast.error((e as Error).message);
    }
  }
  return (
    <div className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border p-3 text-sm">
      <span>
        {state.running
          ? `Adding platform details: ${state.done || 0} of ${state.total || 0} done${state.failed ? `, ${state.failed} failed` : ""}${state.current ? ` (now: ${state.current.replace(/^https?:\/\/(www\.)?/, "")})` : ""}`
          : `${state.older_audits_without_platform} older audits have no platform details yet. Existing findings are kept; each site is re-checked gently, one at a time.`}
      </span>
      {!state.running ? (
        <Button variant="secondary" onClick={start}>
          Update older audits (25 at a time)
        </Button>
      ) : null}
    </div>
  );
}
