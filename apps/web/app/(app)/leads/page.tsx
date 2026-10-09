"use client";

import { FileOutreachControls, type FileOutreach } from "@/components/file-outreach";
import { OutreachTable, type OutreachLead } from "@/components/outreach";
import { Badge, Button, Card, Empty, Input, Label } from "@/components/ui";
import { api } from "@/lib/api";
import { LEAD_SOURCE, LEAD_STATUS, SAMPLE_LEADS_CSV } from "@/lib/constants";
import { formatDate } from "@/lib/utils";
import { FileSpreadsheet, Map, UserPlus } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

type LeadFile = {
  id: string;
  kind: "scrape" | "import" | "manual";
  name: string;
  status: string;
  lead_count: number;
  ready_leads: number;
  detail: string;
  created_at: string | null;
};

type Lead = OutreachLead & {
  category: string;
  city: string;
  country: string;
  status: string;
  source: string;
};

// Pipeline stages that change on their own; the list refreshes while any lead is in one.
const MOVING = new Set(["website_found", "auditing", "audited", "pdf_generated", "sending"]);

function statusTone(status: string): "ok" | "warn" | "bad" | "default" {
  if (status === "ready" || status === "completed") return "ok";
  if (status === "processing" || status === "running" || status === "queued") return "warn";
  if (status === "failed" || status === "blocked") return "bad";
  return "default";
}

function campaignHref(file: LeadFile) {
  if (file.kind === "import") return `/campaigns/new?importId=${file.id}`;
  if (file.kind === "scrape") return `/campaigns/new?scrapeId=${file.id}`;
  return "/campaigns/new?manual=1";
}

function LeadsInner() {
  const router = useRouter();
  const params = useSearchParams();
  const fileRef = useRef<HTMLInputElement>(null);
  const kind = params.get("kind") || "";
  const fileId = params.get("id") || "";
  const [files, setFiles] = useState<LeadFile[]>([]);
  const [leads, setLeads] = useState<Lead[]>([]);
  const [total, setTotal] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [showManual, setShowManual] = useState(false);
  const [manual, setManual] = useState({
    business_name: "",
    category: "",
    city: "",
    country: "",
    primary_phone: "",
    primary_email: "",
    website_url: "",
  });

  const [fileOutreach, setFileOutreach] = useState<FileOutreach | null>(null);
  const openFile = files.find((item) => item.kind === kind && item.id === fileId) || null;

  async function loadFiles() {
    setFiles(await api<LeadFile[]>("/api/leads/files"));
  }

  async function removeFile(file: LeadFile) {
    if (file.kind === "manual") return;
    if (!window.confirm("Delete this file and its leads? This cannot be undone.")) return;
    const path = file.kind === "scrape" ? `/api/scraping/jobs/${file.id}` : `/api/leads/imports/${file.id}`;
    await api(path, { method: "DELETE" });
    toast.success("File deleted");
    if (kind === file.kind && fileId === file.id) {
      router.push("/leads");
    }
    await loadFiles();
  }

  async function loadFileLeads(file: LeadFile) {
    if (file.kind === "scrape") {
      api<{ outreach?: FileOutreach }>(`/api/scraping/jobs/${file.id}`)
        .then((job) => setFileOutreach(job.outreach || null))
        .catch(() => setFileOutreach(null));
    } else {
      setFileOutreach(null);
    }
    const query = new URLSearchParams({ page: "1", page_size: "100", sort: "created_at", order: "desc" });
    if (file.kind === "import") query.set("import_batch_id", file.id);
    if (file.kind === "scrape") query.set("scraping_job_id", file.id);
    if (file.kind === "manual") query.set("source", "manual");
    const res = await api<{ items: Lead[]; total: number }>(`/api/leads?${query}`);
    setLeads(res.items);
    setTotal(res.total);
  }

  useEffect(() => {
    loadFiles().catch((e) => toast.error(e.message));
  }, []);

  useEffect(() => {
    if (!openFile) {
      setLeads([]);
      setTotal(0);
      return;
    }
    loadFileLeads(openFile).catch((e) => toast.error(e.message));
  }, [kind, fileId, files.length]);

  useEffect(() => {
    if (!openFile || !leads.some((lead) => lead.status === "processing" || MOVING.has(lead.outreach?.stage || ""))) return;
    const timer = setInterval(() => loadFileLeads(openFile).catch(() => undefined), 8000);
    return () => clearInterval(timer);
  }, [openFile, leads]);

  useEffect(() => {
    const busy = files.some((item) => ["processing", "running", "queued"].includes(item.status));
    if (!busy) return;
    const timer = setInterval(() => loadFiles().catch(() => undefined), 4000);
    return () => clearInterval(timer);
  }, [files]);

  function downloadSample() {
    const blob = new Blob([SAMPLE_LEADS_CSV], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "sample-leads.csv";
    a.click();
  }

  async function importCsv(file: File) {
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await api<{ import_id: string; filename: string; new: number; status: string }>(
        "/api/leads/import",
        { method: "POST", body: fd }
      );
      toast.success(`Saved as ${res.filename}. It will not mix into other files.`);
      await loadFiles();
      router.push(`/leads?kind=import&id=${res.import_id}`);
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function addManual() {
    if (!manual.business_name.trim()) return;
    await api("/api/leads", { method: "POST", body: JSON.stringify(manual) });
    toast.success("Saved in Manual leads");
    setManual({
      business_name: "",
      category: "",
      city: "",
      country: "",
      primary_phone: "",
      primary_email: "",
      website_url: "",
    });
    setShowManual(false);
    await loadFiles();
    router.push("/leads?kind=manual&id=manual");
  }

  if (openFile) {
    return (
      <div className="space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <button className="text-sm text-primary" onClick={() => router.push("/leads")}>
              ← All lead files
            </button>
            <h1 className="mt-2 text-2xl font-semibold">{openFile.name}</h1>
            <p className="text-sm text-muted">
              {openFile.detail || (openFile.kind === "scrape" ? "Maps scrape" : openFile.kind === "import" ? "Uploaded CSV" : "Manual")}
              {" · "}
              {openFile.ready_leads} ready to email
            </p>
            {openFile.kind === "scrape" ? (
              <div className="mt-2">
                <FileOutreachControls
                  jobId={openFile.id}
                  outreach={fileOutreach}
                  onChange={() => loadFileLeads(openFile).catch(() => undefined)}
                />
              </div>
            ) : null}
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge tone={statusTone(openFile.status)}>{LEAD_STATUS[openFile.status as keyof typeof LEAD_STATUS] || openFile.status}</Badge>
            {openFile.kind !== "scrape" ? (
              <Button disabled={openFile.ready_leads < 1} onClick={() => router.push(campaignHref(openFile))}>
                Email this file
              </Button>
            ) : (
              <Button variant="secondary" onClick={() => router.push("/scraping")}>
                View scrape
              </Button>
            )}
            {openFile.kind !== "manual" ? (
              <Button variant="danger" onClick={() => removeFile(openFile).catch((e) => toast.error(e.message))}>
                Delete file
              </Button>
            ) : null}
          </div>
        </div>
        {leads.length === 0 ? (
          <Empty title="No emailed-ready leads in this file yet" hint="Wait for analysis, or scrape/upload again." />
        ) : (
          <OutreachTable
            leads={leads}
            actions={(lead) =>
              (lead as Lead).status === "processing" ? (
                <span className="mr-2 align-middle">
                  <Badge tone="warn">{LEAD_STATUS.processing || "Analyzing website"}</Badge>
                </span>
              ) : null
            }
          />
        )}
        <p className="text-sm text-muted">{total} in this file</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">Lead files</h1>
        <p className="text-sm text-muted">Each scrape and each upload stays in its own file. Campaigns send from one file.</p>
      </div>

      <div className="grid gap-3 md:grid-cols-3">
        <Card className="space-y-3">
          <div className="flex items-center gap-2 font-medium">
            <Map className="h-4 w-4" />
            New Maps scrape
          </div>
          <p className="text-sm text-muted">One scrape job becomes one file, even if you add Miami and London in the same run.</p>
          <Button variant="secondary" onClick={() => router.push("/scraping")}>
            Start scrape
          </Button>
        </Card>
        <Card className="space-y-3">
          <div className="flex items-center gap-2 font-medium">
            <FileSpreadsheet className="h-4 w-4" />
            Upload a CSV
          </div>
          <p className="text-sm text-muted">Creates its own file. Those rows will not appear in other files.</p>
          <div className="flex flex-wrap gap-2">
            <label className="inline-flex cursor-pointer items-center rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground">
              {uploading ? "Uploading…" : "Choose CSV"}
              <input
                ref={fileRef}
                type="file"
                accept=".csv,text/csv"
                className="hidden"
                disabled={uploading}
                onChange={(e) => e.target.files?.[0] && importCsv(e.target.files[0]).catch((err) => toast.error(err.message))}
              />
            </label>
            <Button variant="ghost" onClick={downloadSample}>
              Sample
            </Button>
          </div>
        </Card>
        <Card className="space-y-3">
          <div className="flex items-center gap-2 font-medium">
            <UserPlus className="h-4 w-4" />
            Add one lead
          </div>
          <p className="text-sm text-muted">Goes into a Manual leads file.</p>
          <Button variant="secondary" onClick={() => setShowManual((v) => !v)}>
            {showManual ? "Close" : "Add manually"}
          </Button>
        </Card>
      </div>

      {showManual ? (
        <Card className="grid gap-3 md:grid-cols-4">
          <div className="md:col-span-2">
            <Label>Business name</Label>
            <Input value={manual.business_name} onChange={(e) => setManual({ ...manual, business_name: e.target.value })} />
          </div>
          <div>
            <Label>Category</Label>
            <Input value={manual.category} onChange={(e) => setManual({ ...manual, category: e.target.value })} />
          </div>
          <div>
            <Label>City</Label>
            <Input value={manual.city} onChange={(e) => setManual({ ...manual, city: e.target.value })} />
          </div>
          <div>
            <Label>Country</Label>
            <Input value={manual.country} onChange={(e) => setManual({ ...manual, country: e.target.value })} />
          </div>
          <div>
            <Label>Phone</Label>
            <Input value={manual.primary_phone} onChange={(e) => setManual({ ...manual, primary_phone: e.target.value })} />
          </div>
          <div>
            <Label>Email</Label>
            <Input value={manual.primary_email} onChange={(e) => setManual({ ...manual, primary_email: e.target.value })} />
          </div>
          <div>
            <Label>Website</Label>
            <Input value={manual.website_url} onChange={(e) => setManual({ ...manual, website_url: e.target.value })} />
          </div>
          <div className="flex items-end">
            <Button onClick={() => addManual().catch((e) => toast.error(e.message))}>Save to Manual file</Button>
          </div>
        </Card>
      ) : null}

      {files.length === 0 ? (
        <Empty title="No lead files yet" hint="Scrape Maps or upload a CSV. Each run becomes its own file." />
      ) : (
        <div className="space-y-3">
          {files.map((file) => (
            <Card key={`${file.kind}-${file.id}`} className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <button className="text-left font-medium text-primary" onClick={() => router.push(`/leads?kind=${file.kind}&id=${file.id}`)}>
                  {file.name}
                </button>
                <p className="text-sm text-muted">
                  {file.kind === "scrape" ? "Scrape" : file.kind === "import" ? "Upload" : "Manual"}
                  {file.detail ? ` · ${file.detail}` : ""}
                  {` · ${file.ready_leads} ready`}
                  {file.created_at ? ` · ${formatDate(file.created_at)}` : ""}
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={statusTone(file.status)}>{file.status}</Badge>
                <Button variant="secondary" onClick={() => router.push(`/leads?kind=${file.kind}&id=${file.id}`)}>
                  Open
                </Button>
                {file.kind !== "scrape" ? (
                  <Button disabled={file.ready_leads < 1} onClick={() => router.push(campaignHref(file))}>
                    Email this file
                  </Button>
                ) : null}
                {file.kind !== "manual" ? (
                  <Button variant="danger" onClick={() => removeFile(file).catch((e) => toast.error(e.message))}>
                    Delete
                  </Button>
                ) : null}
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

export default function LeadsPage() {
  return (
    <Suspense>
      <LeadsInner />
    </Suspense>
  );
}
