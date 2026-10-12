"use client";

import { useHealth } from "@/components/app-shell";
import { useEmailAccounts } from "@/components/send-from";
import { Button, Card } from "@/components/ui";
import { api } from "@/lib/api";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

type Dash = {
  total_leads: number;
  leads_with_email: number;
  websites_analyzed: number;
  average_website_score: number | null;
  average_lead_score: number | null;
  emails_sent_today: number;
  remaining_daily_capacity: number | null;
  active_campaigns: number;
  completed_campaigns: number;
  failed_emails: number;
  bounce_count: number;
  unsubscribe_count: number;
  suppressed_contacts: number;
  open_rate: number | null;
  platform_distribution: { platform: string; count: number }[];
  leads_by_country: { country: string; count: number }[];
  leads_by_category: { category: string; count: number }[];
};

export default function DashboardPage() {
  const [data, setData] = useState<Dash | null>(null);
  const [error, setError] = useState("");
  const health = useHealth();
  const accounts = useEmailAccounts();

  useEffect(() => {
    api<Dash>("/api/dashboard")
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);

  if (error) return <p className="text-danger">{error}</p>;
  if (!data) return <p className="text-muted">Loading dashboard…</p>;

  const cards = [
    ["Total leads", data.total_leads],
    ["Leads with email", data.leads_with_email],
    ["Websites analyzed", data.websites_analyzed],
    ["Avg website score", data.average_website_score ?? "—"],
    ["Emails sent today", data.emails_sent_today],
    ["Remaining today", data.remaining_daily_capacity ?? "—"],
    ["Active campaigns", data.active_campaigns],
    ["Failed emails", data.failed_emails],
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Dashboard</h1>
        <p className="text-sm text-muted">Your workspace only. Other users cannot see this data.</p>
      </div>
      <Card className="space-y-3">
        <h2 className="font-medium">Do this next</h2>
        <div className="grid gap-3 sm:grid-cols-3">
          <div className="rounded-md border border-border p-3">
            <p className="font-medium">1. Connect Gmail</p>
            <p className="mt-1 text-sm text-muted">Emails send from your own inbox.</p>
            <Link href="/email-accounts">
              <Button className="mt-3" variant="secondary">
                Open Gmail
              </Button>
            </Link>
          </div>
          <div className="rounded-md border border-border p-3">
            <p className="font-medium">2. Get leads</p>
            <p className="mt-1 text-sm text-muted">Each scrape or upload is its own file. Campaigns send from one file.</p>
            <Link href="/leads">
              <Button className="mt-3" variant="secondary">
                Add leads
              </Button>
            </Link>
          </div>
          <div className="rounded-md border border-border p-3">
            <p className="font-medium">3. Send</p>
            <p className="mt-1 text-sm text-muted">Start a scrape and it sends on its own, up to your daily limit.</p>
            <Link href="/scraping">
              <Button className="mt-3">
                Start a scrape
              </Button>
            </Link>
          </div>
        </div>
      </Card>
      {accounts && !accounts.some((a) => a.status === "connected") ? (
        <Card className="border-amber-300 bg-amber-50 text-amber-900 dark:bg-amber-950/40 dark:text-amber-100">
          No email account connected yet.{" "}
          <Link href="/email-accounts" className="underline">
            Connect a Gmail in Email setup
          </Link>{" "}
          (with an App Password) to start sending.
        </Card>
      ) : null}
      {!health?.ai_configured ? (
        <Card className="border-amber-300 bg-amber-50 text-amber-900 dark:bg-amber-950/40 dark:text-amber-100">
          Add your AI API key in Settings. Until then we send the template as-is.
        </Card>
      ) : null}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {cards.map(([label, value]) => (
          <Card key={String(label)}>
            <p className="text-sm text-muted">{label}</p>
            <p className="mt-2 text-2xl font-semibold">{value}</p>
          </Card>
        ))}
      </div>
      <p className="text-sm text-muted">
        Open rate is {data.open_rate === null ? "not claimed" : `${data.open_rate}%`} because open tracking is
        off unless you enable it.
      </p>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <h2 className="mb-4 font-medium">Platform distribution</h2>
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data.platform_distribution}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="platform" />
                <YAxis />
                <Tooltip />
                <Bar dataKey="count" fill="#2563eb" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card>
          <h2 className="mb-4 font-medium">Leads by country</h2>
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data.leads_by_country}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="country" />
                <YAxis />
                <Tooltip />
                <Bar dataKey="count" fill="#0f172a" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
      </div>
    </div>
  );
}
