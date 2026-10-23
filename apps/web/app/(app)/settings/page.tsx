"use client";

import { Badge, Button, Card, Input, Label, Textarea } from "@/components/ui";
import { CheckCircle2 } from "lucide-react";
import { api } from "@/lib/api";
import Link from "next/link";
import { useEffect, useState } from "react";
import { toast } from "sonner";

type AiProvider = {
  id: string;
  label: string;
  api_base: string;
  models: string[];
  key_hint: string;
  docs_url?: string;
};

/** Optional mailbox verification with a provider's free allowance (apps/api/app/services/email_verification.py). */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function EmailVerification({ s, set }: { s: any; set: (key: string, value: any) => void }) {
  const [key, setKey] = useState("");
  const [testing, setTesting] = useState(false);
  const usage = s.email_verifier_usage || {};
  const provider = s.email_verifier_provider || "";

  async function test() {
    setTesting(true);
    try {
      const res = await api<{ message: string }>("/api/settings/email-verifier/test", {
        method: "POST",
        body: JSON.stringify({ provider, key, url: s.email_verifier_url || "" }),
      });
      toast.success(res.message);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="space-y-3 rounded-lg border border-border p-3">
      <div>
        <Label>Email verification before outreach (optional)</Label>
        <p className="text-xs text-muted">
          Checks whether a mailbox really exists before its audit starts, so a dead address costs no email and no
          Claude tokens. Uses a verification service&apos;s free monthly allowance (ZeroBounce and Hunter both have one;
          check their sites for the current amount). Only the address&apos;s result is stored; it&apos;s checked once.
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <Label>Service</Label>
          <select
            className="w-full rounded-md border border-border bg-card px-3 py-2 text-sm"
            value={provider}
            onChange={(e) => set("email_verifier_provider", e.target.value)}
          >
            <option value="">Off (free checks only)</option>
            <option value="own">My own checker (apps/verifier on your server)</option>
            <option value="zerobounce">ZeroBounce</option>
            <option value="hunter">Hunter</option>
          </select>
        </div>
        {provider === "own" ? (
          <div>
            <Label>Checker address</Label>
            <Input
              value={s.email_verifier_url || ""}
              placeholder="https://verify.your-check-domain.com"
              onChange={(e) => set("email_verifier_url", e.target.value)}
            />
          </div>
        ) : null}
        {provider ? (
          <div>
            <Label>
              {provider === "own" ? "Token" : "API key"} {s.email_verifier_key_set ? "(saved; paste to replace)" : ""}
            </Label>
            <div className="flex gap-2">
              <Input
                type="password"
                value={key}
                placeholder={s.email_verifier_key_set ? "••••••••" : "Paste your API key"}
                onChange={(e) => {
                  setKey(e.target.value);
                  set("email_verifier_key", e.target.value);
                }}
              />
              <Button variant="secondary" disabled={testing} onClick={test}>
                {testing ? "Testing..." : "Test key"}
              </Button>
            </div>
          </div>
        ) : null}
      </div>
      {provider ? (
        <>
          <div className="flex flex-wrap gap-4 text-sm">
            <label className="flex items-start gap-2">
              <input
                type="radio"
                className="mt-1"
                checked={(s.email_verifier_scope || "risky") === "risky"}
                onChange={() => set("email_verifier_scope", "risky")}
              />
              <span>
                Risky leads only (recommended)
                <span className="block text-xs text-muted">
                  An address not on the website&apos;s own domain, or a business type that has bounced 5%+ for you.
                  Makes a small free allowance last.
                </span>
              </span>
            </label>
            <label className="flex items-start gap-2">
              <input
                type="radio"
                className="mt-1"
                checked={s.email_verifier_scope === "all"}
                onChange={() => set("email_verifier_scope", "all")}
              />
              <span>
                Every lead
                <span className="block text-xs text-muted">Uses the allowance faster.</span>
              </span>
            </label>
          </div>
          {provider === "own" ? (
            <p className="text-xs text-muted">
              Your own checker has no monthly allowance, so &ldquo;Every lead&rdquo; is fine. It can&apos;t confirm
              addresses on Google Workspace (Google accepts every address there): those come back &ldquo;accepts every
              address&rdquo; and are sent as before.
            </p>
          ) : null}
          <div className={`flex flex-wrap items-end gap-3 ${provider === "own" ? "hidden" : ""}`}>
            <div>
              <Label>Checks per month (stay within the free allowance)</Label>
              <Input
                type="number"
                min={0}
                className="w-32"
                value={s.email_verifier_monthly_limit ?? 100}
                onChange={(e) => set("email_verifier_monthly_limit", Number(e.target.value))}
              />
            </div>
            <p className="pb-2 text-sm">
              This month: <span className="font-medium">{usage.used ?? 0}</span> of {usage.limit ?? s.email_verifier_monthly_limit ?? 100}
              {usage.exhausted ? " · the service says the allowance is used up; outreach carries on with the free checks" : ""}
            </p>
          </div>
          <p className="text-xs text-muted">
            Only a &ldquo;doesn&apos;t exist&rdquo; answer stops an email. &ldquo;Accepts every address&rdquo; or
            &ldquo;unknown&rdquo; goes out as before, and when the monthly checks are used up nothing stops: the free
            checks still apply.
          </p>
        </>
      ) : null}
    </div>
  );
}

/** apps/api/app/services/automations.py::AUTOMATIONS */
const AUTOMATION_OPTIONS: [string, string, string][] = [
  ["online_booking", "Online booking with automatic reminders", "Book any hour; confirmations and reminders go out by themselves."],
  ["instant_reply", "Instant reply to every enquiry", "Every form or quote request gets a reply within a minute."],
  ["ai_assistant", "AI assistant on the website", "Answers questions and takes bookings 24/7."],
  ["review_requests", "Automatic review requests", "A Google review request after each visit or job."],
  ["quote_followup", "Automatic quote follow-ups", "Friendly follow-ups on quotes that haven't been accepted."],
  ["missed_call_textback", "Missed-call text-back", "A missed call gets an instant text with a booking link."],
];

export default function SettingsPage() {
  const [tab, setTab] = useState("workspace");
  const [s, setS] = useState<any>(null);
  const [providers, setProviders] = useState<AiProvider[]>([]);
  const [serviceName, setServiceName] = useState("");
  const [serviceDesc, setServiceDesc] = useState("");
  const [aiKey, setAiKey] = useState("");
  const [apifyToken, setApifyToken] = useState("");
  const [replacingApify, setReplacingApify] = useState(false);
  const [testingAi, setTestingAi] = useState(false);

  useEffect(() => {
    Promise.all([api("/api/settings"), api<AiProvider[]>("/api/settings/ai-providers")])
      .then(([settings, catalog]) => {
        setS(settings);
        setProviders(catalog);
        // Deep link, e.g. /settings?tab=apify from the Scraping page
        const requested = new URLSearchParams(window.location.search).get("tab");
        if (requested) setTab(requested);
      })
      .catch((e) => toast.error(e.message));
  }, []);

  if (!s) return <p className="text-muted">Loading…</p>;

  function set(key: string, value: any) {
    setS((cur: any) => ({ ...cur, [key]: value }));
  }

  async function save() {
    const {
      ai_configured,
      google_oauth_configured,
      ai_api_key_set,
      google_client_secret_set,
      google_redirect_uri,
      google_redirect_uris,
      apify_configured,
      apify_token_set,
      ...rest
    } = s;
    const body: Record<string, unknown> = { ...rest };
    if (aiKey.trim()) body.ai_api_key = aiKey.trim();
    if (apifyToken.trim()) body.apify_token = apifyToken.trim();
    const updated = await api("/api/settings", { method: "PUT", body: JSON.stringify(body) });
    setS(updated);
    setAiKey("");
    setApifyToken("");
    setReplacingApify(false);
    toast.success("Settings saved");
  }

  async function testAi() {
    setTestingAi(true);
    try {
      // Claude Code can take a while to start, so allow longer than the default request timeout.
      const res = await api<{ ok: boolean; sample: string }>("/api/settings/test-ai", {
        method: "POST",
        signal: AbortSignal.timeout(180_000),
      });
      toast.success(res.sample ? `AI responded: ${res.sample}` : "AI key works");
    } finally {
      setTestingAi(false);
    }
  }

  const tabs = [
    { id: "workspace", label: "Workspace" },
    { id: "sending", label: "Sending" },
    { id: "ai", label: "AI setup" },
    { id: "apify", label: "Google Maps leads" },
  ];

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Settings</h1>
      <div className="flex flex-wrap gap-2">
        {tabs.map((t) => (
          <Button key={t.id} variant={tab === t.id ? "primary" : "secondary"} onClick={() => setTab(t.id)}>
            {t.label}
          </Button>
        ))}
      </div>
      {tab === "workspace" ? (
        <Card className="space-y-3">
          <div><Label>Sender name</Label><Input value={s.sender_name} onChange={(e) => set("sender_name", e.target.value)} /></div>
          <div><Label>Company name</Label><Input value={s.company_name} onChange={(e) => set("company_name", e.target.value)} /></div>
          <div><Label>Company website</Label><Input value={s.company_website} onChange={(e) => set("company_website", e.target.value)} /></div>
          <div><Label>Contact email</Label><Input value={s.contact_email} onChange={(e) => set("contact_email", e.target.value)} /></div>
          <div><Label>Description</Label><Textarea value={s.company_description} onChange={(e) => set("company_description", e.target.value)} /></div>
          <div>
            <Label>Portfolio Google Sheet (reference sites)</Label>
            <Input
              value={s.portfolio_sheet_url || ""}
              placeholder="https://docs.google.com/spreadsheets/d/..."
              onChange={(e) => set("portfolio_sheet_url", e.target.value)}
            />
            <PortfolioStatus saved={s.portfolio_sheet_url || ""} />
          </div>
          <div>
            <Label>Free offers for cold emails (one per line)</Label>
            <Textarea
              rows={4}
              value={s.free_offers || ""}
              placeholder={"A free redesign of the first screen of your homepage, as a Figma mockup\nA free landing page design for your main service\nA free 10-minute screen-recorded walkthrough of the fixes on your own site"}
              onChange={(e) => set("free_offers", e.target.value)}
            />
            <p className="mt-1 text-xs text-muted">
              Each email offers one of these, the one that fits that website&apos;s biggest problem. Leave empty to use the
              three examples shown.
            </p>
          </div>
          <div className="rounded-lg border border-border p-3">
            <Label>What your emails lead with</Label>
            <div className="mt-2 grid gap-2 sm:grid-cols-3">
              {(
                [
                  ["balanced", "Website issues first, then automation (recommended)", "Opens with the main website finding and its fix, then proposes one automation that fits the business."],
                  ["automation", "Automation first", "Opens with what the site shows that an automation would fix, pitches that automation; the website fix is a short supporting point. The PDF shows automations near the top."],
                  ["website", "Website first", "Website redesign and fixes; an automation only as an optional extra."],
                ] as const
              ).map(([key, title, hint]) => (
                <label
                  key={key}
                  className={`cursor-pointer rounded-lg border px-3 py-2 text-sm ${
                    (s.outreach_focus || "balanced") === key ? "border-primary ring-2 ring-primary/30" : "border-border"
                  }`}
                >
                  <input
                    type="radio"
                    className="mr-2"
                    checked={(s.outreach_focus || "balanced") === key}
                    onChange={() => set("outreach_focus", key)}
                  />
                  <span className="font-medium">{title}</span>
                  <span className="block text-xs text-muted">{hint}</span>
                </label>
              ))}
            </div>
            <p className="mt-2 text-xs text-muted">
              Free offers follow the focus when you haven&apos;t written your own (e.g. a free automation plan). Your other
              services (SEO, social media...) can come up in one sentence, only when the audit found something they fix.
            </p>
          </div>
          <div className="rounded-lg border border-border p-3">
            <Label>Automations you offer</Label>
            <p className="mb-2 text-xs text-muted">
              Each audit picks the one that fits the business best, from what its website shows (e.g. booking by phone
              only, a contact form nobody answers straight away) and its type of business. The email proposes it in one
              short paragraph and the subject line is built around it; the PDF gets a &ldquo;What Could Run
              Automatically&rdquo; page. Only the ones ticked here are ever offered.
            </p>
            <div className="grid gap-2 sm:grid-cols-2">
              {AUTOMATION_OPTIONS.map(([key, title, hint]) => {
                const chosen = String(s.automation_offers ?? "").split(",").filter(Boolean);
                const on = chosen.includes(key);
                return (
                  <label key={key} className="flex items-start gap-2 text-sm">
                    <input
                      type="checkbox"
                      className="mt-1"
                      checked={on}
                      onChange={() =>
                        set("automation_offers", (on ? chosen.filter((k) => k !== key) : [...chosen, key]).join(","))
                      }
                    />
                    <span>
                      {title}
                      <span className="block text-xs text-muted">{hint}</span>
                    </span>
                  </label>
                );
              })}
            </div>
          </div>
          <div><Label>Target industries</Label><Input value={s.target_industries || ""} onChange={(e) => set("target_industries", e.target.value)} /></div>
          <div className="space-y-2">
            <Label>Your services</Label>
            {(s.services || []).map((svc: any, i: number) => (
              <div key={svc.id || i} className="flex gap-2">
                <Input value={svc.name} onChange={(e) => {
                  const services = [...s.services];
                  services[i] = { ...svc, name: e.target.value };
                  set("services", services);
                }} />
                <Button variant="ghost" onClick={() => set("services", s.services.filter((_: any, idx: number) => idx !== i))}>Remove</Button>
              </div>
            ))}
            <div className="flex gap-2">
              <Input placeholder="Service name" value={serviceName} onChange={(e) => setServiceName(e.target.value)} />
              <Input placeholder="Description" value={serviceDesc} onChange={(e) => setServiceDesc(e.target.value)} />
              <Button variant="secondary" onClick={() => {
                if (!serviceName) return;
                set("services", [...(s.services || []), { name: serviceName, description: serviceDesc, is_active: true, sort_order: (s.services || []).length }]);
                setServiceName("");
                setServiceDesc("");
              }}>Add</Button>
            </div>
          </div>
        </Card>
      ) : null}
      {tab === "sending" ? (
        <Card className="space-y-3">
          <p className="text-sm text-muted">This is how many emails go out per working day. The next batch waits for the next one.</p>
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <Label>Emails per day</Label>
              <Input type="number" min={1} value={s.default_daily_send_limit} onChange={(e) => set("default_daily_send_limit", Number(e.target.value))} />
            </div>
            <div>
              <Label>Daily limits reset at</Label>
              <select
                className="w-full rounded-md border border-border bg-card px-3 py-2 text-sm"
                value={s.day_start_hour ?? 0}
                onChange={(e) => set("day_start_hour", Number(e.target.value))}
              >
                {Array.from({ length: 24 }, (_, h) => (
                  <option key={h} value={h}>
                    {`${String(h).padStart(2, "0")}:00`}
                    {h === 0 ? " (midnight)" : ""}
                  </option>
                ))}
              </select>
              <p className="mt-1 text-xs text-muted">
                The start of your working day (your computer&apos;s time). Sent counts and audit counts go back to 0 then.
              </p>
            </div>
          </div>
          <label className="flex items-start gap-2 rounded-lg border border-border p-3 text-sm">
            <input
              type="checkbox"
              className="mt-1"
              checked={s.safe_addresses_only !== false}
              onChange={(e) => set("safe_addresses_only", e.target.checked)}
            />
            <span>
              <span className="font-medium">Only email safe address types (recommended)</span>
              <span className="block text-xs text-muted">
                Emails go only to: a general inbox on the business&apos;s own domain (info@, office@, hello@), a
                department (sales@...), a person named on the website, the mailbox for the lead&apos;s own location, or
                Gmail / Outlook / Yahoo. Riskier types (another company&apos;s domain, addresses that only look like a
                name, admin@, old internet-provider addresses) are blocked unless the verification service below
                confirms the mailbox; the app then tries the business&apos;s next safe address. Checked before the
                audit and again right before each email is sent.
              </span>
            </span>
          </label>
          <EmailVerification s={s} set={set} />
          <div className="rounded-lg border border-border p-3">
            <Label>Delay between emails from the same inbox (minutes, random each time)</Label>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-sm">
              <Input
                type="number"
                min={2}
                max={60}
                step={0.5}
                className="w-24"
                value={Math.round((s.default_min_send_delay_seconds / 60) * 2) / 2}
                onChange={(e) => set("default_min_send_delay_seconds", Math.round(Number(e.target.value) * 60))}
              />
              to
              <Input
                type="number"
                min={2}
                max={60}
                step={0.5}
                className="w-24"
                value={Math.round((s.default_max_send_delay_seconds / 60) * 2) / 2}
                onChange={(e) => set("default_max_send_delay_seconds", Math.round(Number(e.target.value) * 60))}
              />
              minutes
            </div>
            <p className="mt-1 text-xs text-muted">
              After every email, that inbox waits a random time in this range before its next one, so it never sends in
              a robotic rhythm. Each inbox waits on its own, so with rotation 3 inboxes send about 3 times as fast.
              Inboxes on the same own domain also stay at least 1 minute apart. 3 to 7 minutes is a good range for cold
              email; never less than 2 (Gmail rejects emails sent back to back).
            </p>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={s.skip_previously_contacted} onChange={(e) => set("skip_previously_contacted", e.target.checked)} />
            Skip businesses you already emailed
          </label>
          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" className="mt-1" checked={!!s.track_opens} onChange={(e) => set("track_opens", e.target.checked)} />
            <span>
              Track email opens
              <span className="block text-xs text-muted">
                Adds a tiny image from your own tracking domain to record when an email is opened. Emails never carry
                bulk-mail headers, which helps them reach Gmail&apos;s Primary tab. Replies and bounces are tracked
                either way.
              </span>
            </span>
          </label>
          <p className="text-sm">
            <Link href="/suppression" className="text-primary underline">
              Unsubscribes
            </Link>
          </p>
        </Card>
      ) : null}
      {tab === "sending" ? (
        <Card className="space-y-4">
          <div>
            <h2 className="font-medium">Deliverability</h2>
            <p className="text-sm text-muted">
              What cold-email tools do, built in. Your outreach flow stays the same; these run alongside it.
            </p>
          </div>

          <div className="space-y-2 rounded-lg border border-border p-3">
            <label className="flex items-start gap-2 text-sm">
              <input type="checkbox" className="mt-1" checked={!!s.followups_enabled} onChange={(e) => set("followups_enabled", e.target.checked)} />
              <span>
                <span className="font-medium">Follow-ups</span>
                <span className="block text-xs text-muted">
                  When a lead doesn&apos;t reply, a short follow-up written by Claude goes out in the same email thread,
                  from the same inbox, without the PDF. They stop as soon as the lead replies, bounces or unsubscribes.
                  Follow-ups count towards each inbox&apos;s daily limit and go out before new leads.
                </span>
              </span>
            </label>
            {s.followups_enabled ? (
              <div className="ml-6 max-w-xs">
                <Label>Days between emails</Label>
                <Input
                  value={s.followup_days ?? "3,5"}
                  placeholder="3,5"
                  onChange={(e) => set("followup_days", e.target.value)}
                />
                <p className="mt-1 text-xs text-muted">
                  &quot;3,5&quot; = follow-up 1 three days after the first email, follow-up 2 five days after that. Up to 5.
                </p>
              </div>
            ) : null}
          </div>

          <div className="space-y-2 rounded-lg border border-border p-3">
            <label className="flex items-start gap-2 text-sm">
              <input type="checkbox" className="mt-1" checked={!!s.send_window_enabled} onChange={(e) => set("send_window_enabled", e.target.checked)} />
              <span>
                <span className="font-medium">Sending hours</span>
                <span className="block text-xs text-muted">
                  Automatic emails go out only in these hours (your computer&apos;s time). Emails ready outside them wait
                  until the hours start. Pressing Send yourself always sends straight away.
                </span>
              </span>
            </label>
            {s.send_window_enabled ? (
              <div className="ml-6 flex flex-wrap items-end gap-3">
                <div>
                  <Label>From</Label>
                  <select
                    className="rounded-md border border-border bg-card px-3 py-2 text-sm"
                    value={s.send_window_start ?? 8}
                    onChange={(e) => set("send_window_start", Number(e.target.value))}
                  >
                    {Array.from({ length: 24 }, (_, h) => (
                      <option key={h} value={h}>{`${String(h).padStart(2, "0")}:00`}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <Label>Until</Label>
                  <select
                    className="rounded-md border border-border bg-card px-3 py-2 text-sm"
                    value={s.send_window_end ?? 19}
                    onChange={(e) => set("send_window_end", Number(e.target.value))}
                  >
                    {Array.from({ length: 24 }, (_, i) => i + 1).map((h) => (
                      <option key={h} value={h}>{`${String(h % 24).padStart(2, "0")}:00`}</option>
                    ))}
                  </select>
                </div>
                <label className="flex items-center gap-2 pb-2 text-sm">
                  <input type="checkbox" checked={!!s.send_weekdays_only} onChange={(e) => set("send_weekdays_only", e.target.checked)} />
                  Monday to Friday only
                </label>
              </div>
            ) : null}
          </div>

          <label className="flex items-start gap-2 rounded-lg border border-border p-3 text-sm">
            <input type="checkbox" className="mt-1" checked={!!s.check_email_domains} onChange={(e) => set("check_email_domains", e.target.checked)} />
            <span>
              <span className="font-medium">Skip emails that would bounce</span>
              <span className="block text-xs text-muted">
                Before auditing a lead, rules out addresses whose domain can&apos;t receive mail and moves on to the
                business&apos;s next address. Leads with none left are marked &quot;undeliverable&quot; and skipped.
                Whatever this is set to, every first email and follow-up gets a local risk check right before it is
                sent: address format, earlier bounces and the domain&apos;s DNS. If a check fails, can&apos;t be completed
                or is out of date, the email is held, not sent. This is not mailbox verification: it can&apos;t tell
                whether the mailbox itself exists.
              </span>
            </span>
          </label>

          <label className="flex items-start gap-2 rounded-lg border border-border p-3 text-sm">
            <input type="checkbox" className="mt-1" checked={!!s.warmup_enabled} onChange={(e) => set("warmup_enabled", e.target.checked)} />
            <span>
              <span className="font-medium">Warm-up between my inboxes</span>
              <span className="block text-xs text-muted">
                With 2 or more Gmails connected, they send each other a few natural emails a day during sending hours,
                rescue them from spam, mark them important and reply to some. Filed under a &quot;Warm-up&quot; label, out of
                your inbox. Switch it per inbox in Email setup.
              </span>
            </span>
          </label>

          <p className="text-xs text-muted">
            Also automatic: emails without a chosen inbox rotate across all your connected Gmails; a newly added Gmail
            starts at 5 emails a day and rises by 2 a day up to its limit; an inbox pauses itself if more than 5% of its
            recent emails bounce. See each inbox in{" "}
            <Link href="/email-accounts" className="text-primary underline">
              Email setup
            </Link>
            .
          </p>
        </Card>
      ) : null}
      {tab === "ai" ? (
        <Card className="space-y-3">
          <p className="text-sm text-muted">
            Choose ChatGPT, Claude, Gemini, or another provider. Each workspace uses its own key. Keys are encrypted and never shown again after you save.
          </p>
          <p className="text-sm">
            {s.ai_configured ? "AI is configured for this account." : "Choose Claude Code or add an API key to enable website audits and personalization."}
            {s.ai_api_key_set ? " A key is already saved." : ""}
          </p>
          <div>
            <Label>Provider</Label>
            <select
              className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
              value={s.ai_provider}
              onChange={(e) => {
                const next = providers.find((p) => p.id === e.target.value);
                setS((cur: any) => ({
                  ...cur,
                  ai_provider: e.target.value,
                  ai_api_base: next?.api_base ?? cur.ai_api_base,
                  ai_model: next?.models[0] || cur.ai_model,
                }));
              }}
            >
              {providers.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
              {!providers.some((p) => p.id === s.ai_provider) && s.ai_provider ? (
                <option value={s.ai_provider}>{s.ai_provider}</option>
              ) : null}
            </select>
          </div>
          {providers.find((p) => p.id === s.ai_provider)?.docs_url ? (
            <p className="text-sm">
              API Documentation →{" "}
              <a
                className="text-primary underline"
                href={providers.find((p) => p.id === s.ai_provider)?.docs_url}
                target="_blank"
                rel="noreferrer"
              >
                Open documentation
              </a>
            </p>
          ) : null}
          {s.ai_provider === "claude_code" ? (
            <div className="space-y-1 rounded-md border border-border p-3 text-sm">
              <p className="font-medium">No API key needed: uses your Claude subscription through Claude Code.</p>
              <p className="text-muted">
                One-time setup in the terminal where the app runs: <code>claude setup-token</code>, then add the token to
                the <code>.env</code> file as <code>CLAUDE_CODE_OAUTH_TOKEN=...</code> and restart (or run <code>claude</code>{" "}
                once and log in). Then click <b>Test AI connection</b>.
              </p>
              <p className="text-muted">{s.ai_configured ? "Claude Code is installed." : "Claude Code was not found on this machine."}</p>
            </div>
          ) : (
            <div>
              <Label>API key</Label>
              <Input
                type="password"
                autoComplete="new-password"
                placeholder={
                  s.ai_api_key_set
                    ? "Saved — paste a new key to replace it"
                    : providers.find((p) => p.id === s.ai_provider)?.key_hint || "sk-..."
                }
                value={aiKey}
                onChange={(e) => setAiKey(e.target.value)}
              />
            </div>
          )}
          <div>
            <Label>Model</Label>
            {providers.find((p) => p.id === s.ai_provider)?.models.length ? (
              <select
                className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                value={
                  providers.find((p) => p.id === s.ai_provider)?.models.includes(s.ai_model)
                    ? s.ai_model
                    : "__custom"
                }
                onChange={(e) => {
                  if (e.target.value !== "__custom") set("ai_model", e.target.value);
                }}
              >
                {providers.find((p) => p.id === s.ai_provider)?.models.map((model) => (
                  <option key={model} value={model}>
                    {model}
                  </option>
                ))}
                <option value="__custom">Custom model…</option>
              </select>
            ) : null}
            {!providers.find((p) => p.id === s.ai_provider)?.models.includes(s.ai_model) ||
            !providers.find((p) => p.id === s.ai_provider)?.models.length ? (
              <Input className="mt-2" value={s.ai_model} onChange={(e) => set("ai_model", e.target.value)} />
            ) : null}
          </div>
          <Button variant="secondary" disabled={testingAi} onClick={() => testAi().catch((e) => toast.error(e.message))}>
            {testingAi ? "Testing…" : "Test AI connection"}
          </Button>
        </Card>
      ) : null}
      {tab === "apify" ? (
        <Card className="space-y-4">
          <div>
            <Label>Where leads come from</Label>
            <div className="mt-1 grid gap-2 sm:grid-cols-2">
              {(
                [
                  ["scraper", "Built-in scraper (free)", "Searches Google Maps in a headless browser on this PC and checks each business website for emails and social links. No account needed."],
                  ["apify", "Apify (API token)", "Runs the search on Apify's servers (paid by usage). Needs your Apify API token below."],
                ] as const
              ).map(([value, title, hint]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => set("scraper_provider", value)}
                  className={`rounded-xl border px-4 py-3 text-left text-sm ${s.scraper_provider === value ? "border-primary ring-2 ring-primary/30" : "border-border"}`}
                >
                  <div className="font-medium">{title}</div>
                  <div className="mt-1 text-xs text-muted">{hint}</div>
                </button>
              ))}
            </div>
          </div>
          {s.scraper_provider === "apify" ? (
            <>
              <p className="text-sm text-muted">
                Create a free account at{" "}
                <a href="https://apify.com" target="_blank" rel="noreferrer" className="text-primary underline">
                  apify.com
                </a>
                , then copy your token from{" "}
                <a
                  href="https://console.apify.com/settings/integrations"
                  target="_blank"
                  rel="noreferrer"
                  className="text-primary underline"
                >
                  Settings → API &amp; Integrations
                </a>
                .
              </p>
              {s.apify_token_set && !replacingApify ? (
                <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border p-3">
                  <div className="flex items-center gap-2 text-sm">
                    <CheckCircle2 className="h-5 w-5 text-emerald-600" />
                    <span className="font-medium">Apify token saved</span>
                    <Badge tone="ok">Connected</Badge>
                  </div>
                  <Button variant="secondary" onClick={() => setReplacingApify(true)}>
                    Replace token
                  </Button>
                </div>
              ) : (
                <div className="space-y-1">
                  <Label>Apify API token</Label>
                  <Input
                    type="password"
                    autoComplete="new-password"
                    placeholder="apify_api_..."
                    value={apifyToken}
                    onChange={(e) => setApifyToken(e.target.value)}
                  />
                  <p className={`text-xs ${apifyToken.trim() ? "text-muted" : "text-red-600"}`}>
                    {apifyToken.trim()
                      ? "Click Save settings to use this token."
                      : s.apify_token_set
                        ? "Paste the new token, then Save settings (the current one stays until you do)."
                        : "Add your Apify API token to use Apify."}
                  </p>
                  {s.apify_token_set ? (
                    <Button
                      variant="ghost"
                      onClick={() => {
                        setReplacingApify(false);
                        setApifyToken("");
                      }}
                    >
                      Keep the saved token
                    </Button>
                  ) : null}
                </div>
              )}
            </>
          ) : (
            <p className="text-sm text-muted">
              Fast mode: about 20 businesses in 15–30 seconds, including website email lookups. Google may show a CAPTCHA
              after very heavy use; the scrape then stops and keeps the leads already found. Try again later or switch to
              Apify.
            </p>
          )}
        </Card>
      ) : null}
      <Button onClick={() => save().catch((e) => toast.error(e.message))}>Save settings</Button>
    </div>
  );
}

type PortfolioInfo = {
  configured: boolean;
  error?: string;
  sites?: number;
  niches?: number;
  alive?: number;
  dead?: number;
  checking?: number;
};

/** What the app read from the saved portfolio sheet (sites, niches, how many still load). */
function PortfolioStatus({ saved }: { saved: string }) {
  const [info, setInfo] = useState<PortfolioInfo | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!saved) return;
    api<PortfolioInfo>("/api/settings/portfolio").then(setInfo).catch(() => setInfo(null));
  }, [saved]);

  async function reload() {
    setBusy(true);
    try {
      setInfo(await api<PortfolioInfo>("/api/settings/portfolio/reload", { method: "POST" }));
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!saved) {
    return (
      <p className="mt-1 text-xs text-muted">
        Paste the sheet link (Share: &quot;Anyone with the link&quot;, Viewer) and Save settings. One column per niche with the
        sites under it, or one row per site with URL and niche columns. Every email then mentions one site you built in
        the lead&apos;s niche.
      </p>
    );
  }
  return (
    <div className="mt-1 flex flex-wrap items-center gap-2 text-xs">
      {info?.error ? <span className="text-red-600">{info.error}</span> : null}
      {info && !info.error && info.sites !== undefined ? (
        <>
          <Badge tone="ok">
            {info.sites} sites in {info.niches} niches
          </Badge>
          <span className="text-muted">
            {info.alive} live{info.dead ? `, ${info.dead} not loading (never used)` : ""}
            {info.checking ? `, checking ${info.checking}...` : ""}
          </span>
        </>
      ) : null}
      <Button variant="secondary" className="h-7 py-0.5" disabled={busy} onClick={reload}>
        {busy ? "Reading..." : "Reload sheet"}
      </Button>
    </div>
  );
}
