"use client";

import { FileOutreachControls, OutreachModePicker, type FileOutreach, type OutreachMode } from "@/components/file-outreach";
import { BusinessTypeSelect } from "@/components/business-types";
import { LocationPicker, searchArea, type Location } from "@/components/location-picker";
import { DEFAULT_RECIPIENTS, RecipientOptionsBox, recipientsForApi, type RecipientOptions } from "@/components/email-rotation";
import { CitySuggestions, DEFAULT_SCHEDULE, SchedulePicker, scheduleForApi, slotsProblem, type Schedule } from "@/components/schedule-picker";
import { SendFrom } from "@/components/send-from";
import { OutreachTable, type OutreachLead } from "@/components/outreach";
import { Badge, Button, Card, Input, Label } from "@/components/ui";
import { api } from "@/lib/api";
import Link from "next/link";
import { useEffect, useState } from "react";
import { toast } from "sonner";

type LeadRow = OutreachLead & {
  status: string;
};

// Pipeline stages that change on their own; the lead list refreshes while any lead is in one.
const MOVING = new Set(["website_found", "auditing", "audited", "pdf_generated", "sending"]);

function statusTone(status: string): "ok" | "warn" | "bad" | "default" {
  if (status === "completed" || status === "ready") return "ok";
  if (status === "failed" || status === "blocked" || status === "cancelled") return "bad";
  if (status === "running" || status === "queued" || status === "paused") return "warn";
  return "default";
}

type Search = { country: string; city: string; category: string };
type Job = {
  id: string;
  name: string;
  status: string;
  businesses_found: number;
  new_leads: number;
  duplicate_leads: number;
  skipped_no_email?: number;
  discarded?: number;
  analyzed_count?: number;
  finding_emails?: boolean;
  outreach?: FileOutreach | null;
  errors_count: number;
  current_query: string;
  worker_status: string;
  error_message: string;
  campaign_id?: string | null;
  campaign_status?: string;
  campaign_sent_today?: number;
  campaign_daily_limit?: number;
  searches: { country: string; city: string; category: string; status: string; businesses_found: number; new_leads: number; error_message: string }[];
};

export default function ScrapingPage() {
  const [jobName, setJobName] = useState("");
  const [location, setLocationState] = useState<Location>({ country: "United States", area: "" });
  // Several states / cities per file: each one is searched for every business type
  const [areas, setAreas] = useState<string[]>(["Miami, Florida"]);
  const [schedule, setSchedule] = useState<Schedule>(DEFAULT_SCHEDULE);
  const [recipients, setRecipients] = useState<RecipientOptions>(DEFAULT_RECIPIENTS);
  const [windowLabel, setWindowLabel] = useState("");
  const setLocation = (next: Location) => {
    if (next.country !== location.country) setAreas([]); // places belong to one country
    setLocationState(next);
  };
  const addArea = (area: string) => {
    const value = area.trim();
    if (value) setAreas((rows) => (rows.includes(value) ? rows : [...rows, value]));
  };
  const [typeDraft, setTypeDraft] = useState("");
  const [types, setTypes] = useState<string[]>(["Plumbers"]);
  const [job, setJob] = useState<Job | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [leads, setLeads] = useState<LeadRow[]>([]);
  const [dailyLimit, setDailyLimit] = useState(50);
  const [outreachMode, setOutreachMode] = useState<OutreachMode>("auto");
  const [outreachLimit, setOutreachLimit] = useState(0); // audits per day; 0 = no limit
  const [outreachAccount, setOutreachAccount] = useState(""); // "" = automatic (rotation)
  const [followups, setFollowups] = useState(true);
  const [source, setSource] = useState<{ provider: string; apifyReady: boolean } | null>(null);

  async function loadLeads(jobId: string) {
    const res = await api<{ items: LeadRow[] }>(
      `/api/leads?scraping_job_id=${jobId}&page=1&page_size=100&sort=created_at&order=desc`
    );
    setLeads(res.items || []);
  }

  async function refresh(id?: string) {
    const list = await api<Job[]>("/api/scraping/jobs");
    setJobs(list);
    const current = id ? list.find((j) => j.id === id) : list[0];
    if (current) {
      setJob(current);
      await loadLeads(current.id).catch(() => setLeads([]));
    }
  }

  useEffect(() => {
    refresh().catch((e) => toast.error(e.message));
    api<any>("/api/settings")
      .then((s) => {
        setDailyLimit(s.default_daily_send_limit || 50);
        setSource({ provider: s.scraper_provider, apifyReady: Boolean(s.apify_configured) });
        if (s.send_window_enabled) {
          const h = (n: number) => `${String(n % 24).padStart(2, "0")}:00`;
          setWindowLabel(`${h(s.send_window_start)}-${h(s.send_window_end)}${s.send_weekdays_only ? ", Mon-Fri" : ""}`);
        } else {
          setWindowLabel("any time");
        }
      })
      .catch(() => undefined);
  }, []);

  // Keep the lead list moving while leads go through audit -> PDF -> email -> send.
  useEffect(() => {
    if (!job || !leads.some((lead) => lead.status === "processing" || MOVING.has(lead.outreach?.stage || ""))) return;
    const t = setInterval(() => loadLeads(job.id).catch(() => undefined), 8000);
    return () => clearInterval(t);
  }, [job?.id, leads]);

  useEffect(() => {
    const busy =
      job &&
      (["queued", "running", "paused"].includes(job.status) ||
        ["running", "paused"].includes(job.campaign_status || "") ||
        job.finding_emails);
    if (!busy) return;
    const t = setInterval(() => refresh(job.id), 2000);
    return () => clearInterval(t);
  }, [job?.id, job?.status, job?.campaign_status]);

  function addType() {
    const value = typeDraft.trim();
    if (!value || types.includes(value)) return;
    setTypes((rows) => [...rows, value]);
    setTypeDraft("");
  }

  async function start() {
    const name = jobName.trim();
    if (!name) {
      toast.error("Enter a lead/job name");
      return;
    }
    const typed = searchArea(location);
    const places = typed && !areas.includes(typed) ? [...areas, typed] : areas;
    if (!places.length || !location.country || !types.length) {
      toast.error("Choose a country, at least one state or city, and at least one business type");
      return;
    }
    if (outreachMode !== "off" && schedule.mode === "local" && !schedule.days.length) {
      toast.error("Pick at least one day in the sending schedule");
      return;
    }
    if (outreachMode !== "off" && slotsProblem(schedule)) {
      toast.error(slotsProblem(schedule));
      return;
    }
    // Every place x every business type. "Miami, Florida" (or the whole state) keeps the Maps search unambiguous.
    const searches = places.flatMap((city) => types.map((category) => ({ country: location.country, city, category })));
    const created = await api<Job>("/api/scraping/jobs", {
      method: "POST",
      body: JSON.stringify({
        name,
        searches,
        // Outreach for scraped leads is the personalised audit pipeline (Automatic outreach card), not a template.
        auto_send: false,
        // This file's own automatic outreach (audit -> PDF -> personalised email), independent of other files.
        outreach_mode: outreachMode,
        outreach_daily_limit: outreachLimit,
        outreach_email_account_id: outreachAccount || undefined,
        followups,
        schedule: scheduleForApi(schedule),
        ...recipientsForApi(recipients),
      }),
    });
    const started = await api<Job>(`/api/scraping/jobs/${created.id}/start`, { method: "POST" });
    setJob(started);
    toast.success("Scraping started");
  }

  useEffect(() => {
    if (!jobs.some((j) => j.outreach?.status === "running")) return;
    const t = setInterval(() => refresh(job?.id).catch(() => undefined), 15000);
    return () => clearInterval(t);
  }, [jobs, job?.id]);

  async function findMissingEmails(id: string) {
    const res = await api<{ checking: number; started: boolean }>(`/api/scraping/jobs/${id}/find-emails`, { method: "POST" });
    toast.success(
      res.checking
        ? `Searching ${res.checking} websites for emails. Leads with an email are added; the rest are removed.`
        : "Every lead with a website already has an email."
    );
    await refresh(id);
  }

  async function removeFile(id: string) {
    if (!window.confirm("Delete this file and its leads? This cannot be undone.")) return;
    await api(`/api/scraping/jobs/${id}`, { method: "DELETE" });
    toast.success("File deleted");
    const list = await api<Job[]>("/api/scraping/jobs");
    setJobs(list);
    if (job?.id === id) {
      const next = list[0] || null;
      setJob(next);
      setLeads([]);
      if (next) await loadLeads(next.id).catch(() => setLeads([]));
    }
  }

  async function stopSending() {
    if (!job?.campaign_id) return;
    await api(`/api/campaigns/${job.campaign_id}/stop`, { method: "POST" });
    toast.success("Stopped sending");
    await refresh(job.id);
  }

  /** Pause / resume / cancel one scrape that is already running (from its own card, never from the form). */
  async function scrapeAction(target: Job, kind: "pause" | "resume" | "cancel") {
    if (kind === "cancel" && !window.confirm(`Cancel scraping "${target.name}"? Leads found so far are kept.`)) return;
    try {
      const updated = await api<Job>(`/api/scraping/jobs/${target.id}/${kind}`, { method: "POST" });
      if (job?.id === updated.id) setJob(updated);
      setJobs((rows) => rows.map((row) => (row.id === updated.id ? { ...row, ...updated } : row)));
      toast.success(kind === "cancel" ? "Scraping cancelled" : kind === "pause" ? "Scraping paused" : "Scraping resumed");
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  /** Start over with an empty form; nothing that is already running is touched. */
  function clearForm() {
    setJobName("");
    setLocationState({ country: "United States", area: "" });
    setAreas([]);
    setTypes([]);
    setTypeDraft("");
    setOutreachMode("auto");
    setOutreachLimit(0);
    setOutreachAccount("");
    setFollowups(true);
    setSchedule(DEFAULT_SCHEDULE);
    setRecipients(DEFAULT_RECIPIENTS);
  }

  function scrapeControls(target: Job) {
    const active = ["queued", "running", "paused"].includes(target.status);
    if (!active) return null;
    return (
      <span className="flex flex-wrap gap-2">
        {target.status === "paused" ? (
          <Button variant="secondary" onClick={() => scrapeAction(target, "resume")}>
            Resume scraping
          </Button>
        ) : (
          <Button variant="secondary" onClick={() => scrapeAction(target, "pause")}>
            Pause scraping
          </Button>
        )}
        <Button variant="danger" onClick={() => scrapeAction(target, "cancel")}>
          Cancel scraping
        </Button>
      </span>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Scraping / Leads</h1>
        <p className="text-sm text-muted">
          Each job stays in its own file, with its own automatic outreach: every lead with a website and an email is
          audited and gets its own personalised email with the PDF attached, within that file&apos;s daily limit. Start,
          pause or stop each file separately.
        </p>
      </div>
      <Card className="space-y-4">
        <div>
          <Label>Lead/Job name</Label>
          <Input value={jobName} onChange={(e) => setJobName(e.target.value)} placeholder="New York HVAC Leads" />
        </div>
        <LocationPicker value={location} onChange={setLocation} onPick={addArea} />
        <div className="-mt-2 flex flex-wrap items-center gap-2">
          {areas.map((area) => (
            <button
              key={area}
              type="button"
              className="rounded-full border border-primary bg-primary/10 px-3 py-1 text-sm"
              onClick={() => setAreas((rows) => rows.filter((row) => row !== area))}
            >
              {area} ×
            </button>
          ))}
          {location.area.trim() ? (
            <Button type="button" variant="secondary" onClick={() => { addArea(location.area); setLocationState({ ...location, area: "" }); }}>
              Add &quot;{location.area.trim()}&quot;
            </Button>
          ) : null}
          {!areas.length ? <span className="text-xs text-muted">No places yet: pick one above or from the suggestions.</span> : null}
        </div>
        <CitySuggestions country={location.country} selected={areas} onAdd={addArea} />
        <div>
          <Label>Business types</Label>
          <div className="mt-1 flex flex-wrap gap-2">
            {types.map((item) => (
              <button
                key={item}
                type="button"
                className="rounded-full border border-border px-3 py-1 text-sm"
                onClick={() => setTypes((rows) => rows.filter((row) => row !== item))}
              >
                {item} ×
              </button>
            ))}
          </div>
          <div className="mt-2 flex flex-wrap gap-2">
            <BusinessTypeSelect selected={types} onAdd={(t) => setTypes((rows) => (rows.includes(t) ? rows : [...rows, t]))} />
            <Input
              className="min-w-[14rem] flex-1"
              value={typeDraft}
              onChange={(e) => setTypeDraft(e.target.value)}
              placeholder="...or type your own (e.g. Boat dealers)"
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  addType();
                }
              }}
            />
            <Button variant="secondary" type="button" onClick={addType}>
              Add
            </Button>
          </div>
        </div>

        {source ? (
          <p className="text-sm text-muted">
            Lead source:{" "}
            <span className="font-medium text-foreground">
              {source.provider === "apify" ? "Apify" : "Built-in scraper (free)"}
            </span>
            {source.provider === "apify" && !source.apifyReady ? (
              <span className="text-red-600"> · Apify token missing</span>
            ) : null}{" "}
            ·{" "}
            <Link href="/settings?tab=apify" className="text-primary underline">
              change
            </Link>
          </p>
        ) : null}
        <OutreachModePicker mode={outreachMode} limit={outreachLimit} onMode={setOutreachMode} onLimit={setOutreachLimit} />
        {outreachMode !== "off" ? (
          <div className="space-y-2">
            <SendFrom className="max-w-md" value={outreachAccount} onChange={setOutreachAccount} />
            <SchedulePicker country={location.country} areas={areas} value={schedule} onChange={setSchedule} windowLabel={windowLabel} />
            <RecipientOptionsBox value={recipients} onChange={setRecipients} />
            <label className="flex items-start gap-2 text-sm">
              <input type="checkbox" className="mt-1" checked={followups} onChange={(e) => setFollowups(e.target.checked)} />
              <span>
                Send follow-ups
                <span className="block text-xs text-muted">
                  Short follow-ups in the same thread if a lead doesn&apos;t reply (timing in AI &amp; settings → Sending).
                </span>
              </span>
            </label>
          </div>
        ) : null}
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => start().catch((e) => toast.error(e.message))}>Start scraping</Button>
          <Button variant="secondary" onClick={clearForm}>
            Clear form
          </Button>
          <span className="self-center text-xs text-muted">
            This form only sets up a new scrape. Pause or cancel a running one on its own card below.
          </span>
        </div>
      </Card>
      {job ? (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-card px-4 py-3">
            <div>
              <h2 className="font-medium">{job.name || "Current scrape"}</h2>
              <p className="text-sm text-muted">
                Scraped {job.businesses_found || 0} · Leads with email {job.new_leads || 0}
                {job.skipped_no_email ? ` · ${job.skipped_no_email} websites being searched for an email` : ""}
                {job.discarded ? ` · ${job.discarded} skipped (no email found)` : ""} · Analyzed {job.analyzed_count || 0}
              </p>
              {(job.status === "failed" || job.status === "blocked") && job.error_message ? (
                <p className="mt-1 text-sm text-danger">Reason: {job.error_message}</p>
              ) : null}
              <div className="mt-2">
                <FileOutreachControls jobId={job.id} outreach={job.outreach} onChange={() => refresh(job.id).catch(() => undefined)} />
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {scrapeControls(job)}
              <Badge tone={statusTone(job.status)}>{job.status}</Badge>
              {job.finding_emails ? (
                <Badge tone="warn">Finding emails…</Badge>
              ) : (job.skipped_no_email || 0) > 0 && !["queued", "running"].includes(job.status) ? (
                <Button variant="secondary" onClick={() => findMissingEmails(job.id).catch((e) => toast.error(e.message))}>
                  Search websites for emails
                </Button>
              ) : null}
              <Link href={`/leads?kind=scrape&id=${job.id}`}>
                <Button variant="secondary">Open file</Button>
              </Link>
              <Button variant="danger" onClick={() => removeFile(job.id).catch((e) => toast.error(e.message))}>
                Delete
              </Button>
              {job.campaign_id ? (
                <Link href={`/campaigns/${job.campaign_id}`}>
                  <Button variant="secondary">View campaign</Button>
                </Link>
              ) : job.status === "completed" && (job.new_leads || 0) > 0 ? (
                <Link href={`/campaigns/new?scrapeId=${job.id}`}>
                  <Button variant="secondary">Create campaign</Button>
                </Link>
              ) : null}
            </div>
          </div>
          {job.campaign_id ? (
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-card px-4 py-3">
              <div>
                <div className="font-medium">Campaign for this scrape</div>
                <p className="text-sm text-muted">
                  {job.campaign_status === "running"
                    ? `Sending automatically · ${job.campaign_sent_today || 0}/${job.campaign_daily_limit || dailyLimit} today`
                    : job.campaign_status === "paused"
                      ? `Paused at the daily limit · ${job.campaign_sent_today || 0}/${job.campaign_daily_limit || dailyLimit} today. Continues tomorrow.`
                      : job.campaign_status === "failed"
                        ? "Sending stopped"
                        : job.campaign_status || "Waiting for ready leads"}
                </p>
              </div>
              {["running", "paused", "draft"].includes(job.campaign_status || "") ? (
                <Button variant="danger" onClick={() => stopSending().catch((e) => toast.error(e.message))}>
                  Stop sending
                </Button>
              ) : null}
            </div>
          ) : null}
          {leads.length === 0 ? (
            <p className="text-sm text-muted">
              {["queued", "running"].includes(job.status)
                ? "Each lead will appear as its own bar below."
                : "No leads were saved in this file."}
            </p>
          ) : (
            <OutreachTable
              leads={leads}
              actions={(lead) =>
                (lead as LeadRow).status === "processing" ? (
                  <span className="mr-2 align-middle">
                    <Badge tone="warn">Checking website</Badge>
                  </span>
                ) : null
              }
            />
          )}
        </div>
      ) : null}
      {jobs.filter((j) => j.id !== job?.id).length ? (
        <div className="space-y-2">
          <h2 className="font-medium">Recent files</h2>
          {jobs.filter((j) => j.id !== job?.id).map((j) => (
            <div key={j.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-card px-4 py-3">
              <div className="space-y-1">
                <div className="font-medium">{j.name}</div>
                <div className="text-sm text-muted">{j.new_leads} leads in this file</div>
                <FileOutreachControls jobId={j.id} outreach={j.outreach} onChange={() => refresh(job?.id).catch(() => undefined)} />
              </div>
              <div className="flex flex-wrap items-center gap-2">
                {scrapeControls(j)}
                <Badge tone={statusTone(j.status)}>{j.status}</Badge>
                <Button variant="secondary" onClick={() => { setJob(j); loadLeads(j.id).catch(() => setLeads([])); }}>
                  Show leads
                </Button>
                <Link href={`/leads?kind=scrape&id=${j.id}`}>
                  <Button variant="secondary">Open file</Button>
                </Link>
                <Button variant="danger" onClick={() => removeFile(j.id).catch((e) => toast.error(e.message))}>
                  Delete
                </Button>
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
