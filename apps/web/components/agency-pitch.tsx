"use client";

import { Badge, Button, Card, Label, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

type Pitch = { notes: string; filename: string; size: number };

const NOTES_HINT = `For example:
- What we do for agencies, white-label: Shopify store builds and migrations, theme customisation, CRO, landing pages
- How it works: delivered under your brand, we never contact your clients, NDA on request
- Turnaround and availability: e.g. 48-hour quotes, overlap with US hours (EST) every weekday
- Pricing model: e.g. fixed price per project or a monthly retainer of dev hours
- Proof: years in business, team size, store types we've built`;

function formatSize(bytes: number) {
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/** What every agency pitch is written from: your offer notes and your company profile PDF (attached). */
export function AgencyPitchCard() {
  const [pitch, setPitch] = useState<Pitch | null>(null);
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  async function load() {
    const s = await api<{ agency_pitch: string; agency_profile_filename: string; agency_profile_size: number }>("/api/settings");
    setPitch({ notes: s.agency_pitch || "", filename: s.agency_profile_filename || "", size: s.agency_profile_size || 0 });
    setNotes(s.agency_pitch || "");
  }

  useEffect(() => {
    load().catch((e) => toast.error(e.message));
  }, []);

  async function saveNotes() {
    setSaving(true);
    try {
      await api("/api/settings", { method: "PUT", body: JSON.stringify({ agency_pitch: notes.trim() }) });
      toast.success("Saved. New agency emails are written from these notes.");
      await load();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function upload(file: File) {
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      await api("/api/settings/agency-profile", { method: "POST", body: fd });
      toast.success("Company profile uploaded. Every agency email sent from now on attaches it.");
      await load();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  async function openProfile() {
    const popup = window.open("", "_blank");
    try {
      const res = await api<Response>("/api/settings/agency-profile", { raw: true });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "Could not open the PDF");
      const url = URL.createObjectURL(await res.blob());
      if (popup) popup.location.href = url;
    } catch (e) {
      popup?.close();
      toast.error((e as Error).message);
    }
  }

  async function removeProfile() {
    if (!window.confirm("Remove your company profile? Agency emails stop until you upload one again.")) return;
    await api("/api/settings/agency-profile", { method: "DELETE" });
    toast.success("Company profile removed");
    await load();
  }

  if (!pitch) return null;
  const missing = [!pitch.size && "your company profile PDF", !pitch.notes.trim() && "what you offer agencies"].filter(Boolean);
  const unsaved = notes.trim() !== pitch.notes.trim();
  return (
    <Card className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-medium">Your pitch</h2>
        {missing.length ? <Badge tone="bad">Agency emails are paused</Badge> : <Badge tone="ok">Ready</Badge>}
      </div>
      {missing.length ? (
        <p className="rounded-md bg-amber-50 p-2 text-sm text-amber-800 dark:bg-amber-900/30 dark:text-amber-200">
          Add {missing.join(" and ")} below. Agency files still find agencies and check their addresses, but no email
          is written or sent until both are in place.
        </p>
      ) : null}

      <div className="space-y-2">
        <Label>Company profile (PDF, attached to every agency email)</Label>
        <div className="flex flex-wrap items-center gap-2">
          {pitch.size ? (
            <>
              <span className="text-sm">
                {pitch.filename} <span className="text-muted">· {formatSize(pitch.size)}</span>
              </span>
              <Button variant="secondary" onClick={() => openProfile()}>
                Open
              </Button>
              <Button variant="secondary" onClick={() => fileInput.current?.click()} disabled={uploading}>
                {uploading ? "Uploading…" : "Replace"}
              </Button>
              <Button variant="danger" onClick={() => removeProfile().catch((e) => toast.error(e.message))}>
                Remove
              </Button>
            </>
          ) : (
            <Button onClick={() => fileInput.current?.click()} disabled={uploading}>
              {uploading ? "Uploading…" : "Upload company profile"}
            </Button>
          )}
          <input
            ref={fileInput}
            type="file"
            accept="application/pdf,.pdf"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) upload(file);
            }}
          />
        </div>
        <p className="text-xs text-muted">
          Up to 10 MB. A smaller file (2-3 MB) is better for delivery. Emails sent after you replace it use the new one.
        </p>
      </div>

      <div className="space-y-2">
        <Label>What you offer agencies</Label>
        <Textarea rows={9} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder={NOTES_HINT} />
        <p className="text-xs text-muted">
          Claude writes each email from these notes and the agency&apos;s own website. It only mentions services, prices,
          turnaround times and numbers written here. Your work links come from the portfolio sheet in{" "}
          <Link href="/settings" className="text-primary underline">
            AI &amp; settings
          </Link>
          .
        </p>
        <Button onClick={() => saveNotes()} disabled={saving || !unsaved}>
          {saving ? "Saving…" : "Save"}
        </Button>
      </div>
    </Card>
  );
}

/** On the Agency outreach page: is the pitch ready, with a link to edit it in Settings. */
export function AgencyPitchStatus() {
  const [missing, setMissing] = useState<string[] | null>(null);
  useEffect(() => {
    api<{ agency_pitch: string; agency_profile_size: number }>("/api/settings")
      .then((s) =>
        setMissing(
          [!s.agency_profile_size && "your company profile PDF", !(s.agency_pitch || "").trim() && "what you offer agencies"].filter(
            Boolean
          ) as string[]
        )
      )
      .catch(() => setMissing(null));
  }, []);
  if (missing === null) return null;
  return (
    <div
      className={`flex flex-wrap items-center justify-between gap-2 rounded-xl border px-4 py-3 text-sm ${
        missing.length ? "border-amber-300 bg-amber-50 text-amber-800 dark:bg-amber-900/30 dark:text-amber-200" : "border-border bg-card"
      }`}
    >
      <span>
        {missing.length ? (
          <>Agency emails are paused: add {missing.join(" and ")}.</>
        ) : (
          <>
            <Badge tone="ok">Pitch ready</Badge>{" "}
            <span className="text-muted">Your company profile and offer notes are set.</span>
          </>
        )}
      </span>
      <Link href="/settings?tab=agency">
        <Button variant="secondary">{missing.length ? "Set up in Settings" : "Edit in Settings"}</Button>
      </Link>
    </div>
  );
}
