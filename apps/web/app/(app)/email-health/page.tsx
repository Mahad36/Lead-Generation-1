"use client";

import { Badge, Button, Card, Empty } from "@/components/ui";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/utils";
import { BellRing, RefreshCw, ShieldAlert, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

type Alert = {
  id: string;
  kind: string;
  severity: "warning" | "critical";
  subject: string;
  title: string;
  message: string;
  advice: string;
  since: string | null;
  last_seen_at: string | null;
  resolved_at: string | null;
};
type Alerts = { open: Alert[]; resolved: Alert[] };

/** What each number on an inbox's "Sending health" card means, and where it starts to hurt. */
const GUIDE: { signal: string; good: string; watch: string; act: string }[] = [
  { signal: "Bounce rate", good: "under 2%", watch: "2-3%", act: "3% or more: the app halves sending; 5%+ is critical" },
  { signal: "Unsubscribe requests", good: "under 1%", watch: "1-2%", act: "2% or more: people who'd report spam" },
  { signal: "Gmail errors (24h)", good: "none", watch: "1-2 temporary errors", act: "\"blocked as against policy\": pause the inbox" },
  { signal: "Warm-up emails in spam (7 days)", good: "0-5%", watch: "10%+", act: "25%+: leads' copies are going to spam too" },
  { signal: "Spam placement test", good: "Inbox or Promotions", watch: "-", act: "Spam: lower volume, simplify the email" },
  { signal: "Replies", good: "a few every week", watch: "-", act: "none after 60+ emails: check placement and targeting" },
  { signal: "Daily limit per inbox", good: "25-30", watch: "30-40", act: "over 40 for cold email" },
  { signal: "Own domain: SPF, DKIM, DMARC", good: "all 3 set up", watch: "DMARC missing", act: "SPF or DKIM missing" },
  { signal: "Own domain: blacklists", good: "not listed", watch: "-", act: "listed: stop outreach from the domain" },
];

const STATUS = [
  { name: "Healthy", text: "No warning signs. Sends up to your limit." },
  { name: "Monitoring", text: "New inbox (still ramping up) or small early signs. Full capacity, watched." },
  { name: "Reduced sending", text: "A warning sign crossed its line. The app halves sending until it clears." },
  { name: "Temporarily paused", text: "Gmail limit or repeated errors. Nothing is sent until it clears." },
  { name: "Reconnection required", text: "The App Password stopped working. Replace it in Email setup." },
];

const MANUAL = [
  "Once a week: send one real outreach email to your own Gmail and Outlook addresses. It should land in the Inbox.",
  "After changing the email template: test it on mail-tester.com (free, aim for 9/10 or more).",
  "Own domain: add it to Google Postmaster Tools (postmaster.google.com). Domain reputation should stay High or Medium and the spam rate under 0.1%. Gmail only shares these there, not with apps.",
];

function AlertCard({ alert }: { alert: Alert }) {
  const resolved = !!alert.resolved_at;
  return (
    <Card className={resolved ? "opacity-70" : alert.severity === "critical" ? "border-red-400" : "border-yellow-400"}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            {resolved ? (
              <Badge tone="ok">Resolved</Badge>
            ) : (
              <Badge tone={alert.severity === "critical" ? "bad" : "warn"}>
                {alert.severity === "critical" ? "Act now" : "Warning"}
              </Badge>
            )}
            <span className="font-medium">{alert.title}</span>
          </div>
          <div className="text-sm text-muted">{alert.subject}</div>
        </div>
        <div className="text-right text-xs text-muted">
          {resolved ? `Cleared ${formatDate(alert.resolved_at!)}` : alert.since ? `Since ${formatDate(alert.since)}` : ""}
        </div>
      </div>
      {alert.message ? <p className="mt-2 text-sm">{alert.message}</p> : null}
      {alert.advice && !resolved ? (
        <p className="mt-2 rounded-md bg-background p-2 text-sm">
          <span className="font-medium">What to do: </span>
          {alert.advice}
        </p>
      ) : null}
    </Card>
  );
}

export default function EmailHealthPage() {
  const [data, setData] = useState<Alerts | null>(null);
  const [checking, setChecking] = useState(false);
  const [permission, setPermission] = useState<string>("unsupported");

  useEffect(() => {
    api<Alerts>("/api/health-alerts")
      .then(setData)
      .catch((e) => toast.error(e.message));
    api("/api/health-alerts/read", { method: "POST" }).catch(() => undefined);
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (typeof Notification !== "undefined") setPermission(Notification.permission);
  }, []);

  async function checkNow() {
    setChecking(true);
    try {
      const result = await api<Alerts & { new: number; resolved: number }>("/api/health-alerts/check", { method: "POST" });
      setData(result);
      toast.success(result.open.length ? `${result.open.length} open alert(s)` : "Checked: no warning signs");
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setChecking(false);
    }
  }

  async function enableDesktop() {
    if (typeof Notification === "undefined") return;
    const result = await Notification.requestPermission();
    setPermission(result);
    if (result === "granted") new Notification("Email health alerts are on", { body: "You'll be told here when something needs attention." });
  }

  const open = data?.open ?? [];
  const critical = open.filter((a) => a.severity === "critical").length;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Email health</h1>
          <p className="text-sm text-muted">
            Warnings when something suggests your emails could start going to spam. Checked every 5 minutes; domain
            records and blacklists once a day.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {permission === "default" || permission === "denied" ? (
            <Button variant="secondary" onClick={enableDesktop} disabled={permission === "denied"}>
              <BellRing className="mr-1 h-4 w-4" />
              {permission === "denied" ? "Desktop alerts blocked in the browser" : "Turn on desktop alerts"}
            </Button>
          ) : permission === "granted" ? (
            <Badge tone="ok">Desktop alerts on</Badge>
          ) : null}
          <Button onClick={checkNow} disabled={checking}>
            <RefreshCw className={`mr-1 h-4 w-4 ${checking ? "animate-spin" : ""}`} />
            {checking ? "Checking..." : "Check now"}
          </Button>
        </div>
      </div>

      {data === null ? (
        <Empty title="Loading..." />
      ) : open.length ? (
        <>
          <div className="flex items-center gap-2 text-sm font-medium">
            <ShieldAlert className={`h-4 w-4 ${critical ? "text-red-500" : "text-yellow-500"}`} />
            {open.length} open alert{open.length > 1 ? "s" : ""}
            {critical ? ` · ${critical} need action now` : ""}
          </div>
          {open.map((a) => (
            <AlertCard key={a.id} alert={a} />
          ))}
        </>
      ) : (
        <Card className="flex items-center gap-3">
          <ShieldCheck className="h-6 w-6 text-green-600" />
          <div>
            <div className="font-medium">No warning signs</div>
            <div className="text-sm text-muted">Every inbox and domain is within the safe ranges below.</div>
          </div>
        </Card>
      )}

      <Card>
        <div className="mb-2 font-medium">How to read your inbox&apos;s numbers</div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-muted">
              <tr>
                <th className="py-1 pr-3">Signal</th>
                <th className="py-1 pr-3">Healthy</th>
                <th className="py-1 pr-3">Watch</th>
                <th className="py-1">Act</th>
              </tr>
            </thead>
            <tbody>
              {GUIDE.map((g) => (
                <tr key={g.signal} className="border-t border-border">
                  <td className="py-1.5 pr-3 font-medium">{g.signal}</td>
                  <td className="py-1.5 pr-3 text-green-700 dark:text-green-400">{g.good}</td>
                  <td className="py-1.5 pr-3 text-yellow-700 dark:text-yellow-400">{g.watch}</td>
                  <td className="py-1.5 text-red-700 dark:text-red-400">{g.act}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="mt-3 grid gap-1 text-sm sm:grid-cols-2">
          {STATUS.map((s) => (
            <div key={s.name}>
              <span className="font-medium">{s.name}: </span>
              <span className="text-muted">{s.text}</span>
            </div>
          ))}
        </div>
        <p className="mt-3 text-xs text-muted">
          Rates are only judged once there&apos;s enough sending to mean something (bounces after 20 emails,
          unsubscribes after 50), so 1 bounce in your first 10 emails doesn&apos;t raise an alarm.
        </p>
      </Card>

      <Card>
        <div className="mb-2 font-medium">Checks only you can do (Gmail doesn&apos;t share these with apps)</div>
        <ul className="list-disc space-y-1 pl-5 text-sm">
          {MANUAL.map((m) => (
            <li key={m}>{m}</li>
          ))}
        </ul>
      </Card>

      {data?.resolved.length ? (
        <>
          <div className="text-sm font-medium text-muted">Resolved in the last 30 days</div>
          {data.resolved.map((a) => (
            <AlertCard key={a.id} alert={a} />
          ))}
        </>
      ) : null}
    </div>
  );
}
