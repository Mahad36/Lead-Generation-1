"use client";

import { ACTIVE, Audit, AuditPanel, statusTone } from "@/components/audit-panel";
import { TrackingBadges } from "@/components/tracking-badges";
import { Badge, Button, Card, Empty, Input } from "@/components/ui";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/utils";
import { Search, X } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

const PAGE_SIZE = 25;

type View = "sent" | "pending" | "not_sent";
const VIEWS: { id: View; label: string; hint: string }[] = [
  { id: "sent", label: "Sent", hint: "Every agency email that went out, newest first, with opens, clicks and replies." },
  { id: "pending", label: "Waiting", hint: "Pitches being written, ready for your review, or waiting for sending hours or an inbox with room." },
  {
    id: "not_sent",
    label: "Not sent",
    hint: "Pitches that failed or weren't sent: the address may bounce, it was held, or sending failed. Open one to choose another address or send it yourself.",
  },
];

type Row = Audit & { business_name?: string; city?: string; email_subject?: string; sent_from?: string };

/** Agency outreach emails: what was sent to which agency, from which inbox, and how it is doing. */
export default function AgencyEmailsPage() {
  const [view, setView] = useState<View>("sent");
  const [rows, setRows] = useState<Row[]>([]);
  const [total, setTotal] = useState(0);
  const [counts, setCounts] = useState<Partial<Record<View, number>>>({});
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<{ leadId: string; email: string } | null>(null);

  const load = useCallback(() => {
    const q = search ? `&q=${encodeURIComponent(search)}` : "";
    return Promise.all(
      VIEWS.map((v) =>
        api<{ items: Row[]; total: number }>(
          `/api/audits?kind=agency&view=${v.id}&page=${v.id === view ? page : 1}&page_size=${v.id === view ? PAGE_SIZE : 1}${q}`
        ).then((data) => [v.id, data] as const)
      )
    ).then((results) => {
      const next: Partial<Record<View, number>> = {};
      for (const [id, data] of results) {
        next[id] = data.total;
        if (id === view) {
          setRows(data.items);
          setTotal(data.total);
        }
      }
      setCounts(next);
    });
  }, [view, page, search]);

  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(query.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    load().catch((e) => toast.error(e.message));
  }, [load]);

  // Keep the list fresh while pitches are being written or sent
  useEffect(() => {
    if (!rows.some((r) => ACTIVE.has(r.status))) return;
    const timer = setTimeout(() => load().catch(() => undefined), 5000);
    return () => clearTimeout(timer);
  }, [rows, load]);

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const current = VIEWS.find((v) => v.id === view)!;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">Agency emails</h1>
        <p className="text-sm text-muted">
          Every white-label pitch to agencies, from your{" "}
          <Link href="/agencies" className="text-primary underline">
            Agency outreach
          </Link>{" "}
          files. Open one to read the email exactly as it was sent, or to review and send a waiting one.
        </p>
      </div>

      {selected ? (
        <AuditPanel key={selected.leadId} leadId={selected.leadId} defaultEmail={selected.email} onChange={() => load().catch(() => undefined)} />
      ) : null}

      <Card>
        <div className="mb-3 flex gap-1 border-b border-border" role="tablist" aria-label="Agency email lists">
          {VIEWS.map((v) => (
            <button
              key={v.id}
              type="button"
              role="tab"
              aria-selected={view === v.id}
              className={
                "-mb-px border-b-2 px-3 py-2 text-sm " +
                (view === v.id ? "border-primary font-medium text-foreground" : "border-transparent text-muted hover:text-foreground")
              }
              onClick={() => {
                setView(v.id);
                setPage(1);
              }}
            >
              {v.label}
              {counts[v.id] !== undefined ? <span className="ml-1 text-xs text-muted">({counts[v.id]})</span> : null}
            </button>
          ))}
        </div>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-muted">{current.hint}</p>
          <div className="relative w-full sm:w-80">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" />
            <Input
              className="pl-9 pr-8"
              value={query}
              placeholder="Search agency, website or email"
              aria-label="Search agency emails"
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

        {rows.length ? (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase text-muted">
                <tr>
                  <th className="py-2 pr-3">Agency</th>
                  <th className="py-2 pr-3">Sent to</th>
                  <th className="py-2 pr-3">Subject</th>
                  <th className="py-2 pr-3">Status</th>
                  <th className="py-2 pr-3">Sent from</th>
                  <th className="py-2 pr-3">{view === "sent" ? "Sent" : "Started"}</th>
                  <th className="py-2" />
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} className="border-t border-border align-top">
                    <td className="py-2 pr-3">
                      <div className="font-medium">{r.business_name || "—"}</div>
                      <div className="max-w-[16rem] truncate text-xs text-muted">
                        {r.city ? `${r.city} · ` : ""}
                        <a className="text-primary" href={r.website_url} target="_blank" rel="noreferrer">
                          {r.website_url.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "")}
                        </a>
                      </div>
                    </td>
                    <td className="py-2 pr-3">{r.recipient_email || "—"}</td>
                    <td className="max-w-[18rem] truncate py-2 pr-3" title={r.email_subject}>
                      {r.email_subject || <span className="text-muted">—</span>}
                    </td>
                    <td className="py-2 pr-3">
                      <span className="inline-flex flex-wrap gap-1">
                        <Badge tone={statusTone(r.status)}>{r.sent_at ? "Sent" : r.step}</Badge>
                        <TrackingBadges t={r} sent={Boolean(r.sent_at)} />
                      </span>
                    </td>
                    <td className="whitespace-nowrap py-2 pr-3">{r.sent_from || "—"}</td>
                    <td className="whitespace-nowrap py-2 pr-3">{formatDate(view === "sent" ? r.sent_at : r.created_at)}</td>
                    <td className="space-x-3 whitespace-nowrap py-2 text-right">
                      <button
                        className="text-primary"
                        onClick={() => {
                          setSelected({ leadId: r.lead_id, email: r.recipient_email });
                          window.scrollTo({ top: 0, behavior: "smooth" });
                        }}
                      >
                        Open email
                      </button>
                      <Link className="text-primary" href={`/leads/${r.lead_id}`}>
                        Lead
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-sm text-muted">
              <span>
                {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, total)} of {total}
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
        ) : search ? (
          <Empty title="No agency emails match this search" hint="Try another agency name, website or email." />
        ) : view === "sent" ? (
          <Empty title="No agency emails sent yet" hint="Start a file in Agency outreach; sent pitches show here." />
        ) : (
          <Empty title="Nothing here" hint={current.hint} />
        )}
      </Card>
    </div>
  );
}
