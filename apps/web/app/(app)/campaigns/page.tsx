"use client";

import { Badge, Button, Card, Empty } from "@/components/ui";
import { api } from "@/lib/api";
import { CAMPAIGN_STATUS } from "@/lib/constants";
import { formatDate } from "@/lib/utils";
import Link from "next/link";
import { useEffect, useState } from "react";
import { toast } from "sonner";

type Campaign = {
  id: string;
  name: string;
  status: string;
  sent_count: number;
  pending_count: number;
  failed_count: number;
  file_name: string;
  file_kind: string;
  created_at: string;
  paused_reason: string;
  outreach_mode?: string;
  auditing_count?: number;
  sent_today?: number;
  daily_limit?: number;
};

const ACTIVE = new Set(["running", "scheduled", "paused", "draft"]);

function tone(status: string): "ok" | "warn" | "bad" | "default" {
  if (status === "running" || status === "completed") return "ok";
  if (status === "paused" || status === "draft" || status === "scheduled") return "warn";
  if (status === "failed" || status === "stopped") return "bad";
  return "default";
}

export default function CampaignsPage() {
  const [items, setItems] = useState<Campaign[]>([]);
  const [busy, setBusy] = useState("");
  const [confirmStop, setConfirmStop] = useState("");

  const load = () => api<Campaign[]>("/api/campaigns").then(setItems);

  useEffect(() => {
    load().catch((e) => toast.error(e.message));
  }, []);

  // Keep the list current while any campaign is working.
  useEffect(() => {
    if (!items.some((c) => c.status === "running" || c.status === "scheduled")) return;
    const timer = setInterval(() => load().catch(() => undefined), 8000);
    return () => clearInterval(timer);
  }, [items]);

  async function act(c: Campaign, action: "pause" | "resume" | "stop") {
    setBusy(`${c.id}:${action}`);
    try {
      await api(`/api/campaigns/${c.id}/${action}`, { method: "POST" });
      toast.success(
        action === "pause"
          ? `Paused "${c.name}". Emails already being prepared wait until you resume.`
          : action === "resume"
            ? `Resumed "${c.name}"`
            : `Stopped "${c.name}". Nothing more will be sent.`
      );
      setConfirmStop("");
      await load();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Email campaigns</h1>
          <p className="text-sm text-muted">Each scrape or upload is its own campaign. Pause or stop anytime. All campaigns share the daily send setting.</p>
        </div>
        <Link href="/campaigns/new">
          <Button>New campaign</Button>
        </Link>
      </div>
      {items.length === 0 ? (
        <Empty title="No campaigns yet" hint="Start a scrape — it creates a campaign and sends as leads are ready." />
      ) : (
        <div className="grid gap-3">
          {items.map((c) => (
            <Card key={c.id} className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <Link href={`/campaigns/${c.id}`} className="font-medium text-primary">
                  {c.name}
                </Link>
                <div className="text-sm text-muted">
                  {c.file_name ? `${c.file_name} · ` : ""}
                  {c.outreach_mode === "audit" ? "Website analysis · " : ""}
                  {c.sent_count} sent · {c.pending_count} left
                  {c.auditing_count ? ` · ${c.auditing_count} auditing` : ""}
                  {c.failed_count ? ` · ${c.failed_count} failed` : ""}
                  {c.status === "running" && c.daily_limit ? ` · ${c.sent_today ?? 0}/${c.daily_limit} today` : ""}
                  {c.created_at ? ` · ${formatDate(c.created_at)}` : ""}
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={tone(c.status)}>
                  {CAMPAIGN_STATUS[c.status] || c.status}
                  {c.status === "paused" && c.paused_reason === "daily_limit_reached" ? " (daily limit)" : ""}
                </Badge>
                {c.status === "running" || c.status === "scheduled" ? (
                  <Button variant="secondary" disabled={!!busy} onClick={() => act(c, "pause")}>
                    Pause
                  </Button>
                ) : null}
                {c.status === "paused" ? (
                  <Button variant="secondary" disabled={!!busy} onClick={() => act(c, "resume")}>
                    Resume
                  </Button>
                ) : null}
                {ACTIVE.has(c.status) ? (
                  confirmStop === c.id ? (
                    <span className="flex items-center gap-1">
                      <Button variant="danger" disabled={!!busy} onClick={() => act(c, "stop")}>
                        Yes, stop it
                      </Button>
                      <Button variant="ghost" onClick={() => setConfirmStop("")}>
                        Keep
                      </Button>
                    </span>
                  ) : (
                    <Button variant="danger" disabled={!!busy} onClick={() => setConfirmStop(c.id)}>
                      Stop
                    </Button>
                  )
                ) : null}
                <Link href={`/campaigns/${c.id}`}>
                  <Button variant="secondary">View</Button>
                </Link>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
