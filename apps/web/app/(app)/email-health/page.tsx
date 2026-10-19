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
  acknowledged: boolean;
  holds_sending: boolean;
};
type Alerts = { open: Alert[]; resolved: Alert[] };
type DomainStat = { domain: string; inboxes: string[]; sent: number; bounced: number; rate: number; target: number };

/** Gmail and Outlook judge the whole domain, not only each inbox: its bounce rate across all its inboxes. */
function DomainCard({ d }: { d: DomainStat }) {
  const pct = (d.rate * 100).toFixed(1);
  const tone = d.sent < 20 ? "default" : d.rate >= 0.05 ? "bad" : d.rate >= d.target ? "warn" : "ok";
  const bar = Math.min(d.rate / 0.06, 1) * 100;
  return (
    <div className="space-y-2 rounded-lg border border-border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="font-medium">{d.domain}</div>
        <Badge tone={tone}>
          {d.sent < 20 ? "Too few emails to judge yet" : d.rate >= d.target ? "Above the 2% safe zone" : "Healthy"}
        </Badge>
      </div>
      <div className="text-sm">
        Bounce rate (last 30 days): <span className="text-lg font-semibold">{pct}%</span>{" "}
        <span className="text-muted">
          ({d.bounced} of {d.sent} emails, from {d.inboxes.length} inbox{d.inboxes.length === 1 ? "" : "es"})
        </span>
      </div>
      <div className="relative h-2 rounded-full bg-background">
        <div
          className={`h-2 rounded-full ${d.rate >= 0.05 ? "bg-red-500" : d.rate >= d.target ? "bg-yellow-500" : "bg-green-600"}`}
          style={{ width: `${Math.max(bar, 2)}%` }}
        />
        <div className="absolute top-[-3px] h-3.5 w-0.5 bg-foreground" style={{ left: `${(d.target / 0.06) * 100}%` }} title="2% target" />
      </div>
      <div className="flex justify-between text-xs text-muted">
        <span>0%</span>
        <span>2% target</span>
        <span>6%+</span>
      </div>
      <p className="text-xs text-muted">{d.inboxes.join(" · ")}</p>
    </div>
  );
}

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

function AlertCard({ alert, onChange }: { alert: Alert; onChange?: () => void }) {
  const resolved = !!alert.resolved_at;
  const acknowledge = (undo: boolean) =>
    api(`/api/health-alerts/${alert.id}/${undo ? "unacknowledge" : "acknowledge"}`, { method: "POST" })
      .then(() => {
        toast.success(undo ? "This warning holds sending again" : "Reviewed: this warning no longer holds sending");
        onChange?.();
      })
      .catch((e) => toast.error(e.message));
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
            {!resolved && alert.holds_sending ? <Badge tone="bad">Holds sending</Badge> : null}
            {!resolved && alert.acknowledged ? <Badge>Reviewed by you</Badge> : null}
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
      {!resolved && alert.severity === "warning" && (alert.holds_sending || alert.acknowledged) ? (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted">
          {alert.acknowledged ? (
            <Button variant="secondary" onClick={() => acknowledge(true)}>
              Hold sending again
            </Button>
          ) : (
            <Button variant="secondary" onClick={() => acknowledge(false)}>
              I&apos;ve reviewed this, allow sending
            </Button>
          )}
          <span>
            Warnings only. The inbox still sends at reduced capacity while it lasts, and it&apos;s held again if it gets
            worse. Critical alerts can&apos;t be overridden.
          </span>
        </div>
      ) : null}
    </Card>
  );
}

export default function EmailHealthPage() {
  const [data, setData] = useState<Alerts | null>(null);
  const [checking, setChecking] = useState(false);
  const [permission, setPermission] = useState<string>("unsupported");
  const [policy, setPolicy] = useState<"strict" | "balanced" | "">("");
  const [domains, setDomains] = useState<DomainStat[]>([]);

  useEffect(() => {
    api<Alerts>("/api/health-alerts")
      .then(setData)
      .catch((e) => toast.error(e.message));
    api("/api/health-alerts/read", { method: "POST" }).catch(() => undefined);
    api<DomainStat[]>("/api/health-alerts/domains")
      .then(setDomains)
      .catch(() => undefined);
    api<{ health_policy?: "strict" | "balanced" }>("/api/settings")
      .then((s) => setPolicy(s.health_policy || "strict"))
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (typeof Notification !== "undefined") setPermission(Notification.permission);
  }, []);

  function reload() {
    api<Alerts>("/api/health-alerts")
      .then(setData)
      .catch((e) => toast.error(e.message));
  }

  async function savePolicy(next: "strict" | "balanced") {
    try {
      await api("/api/settings", { method: "PUT", body: JSON.stringify({ health_policy: next }) });
      setPolicy(next);
      toast.success(next === "strict" ? "Strict protection on" : "Balanced protection on");
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

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
            <AlertCard key={a.id} alert={a} onChange={reload} />
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

      {domains.length ? (
        <Card className="space-y-2">
          <div className="font-medium">Your domain{domains.length > 1 ? "s" : ""}</div>
          <p className="text-sm text-muted">
            Gmail and Outlook judge your whole domain as well as each inbox, so this is the number to keep under 2%.
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            {domains.map((d) => (
              <DomainCard key={d.domain} d={d} />
            ))}
          </div>
        </Card>
      ) : null}

      <Card className="space-y-2">
        <div className="font-medium">Account health protection</div>
        <p className="text-sm text-muted">
          Before every email the app checks, in order: the inbox&apos;s health, Gmail restrictions, today&apos;s safe
          capacity, then your daily limit. If any check fails, nothing is sent from that inbox, even if it has emails left
          today. Your daily limit is a maximum, not a target. Emails wait as &ldquo;Paused &mdash; Account Health
          Protection&rdquo; and go out by themselves once an inbox passes every check. They move to another inbox
          only if that one passes every check itself, and never to get round a Gmail restriction.
        </p>
        <div className="flex flex-wrap gap-4 text-sm">
          <label className="flex items-start gap-2">
            <input type="radio" className="mt-1" checked={policy === "strict"} onChange={() => savePolicy("strict")} />
            <span>
              <span className="font-medium">Strict (recommended)</span>
              <span className="block text-xs text-muted">Hold sending on any credible warning sign.</span>
            </span>
          </label>
          <label className="flex items-start gap-2">
            <input type="radio" className="mt-1" checked={policy === "balanced"} onChange={() => savePolicy("balanced")} />
            <span>
              <span className="font-medium">Balanced</span>
              <span className="block text-xs text-muted">
                Hold only on serious signs; other warnings halve the inbox&apos;s capacity instead.
              </span>
            </span>
          </label>
        </div>
        <p className="text-xs text-muted">
          Whatever you choose, these always hold sending: Gmail blocking your emails, a blacklisted domain, 5% or more
          bounces, missing SPF/DKIM, your emails landing in spam, Gmail limits, and a broken login. If the app can&apos;t
          check an inbox&apos;s health, that inbox waits too.
        </p>
      </Card>

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
