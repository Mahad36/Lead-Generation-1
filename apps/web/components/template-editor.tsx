"use client";

import { Button, Card, Input, Label, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";

const VARS = [
  "business_name", "city", "country", "category", "website", "platform", "website_score",
  "lead_score", "website_analysis", "website_issue", "recommended_service", "personalized_message",
  "sender_name", "sender_company", "sender_website", "signature", "unsubscribe_url",
];

export function TemplateEditor({ id }: { id?: string }) {
  const router = useRouter();
  const [name, setName] = useState("Website opportunity");
  const [subject, setSubject] = useState("Quick idea for {{business_name}}");
  const [html, setHtml] = useState("<p>Hi {{business_name}},</p><p>{{personalized_message}}</p><p>{{sender_name}}<br/>{{sender_company}}</p><p><a href=\"{{unsubscribe_url}}\">Unsubscribe</a></p>");
  const [text, setText] = useState("");
  const [preview, setPreview] = useState<any>(null);
  const [mobile, setMobile] = useState(false);
  const [leads, setLeads] = useState<any[]>([]);
  const [leadId, setLeadId] = useState("");

  useEffect(() => {
    api<{ items: any[] }>("/api/leads?page_size=20").then((r) => setLeads(r.items || []));
    if (!id) return;
    api<any>(`/api/templates/${id}`).then((t) => {
      setName(t.name);
      setSubject(t.subject);
      setHtml(t.html_body);
      setText(t.text_body);
    });
  }, [id]);

  async function save() {
    const payload = { name, subject, html_body: html, text_body: text, is_active: true };
    if (id) await api(`/api/templates/${id}`, { method: "PUT", body: JSON.stringify(payload) });
    else {
      const created = await api<{ id: string }>("/api/templates", { method: "POST", body: JSON.stringify(payload) });
      router.replace(`/email-templates/${created.id}`);
    }
    toast.success("Saved");
  }

  async function doPreview() {
    if (!id) {
      toast.error("Save the template first to preview against a lead.");
      return;
    }
    setPreview(await api(`/api/templates/${id}/preview`, { method: "POST", body: JSON.stringify({ lead_id: leadId || null, subject, html_body: html, text_body: text }) }));
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_280px]">
      <div className="space-y-4">
        <h1 className="text-2xl font-semibold">{id ? "Edit template" : "New template"}</h1>
        <Card className="space-y-3">
          <div>
            <Label>Name</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div>
            <Label>Subject</Label>
            <Input value={subject} onChange={(e) => setSubject(e.target.value)} />
          </div>
          <div>
            <Label>HTML body</Label>
            <Textarea rows={14} value={html} onChange={(e) => setHtml(e.target.value)} />
          </div>
          <div>
            <Label>Plain text fallback</Label>
            <Textarea rows={4} value={text} onChange={(e) => setText(e.target.value)} />
          </div>
          <div className="flex gap-2">
            <Button onClick={() => save().catch((e) => toast.error(e.message))}>Save</Button>
            <Button variant="secondary" onClick={() => doPreview().catch((e) => toast.error(e.message))}>Preview</Button>
          </div>
        </Card>
        {preview ? (
          <Card>
            <div className="mb-3 flex gap-2">
              <Button variant={mobile ? "secondary" : "primary"} onClick={() => setMobile(false)}>Desktop</Button>
              <Button variant={mobile ? "primary" : "secondary"} onClick={() => setMobile(true)}>Mobile</Button>
            </div>
            <p className="mb-2 text-sm font-medium">{preview.subject}</p>
            <div className={`mx-auto border border-border bg-white p-4 text-black ${mobile ? "max-w-sm" : "max-w-2xl"}`} dangerouslySetInnerHTML={{ __html: preview.html_body }} />
          </Card>
        ) : null}
      </div>
      <Card>
        <h2 className="mb-2 font-medium">Variables</h2>
        <div className="flex flex-col gap-1">
          {VARS.map((v) => (
            <button key={v} className="rounded bg-background px-2 py-1 text-left text-xs" onClick={() => { navigator.clipboard.writeText(`{{${v}}}`); toast.success(`Copied {{${v}}}`); }}>
              {`{{${v}}}`}
            </button>
          ))}
        </div>
        <div className="mt-4">
          <Label>Sample lead</Label>
          <select className="w-full rounded-md border border-border bg-card px-2 py-2 text-sm" value={leadId} onChange={(e) => setLeadId(e.target.value)}>
            <option value="">Example data</option>
            {leads.map((l) => (
              <option key={l.id} value={l.id}>{l.business_name}</option>
            ))}
          </select>
        </div>
      </Card>
    </div>
  );
}
