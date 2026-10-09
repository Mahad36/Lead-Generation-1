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

export default function SettingsPage() {
  const [tab, setTab] = useState("workspace");
  const [s, setS] = useState<any>(null);
  const [providers, setProviders] = useState<AiProvider[]>([]);
  const [serviceName, setServiceName] = useState("");
  const [serviceDesc, setServiceDesc] = useState("");
  const [aiKey, setAiKey] = useState("");
  const [googleSecret, setGoogleSecret] = useState("");
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
    if (googleSecret.trim()) body.google_client_secret = googleSecret.trim();
    if (apifyToken.trim()) body.apify_token = apifyToken.trim();
    const updated = await api("/api/settings", { method: "PUT", body: JSON.stringify(body) });
    setS(updated);
    setAiKey("");
    setGoogleSecret("");
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
    { id: "gmail", label: "Gmail" },
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
          <div><Label>Target industries</Label><Input value={s.target_industries || ""} onChange={(e) => set("target_industries", e.target.value)} /></div>
          <div><Label>Signature</Label><Textarea value={s.default_signature} onChange={(e) => set("default_signature", e.target.value)} /></div>
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
          <p className="text-sm text-muted">This is how many emails go out today. The next batch waits until tomorrow.</p>
          <div>
            <Label>Emails per day</Label>
            <Input type="number" min={1} value={s.default_daily_send_limit} onChange={(e) => set("default_daily_send_limit", Number(e.target.value))} />
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
      {tab === "gmail" ? (
        <Card className="space-y-3">
          <p className="text-sm text-muted">
            Create an OAuth client in Google Cloud Console (Gmail send + user email scopes), then paste the credentials here.
            After saving, connect your inbox on the Email accounts page.
          </p>
          {s.google_oauth_configured ? (
            <div className="flex items-center gap-2 text-sm">
              <CheckCircle2 className="h-5 w-5 text-emerald-600" />
              <span className="font-medium">Google OAuth is set up</span>
              <Badge tone="ok">Connected</Badge>
              <span className="text-muted">Paste a new Client Secret below only to replace it.</span>
            </div>
          ) : (
            <p className="text-sm">Gmail connect stays disabled until both Client ID and Client Secret are saved.</p>
          )}
          <div className="space-y-2">
            <Label>Authorized redirect URIs</Label>
            <p className="text-xs text-muted">
              In Google Cloud Console → APIs & Services → Credentials → your OAuth client, add every URI below.
              `localhost` and `127.0.0.1` are different. A port mismatch causes Error 400: redirect_uri_mismatch.
            </p>
            {(s.google_redirect_uris?.length ? s.google_redirect_uris : [s.google_redirect_uri]).filter(Boolean).map((uri: string) => (
              <Input key={uri} readOnly value={uri} />
            ))}
          </div>
          <div>
            <Label>Google Client ID</Label>
            <Input value={s.google_client_id || ""} onChange={(e) => set("google_client_id", e.target.value)} />
          </div>
          <p className="text-sm">
            After saving,{" "}
            <Link href="/email-accounts" className="text-primary underline">
              connect Gmail
            </Link>
            .
          </p>
          <div>
            <Label>Google Client Secret</Label>
            <Input
              type="password"
              autoComplete="new-password"
              placeholder={s.google_client_secret_set ? "Saved — paste a new secret to replace it" : "GOCSPX-..."}
              value={googleSecret}
              onChange={(e) => setGoogleSecret(e.target.value)}
            />
          </div>
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
