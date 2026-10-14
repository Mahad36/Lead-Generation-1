"use client";

import { HEALTH_TONE, type HealthSummary } from "@/components/inbox-health";
import { useEmailAccounts, type EmailAccount } from "@/components/send-from";
import { Badge, Label } from "@/components/ui";
import Link from "next/link";

export type Rotation = {
  enabled: boolean;
  mode: "auto" | "selected" | "health"; // when enabled
  accountIds: string[]; // for "selected"
  accountId: string; // when not enabled ("" = default account)
};

export const DEFAULT_ROTATION: Rotation = { enabled: true, mode: "auto", accountIds: [], accountId: "" };

/** The campaign API fields for this choice. */
export function rotationForApi(r: Rotation) {
  return r.enabled
    ? { rotation_mode: r.mode, account_ids: r.mode === "selected" ? r.accountIds : [], email_account_id: undefined }
    : { rotation_mode: "single", account_ids: [], email_account_id: r.accountId || undefined };
}

type Account = EmailAccount & { health_status?: string; health_summary?: HealthSummary };

const MODES = [
  ["auto", "Automatic", "Every eligible account, each getting emails in proportion to the room it has left today."],
  ["selected", "Selected accounts", "Only the accounts you tick below, by remaining room."],
  ["health", "Health-based", "Every eligible account, healthier ones first (Healthy, then Monitoring, then Reduced)."],
] as const;

/** Single account, or rotation across connected Gmails by their own safe capacity (never a blind round-robin). */
export function EmailRotation({ value, onChange }: { value: Rotation; onChange: (next: Rotation) => void }) {
  const accounts = useEmailAccounts() as Account[] | null;
  const connected = (accounts || []).filter((a) => a.status === "connected" || a.status === "expired");
  const set = (patch: Partial<Rotation>) => onChange({ ...value, ...patch });

  if (accounts !== null && !connected.length) {
    return (
      <div className="rounded-xl border border-border p-3 text-sm text-muted">
        No Gmail connected yet.{" "}
        <Link href="/email-accounts" className="text-primary underline">
          Connect one in Email setup
        </Link>
        .
      </div>
    );
  }

  return (
    <div className="space-y-3 rounded-xl border border-border p-3 sm:col-span-2">
      <label className="flex items-start gap-2 text-sm">
        <input type="checkbox" className="mt-1" checked={value.enabled} onChange={(e) => set({ enabled: e.target.checked })} />
        <span>
          <span className="font-medium">Email rotation</span>
          <span className="block text-xs text-muted">
            Spread this campaign across your connected Gmails according to each one&apos;s own safe capacity. It shares
            the load; it never lets an account send more than its own limit.
          </span>
        </span>
      </label>

      {value.enabled ? (
        <div className="grid gap-2 sm:grid-cols-3">
          {MODES.map(([mode, title, hint]) => (
            <label
              key={mode}
              className={`cursor-pointer rounded-lg border px-3 py-2 text-sm ${value.mode === mode ? "border-primary ring-2 ring-primary/30" : "border-border"}`}
            >
              <input type="radio" className="mr-2" checked={value.mode === mode} onChange={() => set({ mode })} />
              <span className="font-medium">{title}</span>
              <span className="block text-xs text-muted">{hint}</span>
            </label>
          ))}
        </div>
      ) : (
        <div className="max-w-md">
          <Label>Send everything from</Label>
          <select
            className="w-full rounded-md border border-border bg-card px-3 py-2 text-sm"
            value={value.accountId}
            onChange={(e) => set({ accountId: e.target.value })}
          >
            <option value="">Default account</option>
            {connected.map((a) => (
              <option key={a.id} value={a.id}>
                {a.email_address}
              </option>
            ))}
          </select>
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead className="text-muted">
            <tr>
              {value.enabled && value.mode === "selected" ? <th className="py-1 pr-2">Use</th> : null}
              <th className="py-1 pr-2">Account</th>
              <th className="py-1 pr-2">Status</th>
              <th className="py-1 pr-2">Safe today</th>
              <th className="py-1 pr-2">Left today</th>
              <th className="py-1">Why</th>
            </tr>
          </thead>
          <tbody>
            {accounts === null ? (
              <tr>
                <td className="py-1 text-muted" colSpan={6}>
                  Loading accounts...
                </td>
              </tr>
            ) : (
              connected.map((a) => {
                const h = a.health_summary;
                const ticked = value.accountIds.includes(a.id);
                return (
                  <tr key={a.id} className="border-t border-border">
                    {value.enabled && value.mode === "selected" ? (
                      <td className="py-1 pr-2">
                        <input
                          type="checkbox"
                          checked={ticked}
                          onChange={() =>
                            set({ accountIds: ticked ? value.accountIds.filter((x) => x !== a.id) : [...value.accountIds, a.id] })
                          }
                        />
                      </td>
                    ) : null}
                    <td className="py-1 pr-2">{a.email_address}</td>
                    <td className="py-1 pr-2">
                      {h ? <Badge tone={HEALTH_TONE[h.status] || "default"}>{h.label}</Badge> : a.status}
                    </td>
                    <td className="py-1 pr-2">{h ? h.recommended_today ?? h.limit_today : a.limit_today ?? a.daily_limit}</td>
                    <td className="py-1 pr-2">{a.remaining_today}</td>
                    <td className="max-w-xs py-1 text-muted">{h?.reason || ""}</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
      {value.enabled && value.mode === "selected" && !value.accountIds.length ? (
        <p className="text-xs text-amber-700 dark:text-amber-300">Tick at least one account.</p>
      ) : null}
    </div>
  );
}

type Summary = {
  leads: number;
  with_website: number;
  websites_audited: number;
  contacts_with_email: number;
  valid_recipients: number;
  rotation_mode: string;
  accounts: { account_id: string; email_address: string; status: string; status_key: string; capacity_today: number; remaining_today: number }[];
  capacity_per_day: number;
  estimated_days: number | null;
  estimate_note: string;
  warnings: string[];
  compliance: { country: string; count: number; note: string }[];
  main_country: string;
  best_time: { label: string; note: string } | null;
};

const MODE_LABEL: Record<string, string> = {
  single: "Single account",
  auto: "Automatic rotation",
  selected: "Selected accounts",
  health: "Health-based rotation",
};

/** Everything to check before launching (from POST /api/campaigns/preview). */
export function LaunchSummary({ s }: { s?: Summary | null }) {
  if (!s || !s.leads) return null;
  return (
    <div className="space-y-3 rounded-xl border border-border p-3 text-sm">
      <div className="font-medium">Before you launch</div>
      <div className="grid gap-2 sm:grid-cols-3">
        <Stat label="Leads in the file" value={s.leads} />
        <Stat label="Websites audited" value={`${s.websites_audited} of ${s.with_website}`} />
        <Stat label="Contacts with an email" value={s.contacts_with_email} />
        <Stat label="Valid recipients" value={s.valid_recipients} />
        <Stat label="Sending capacity / day" value={s.capacity_per_day} />
        <Stat label="Estimated time" value={s.estimated_days ? `about ${s.estimated_days} day${s.estimated_days === 1 ? "" : "s"}` : "waits for capacity"} />
      </div>
      <div className="text-xs text-muted">
        {MODE_LABEL[s.rotation_mode] || s.rotation_mode}:{" "}
        {s.accounts.map((a) => `${a.email_address} (${a.status}, ${a.capacity_today}/day)`).join(" · ") || "no accounts"}
        <span className="block">{s.estimate_note}</span>
      </div>
      {s.best_time ? (
        <div className="text-xs">
          <span className="font-medium">Best time for {s.main_country}:</span> {s.best_time.label}. {s.best_time.note}
        </div>
      ) : null}
      {s.warnings.length ? (
        <ul className="list-disc space-y-0.5 pl-5 text-xs text-amber-700 dark:text-amber-300">
          {s.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      ) : null}
      {s.compliance.length ? (
        <details className="text-xs">
          <summary className="cursor-pointer font-medium">Email rules for these countries (general guidance, not legal advice)</summary>
          <ul className="mt-1 list-disc space-y-1 pl-5 text-muted">
            {s.compliance.map((c) => (
              <li key={c.country}>
                <span className="text-foreground">{c.country}</span> ({c.count}): {c.note}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg bg-background px-3 py-2">
      <div className="text-xs text-muted">{label}</div>
      <div className="font-semibold">{value}</div>
    </div>
  );
}
