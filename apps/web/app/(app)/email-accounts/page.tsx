"use client";

import { useHealth } from "@/components/app-shell";
import { Badge, Button, Card, Empty, Input, Label } from "@/components/ui";
import { CheckCircle2, ExternalLink } from "lucide-react";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/utils";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { toast } from "sonner";

function AccountsInner() {
  const health = useHealth();
  const params = useSearchParams();
  const [accounts, setAccounts] = useState<any[]>([]);
  const [smtp, setSmtp] = useState({
    email_address: "",
    host: "smtp.gmail.com",
    port: 587,
    username: "",
    password: "",
    display_name: "",
  });
  // null = decide from the accounts (form hidden once an SMTP account is connected)
  const [formOpen, setFormOpen] = useState<boolean | null>(null);
  const [saving, setSaving] = useState(false);
  const smtpConnected = accounts.filter((a) => a.provider === "smtp" && a.status === "connected");
  const showForm = formOpen ?? smtpConnected.length === 0;

  function openForm(email = "") {
    setSmtp((cur) => ({ ...cur, email_address: email, username: email, password: "" }));
    setFormOpen(true);
  }

  async function saveSmtp() {
    setSaving(true);
    try {
      await api("/api/email-accounts/smtp", {
        method: "POST",
        body: JSON.stringify({ ...smtp, username: smtp.username.trim() || smtp.email_address.trim() }),
      });
      toast.success("SMTP connected");
      setSmtp((cur) => ({ ...cur, password: "" }));
      setFormOpen(false);
      await load();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function load() {
    setAccounts(await api("/api/email-accounts"));
  }

  useEffect(() => {
    load().catch((e) => toast.error(e.message));
    if (params.get("connected")) toast.success("Gmail connected");
    if (params.get("error")) toast.error(params.get("error") || "OAuth error");
  }, []);

  async function connect() {
    const res = await api<{ authorization_url: string }>("/api/email-accounts/gmail/connect");
    window.location.href = res.authorization_url;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Email setup</h1>
          <p className="text-sm text-muted">Connect Gmail or SMTP, test it, then pick that account on a campaign.</p>
        </div>
        <Button disabled={!health?.google_oauth_configured} onClick={() => connect().catch((e) => toast.error(e.message))}>
          Connect Gmail
        </Button>
      </div>
      <p className="text-sm">
        Gmail/SMTP Setup Documentation →{" "}
        <a className="text-primary underline" href="https://support.google.com/mail/answer/7126229" target="_blank" rel="noreferrer">
          Open documentation
        </a>
        {" · "}
        <a className="text-primary underline" href="https://support.google.com/accounts/answer/185833" target="_blank" rel="noreferrer">
          Gmail app password
        </a>
      </p>
      {!health?.google_oauth_configured ? (
        <Card className="border-amber-300">
          Add your Google OAuth Client ID and Client Secret in AI & settings → Gmail first. Then return here to connect Gmail.
        </Card>
      ) : (
        <Card className="text-sm text-muted">
          If Google shows <span className="font-medium text-foreground">redirect_uri_mismatch</span>, open Settings →
          Gmail and add those exact Authorized redirect URIs to your OAuth client.
        </Card>
      )}
      {smtpConnected.length && !showForm ? (
        <Card className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-start gap-3">
            <CheckCircle2 className="mt-0.5 h-5 w-5 text-emerald-600" />
            <div>
              <div className="flex items-center gap-2">
                <h2 className="font-medium">SMTP connected</h2>
                <Badge tone="ok">Connected</Badge>
              </div>
              <p className="text-sm text-muted">{smtpConnected.map((a) => a.email_address).join(", ")}</p>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={() => openForm(smtpConnected[0].email_address)}>
              Replace password
            </Button>
            <Button variant="secondary" onClick={() => openForm()}>
              Add another account
            </Button>
          </div>
        </Card>
      ) : (
        <Card className="space-y-3">
          <div className="flex items-center justify-between gap-2">
            <h2 className="font-medium">Connect SMTP</h2>
            {smtpConnected.length ? (
              <Button variant="ghost" onClick={() => setFormOpen(false)}>
                Cancel
              </Button>
            ) : null}
          </div>
          <div className="space-y-2 rounded-lg border border-border bg-background p-3 text-sm">
            <p className="font-medium">Using Gmail? Create an App Password (your normal password won&apos;t work)</p>
            <ol className="list-decimal space-y-1 pl-5 text-muted">
              <li>
                Turn on{" "}
                <a className="text-primary underline" href="https://myaccount.google.com/signinoptions/twosv" target="_blank" rel="noreferrer">
                  2-Step Verification
                </a>{" "}
                for the Gmail account (App Passwords only appear once it is on).
              </li>
              <li>
                Open{" "}
                <a
                  className="inline-flex items-center gap-1 font-medium text-primary underline"
                  href="https://myaccount.google.com/apppasswords"
                  target="_blank"
                  rel="noreferrer"
                >
                  myaccount.google.com/apppasswords <ExternalLink className="h-3 w-3" />
                </a>{" "}
                while signed in to that same Gmail account.
              </li>
              <li>Type a name such as &quot;Lead Generator&quot; and click Create.</li>
              <li>Copy the 16-letter password Google shows (spaces don&apos;t matter) and paste it into Password below.</li>
              <li>
                Use host <code>smtp.gmail.com</code>, port <code>587</code>, and your full Gmail address as the username.
              </li>
            </ol>
            <p className="text-xs text-muted">
              Google shows the password only once; create a new one any time on the same page (and remove old ones there).
              Google Workspace accounts need App Passwords allowed by their admin.
            </p>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <div>
              <Label>Email address</Label>
              <Input
                value={smtp.email_address}
                placeholder="you@gmail.com"
                onChange={(e) =>
                  setSmtp({
                    ...smtp,
                    email_address: e.target.value,
                    username: !smtp.username || smtp.username === smtp.email_address ? e.target.value : smtp.username,
                  })
                }
              />
            </div>
            <div>
              <Label>Display name</Label>
              <Input value={smtp.display_name} placeholder="Your name or company" onChange={(e) => setSmtp({ ...smtp, display_name: e.target.value })} />
            </div>
            <div>
              <Label>SMTP host</Label>
              <Input value={smtp.host} onChange={(e) => setSmtp({ ...smtp, host: e.target.value })} />
            </div>
            <div>
              <Label>Port</Label>
              <Input type="number" value={smtp.port} onChange={(e) => setSmtp({ ...smtp, port: Number(e.target.value) })} />
            </div>
            <div>
              <Label>Username</Label>
              <Input value={smtp.username} placeholder="Same as the email address" onChange={(e) => setSmtp({ ...smtp, username: e.target.value })} />
            </div>
            <div>
              <Label>Password (Gmail: the App Password)</Label>
              <Input
                type="password"
                autoComplete="new-password"
                placeholder="xxxx xxxx xxxx xxxx"
                value={smtp.password}
                onChange={(e) => setSmtp({ ...smtp, password: e.target.value })}
              />
            </div>
          </div>
          <Button variant="secondary" disabled={saving || !smtp.email_address.trim() || !smtp.password} onClick={saveSmtp}>
            {saving ? "Checking the login…" : "Save SMTP"}
          </Button>
        </Card>
      )}
      {accounts.some((a) => a.status === "expired" || String(a.last_error || "").toLowerCase().includes("expired")) ? (
        <Card className="border-amber-300">
          Gmail access expired. Click Connect Gmail again, then Test. Campaigns will not send until this succeeds.
        </Card>
      ) : null}
      {accounts.length === 0 ? (
        <Empty title="No Gmail connected" hint="Click Connect Gmail, then you can send a campaign." />
      ) : (
        accounts.map((a) => (
          <Card key={a.id} className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="font-medium">{a.email_address}</div>
              <div className="text-sm text-muted">
                {a.provider === "smtp" ? "SMTP" : "Gmail"} · Sent today {a.sent_today}/{a.daily_limit} · remaining {a.remaining_today}
                {a.is_default ? " · default" : ""}
              </div>
              {a.last_error ? <div className="text-sm text-danger">{a.last_error}</div> : null}
              {a.provider === "smtp" && a.inbox_error ? (
                <div className="text-sm text-danger">Reply/bounce check failed: {a.inbox_error}</div>
              ) : a.provider === "smtp" && a.inbox_checked_at ? (
                <div className="text-sm text-muted">Replies &amp; bounces checked {formatDate(a.inbox_checked_at)}</div>
              ) : null}
            </div>
            <div className="flex items-center gap-2">
              <Badge tone={a.status === "connected" ? "ok" : "bad"}>{a.status === "expired" ? "expired — reconnect" : a.status}</Badge>
              <Button variant="secondary" onClick={() => api(`/api/email-accounts/${a.id}/default`, { method: "POST" }).then(() => { toast.success("Default set"); load(); })}>
                Default
              </Button>
              {a.provider !== "smtp" && a.status === "connected" ? (
                <Button variant="secondary" onClick={() => api(`/api/email-accounts/${a.id}/refresh`, { method: "POST" }).then(() => toast.success("Refreshed")).catch((e) => toast.error(e.message))}>
                  Re-auth
                </Button>
              ) : null}
              {a.status === "expired" || (a.provider !== "smtp" && a.status !== "connected") ? (
                <Button onClick={() => connect().catch((e) => toast.error(e.message))}>Reconnect Gmail</Button>
              ) : null}
              <Button variant="secondary" onClick={() => api(`/api/email-accounts/${a.id}/test`, { method: "POST" }).then(() => { toast.success("Test sent"); load(); }).catch((e) => { toast.error(e.message); load(); })}>
                Test
              </Button>
              <Button variant="danger" onClick={() => api(`/api/email-accounts/${a.id}/disconnect`, { method: "POST" }).then(() => { toast.success("Disconnected"); load(); })}>
                Disconnect
              </Button>
            </div>
          </Card>
        ))
      )}
    </div>
  );
}

export default function EmailAccountsPage() {
  return (
    <Suspense>
      <AccountsInner />
    </Suspense>
  );
}
