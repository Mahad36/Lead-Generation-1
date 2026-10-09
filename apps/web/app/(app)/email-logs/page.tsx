"use client";

import { TrackingBadges } from "@/components/tracking-badges";
import { Badge, Card } from "@/components/ui";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/utils";
import { useEffect, useState } from "react";
import { toast } from "sonner";

export default function EmailLogsPage() {
  const [data, setData] = useState<{ items: any[]; total: number }>({ items: [], total: 0 });

  useEffect(() => {
    api<{ items: any[]; total: number }>("/api/email-logs").then(setData).catch((e) => toast.error(e.message));
  }, []);

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Email logs</h1>
      <Card className="overflow-x-auto p-0">
        <table className="min-w-full text-sm">
          <thead className="bg-background text-muted">
            <tr>
              {["Recipient", "Business", "Subject", "Status", "Sent", "Tracking", "Error"].map((h) => (
                <th key={h} className="p-3 text-left">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.items.map((row) => (
              <tr key={row.id} className="border-t border-border">
                <td className="p-3">{row.recipient}</td>
                <td className="p-3">{row.business}</td>
                <td className="p-3">{row.subject}</td>
                <td className="p-3"><Badge tone={row.status === "sent" ? "ok" : row.status === "failed" || row.status === "bounced" ? "bad" : "default"}>{row.status}</Badge></td>
                <td className="p-3">{formatDate(row.sent_at)}</td>
                <td className="p-3"><TrackingBadges t={row} sent={Boolean(row.sent_at) && !row.is_test} /></td>
                <td className="p-3 text-danger">{row.error}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
