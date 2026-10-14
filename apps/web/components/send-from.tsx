"use client";

import { Label } from "@/components/ui";
import { api } from "@/lib/api";
import Link from "next/link";
import { useEffect, useState } from "react";

export type EmailAccount = {
  id: string;
  email_address: string;
  provider: string;
  status: string;
  is_default: boolean;
  daily_limit: number;
  limit_today?: number;
  ramping_up?: boolean;
  sent_today: number;
  remaining_today: number;
  paused_reason?: string;
  gmail_signature?: { found: boolean; text: string; error: string };
};

/** What "no inbox chosen" means: rotation across every connected inbox, or the only one there is. */
export function automaticLabel(accounts: EmailAccount[] | null): string {
  const ready = (accounts || []).filter((a) => a.status === "connected" && !a.paused_reason);
  if (ready.length > 1) return `Automatic: rotate across all ${ready.length} inboxes`;
  const only = ready[0] || (accounts || []).find((a) => a.is_default);
  return only ? `Default account (${only.email_address})` : "Default account";
}

let cache: Promise<EmailAccount[]> | null = null;

/** Your email accounts (loaded once per page view and shared by every "Send from" picker on it). */
export function useEmailAccounts(): EmailAccount[] | null {
  const [accounts, setAccounts] = useState<EmailAccount[] | null>(null);
  useEffect(() => {
    cache = cache || api<EmailAccount[]>("/api/email-accounts").catch(() => []);
    let alive = true;
    cache.then((list) => alive && setAccounts(list));
    const timer = setTimeout(() => (cache = null), 30_000); // pick up accounts added in another tab
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, []);
  return accounts;
}

/**
 * Which connected account sends these emails. "" means automatic: each email goes from the connected inbox with the
 * most room left today (with one inbox, that's your default account). Each inbox has its own daily limit.
 */
export function SendFrom({
  value,
  onChange,
  label = "Send from",
  hint = true,
  className = "",
}: {
  value: string;
  onChange: (id: string) => void;
  label?: string;
  hint?: boolean;
  className?: string;
}) {
  const accounts = useEmailAccounts();
  const connected = (accounts || []).filter((a) => a.status === "connected");
  const chosen = (accounts || []).find((a) => a.id === value);

  return (
    <div className={className}>
      <Label>{label}</Label>
      {accounts === null ? (
        <div className="rounded-md border border-border px-3 py-2 text-sm text-muted">Loading accounts...</div>
      ) : !connected.length ? (
        <div className="rounded-md border border-border px-3 py-2 text-sm text-muted">
          No email connected yet.{" "}
          <Link href="/email-accounts" className="text-primary underline">
            Connect Gmail
          </Link>
        </div>
      ) : (
        <select
          className="w-full rounded-md border border-border bg-card px-3 py-2 text-sm"
          value={value}
          onChange={(e) => onChange(e.target.value)}
        >
          <option value="">{automaticLabel(accounts)}</option>
          {connected.map((a) => (
            <option key={a.id} value={a.id}>
              {a.email_address} ·{" "}
              {a.paused_reason ? "paused (bounces)" : `${a.remaining_today} of ${a.limit_today ?? a.daily_limit} left today`}
            </option>
          ))}
          {chosen && chosen.status !== "connected" ? (
            <option value={chosen.id}>{chosen.email_address} (not connected)</option>
          ) : null}
        </select>
      )}
      {hint && connected.length ? (
        <p className="mt-1 text-xs text-muted">
          Each Gmail has its own daily limit; Automatic spreads emails across them.{" "}
          <Link href="/email-accounts" className="underline">
            Add another Gmail
          </Link>
        </p>
      ) : null}
    </div>
  );
}

/** Which of the sending Gmails have a signature (read from Gmail). Missing ones send without, so say so. */
export function GmailSignatureStatus({ accountId }: { accountId: string }) {
  const accounts = useEmailAccounts();
  const sending = (accounts || []).filter((a) => a.status === "connected" && (!accountId || a.id === accountId));
  if (!sending.length) return null;
  const missing = sending.filter((a) => !a.gmail_signature?.found);
  return (
    <span className="mt-1 block space-y-0.5 text-xs">
      {sending.map((a) => (
        <span key={a.id} className={`block ${a.gmail_signature?.found ? "text-muted" : "text-amber-700 dark:text-amber-300"}`}>
          {a.email_address}:{" "}
          {a.gmail_signature?.found
            ? (a.gmail_signature.text || "").split("\n").filter(Boolean).slice(0, 3).join(" · ") || "signature found"
            : "no Gmail signature found, so its emails go out without one"}
        </span>
      ))}
      {missing.length ? (
        <Link href="/email-accounts" className="block text-primary underline">
          Fix in Email setup
        </Link>
      ) : null}
    </span>
  );
}
