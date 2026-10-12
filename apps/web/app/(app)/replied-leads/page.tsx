"use client";

import { Badge, Button, Card, Empty, Input } from "@/components/ui";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/utils";
import { Download, Search, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { toast } from "sonner";

type RepliedLead = {
  lead_id: string | null;
  root_id: string | null;
  business_name: string;
  contact_name: string;
  email: string;
  phone: string;
  website_url: string;
  category: string;
  city: string;
  country: string;
  file_name: string;
  subject: string;
  first_replied_at: string;
  last_reply_at: string;
  last_reply_snippet: string;
  replies: number;
  unread: number;
  you_answered: boolean;
  your_last_answer_at: string | null;
};

const CSV_COLUMNS: [keyof RepliedLead, string][] = [
  ["business_name", "Business"],
  ["contact_name", "Contact"],
  ["email", "Email"],
  ["phone", "Phone"],
  ["website_url", "Website"],
  ["category", "Category"],
  ["city", "City"],
  ["country", "Country"],
  ["file_name", "Lead file"],
  ["first_replied_at", "First replied"],
  ["last_reply_at", "Last reply"],
  ["replies", "Replies"],
  ["you_answered", "You answered"],
  ["last_reply_snippet", "Last reply text"],
];

function downloadCsv(rows: RepliedLead[]) {
  const cell = (value: unknown) => `"${String(value ?? "").replace(/"/g, '""')}"`;
  const lines = [
    CSV_COLUMNS.map(([, title]) => cell(title)).join(","),
    ...rows.map((r) => CSV_COLUMNS.map(([key]) => cell(key === "you_answered" ? (r.you_answered ? "Yes" : "No") : r[key])).join(",")),
  ];
  const url = URL.createObjectURL(new Blob(["﻿" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `replied-leads-${new Date().toISOString().slice(0, 10)}.csv`;
  link.click();
  URL.revokeObjectURL(url);
}

export default function RepliedLeadsPage() {
  const [rows, setRows] = useState<RepliedLead[] | null>(null);
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [needsAnswer, setNeedsAnswer] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => setSearch(query.trim()), 300);
    return () => clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    const load = () =>
      api<RepliedLead[]>(`/api/conversations/leads${search ? `?q=${encodeURIComponent(search)}` : ""}`).then(setRows);
    load().catch((e) => toast.error(e.message));
    const timer = setInterval(() => load().catch(() => undefined), 60_000);
    return () => clearInterval(timer);
  }, [search]);

  const shown = (rows || []).filter((r) => !needsAnswer || !r.you_answered);
  const waiting = (rows || []).filter((r) => !r.you_answered).length;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Replied leads</h1>
          <p className="text-sm text-muted">
            Every lead who answered one of your emails, newest reply first.
          </p>
        </div>
        <Button variant="secondary" disabled={!shown.length} onClick={() => downloadCsv(shown)}>
          <Download className="h-4 w-4" /> Export CSV
        </Button>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <div className="relative w-full sm:w-80">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" />
          <Input
            className="pl-9 pr-8"
            value={query}
            placeholder="Search business, email, website or city"
            aria-label="Search replied leads"
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
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={needsAnswer} onChange={(e) => setNeedsAnswer(e.target.checked)} />
          Waiting for my answer{rows ? ` (${waiting})` : ""}
        </label>
        {rows ? <span className="text-sm text-muted">{shown.length} lead{shown.length === 1 ? "" : "s"}</span> : null}
      </div>

      {rows === null ? (
        <p className="text-sm text-muted">Loading...</p>
      ) : !shown.length ? (
        <Empty
          title={search ? `No replied leads match "${search}"` : needsAnswer ? "You've answered everyone" : "No replies yet"}
          hint={search || needsAnswer ? undefined : "When a lead answers one of your emails, they appear here within a few minutes."}
        />
      ) : (
        <Card className="overflow-x-auto p-0">
          <table className="w-full min-w-[900px] text-sm">
            <thead className="border-b border-border text-left text-xs text-muted">
              <tr>
                <th className="px-4 py-3 font-medium">Business</th>
                <th className="px-4 py-3 font-medium">Contact</th>
                <th className="px-4 py-3 font-medium">Their last reply</th>
                <th className="px-4 py-3 font-medium">Lead file</th>
                <th className="px-4 py-3 font-medium">Replied</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {shown.map((r) => (
                <tr key={r.lead_id || r.email} className="align-top">
                  <td className="px-4 py-3">
                    <div className="font-medium">{r.business_name}</div>
                    {r.website_url ? (
                      <a href={r.website_url} target="_blank" rel="noreferrer" className="text-xs text-muted hover:underline">
                        {r.website_url.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "")}
                      </a>
                    ) : null}
                    {r.category || r.city ? (
                      <div className="text-xs text-muted">{[r.category, r.city, r.country].filter(Boolean).join(" · ")}</div>
                    ) : null}
                  </td>
                  <td className="px-4 py-3">
                    {r.contact_name ? <div>{r.contact_name}</div> : null}
                    <div className="text-xs">{r.email}</div>
                    {r.phone ? <div className="text-xs text-muted">{r.phone}</div> : null}
                  </td>
                  <td className="max-w-xs px-4 py-3 text-xs text-muted">
                    {r.last_reply_snippet || (r.replies ? "(no text)" : "Reply received before conversations were saved")}
                  </td>
                  <td className="px-4 py-3 text-xs">{r.file_name || "-"}</td>
                  <td className="whitespace-nowrap px-4 py-3 text-xs">
                    <div>{formatDate(r.last_reply_at)}</div>
                    {r.replies > 1 ? <div className="text-muted">{r.replies} replies</div> : null}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-col items-start gap-1">
                      {r.unread ? <Badge tone="warn">New</Badge> : null}
                      {r.you_answered ? <Badge tone="ok">Answered</Badge> : <Badge>Needs answer</Badge>}
                    </div>
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-right">
                    <div className="flex justify-end gap-2">
                      {r.root_id ? (
                        <Link href={`/replies?id=${r.root_id}`}>
                          <Button variant="secondary">Conversation</Button>
                        </Link>
                      ) : null}
                      {r.lead_id ? (
                        <Link href={`/leads/${r.lead_id}`}>
                          <Button variant="secondary">Lead</Button>
                        </Link>
                      ) : null}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
