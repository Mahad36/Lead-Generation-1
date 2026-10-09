"use client";

import { TrackingBadges, type Tracking } from "@/components/tracking-badges";
import { Badge, Button, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/utils";
import { Loader2, Paperclip, Send } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

export type ThreadSummary = {
  root_id: string;
  lead_id: string | null;
  business_name: string;
  contact_name: string;
  email: string;
  website_url: string;
  subject: string;
  last_at: string;
  last_direction: "in" | "out";
  last_snippet: string;
  unread: number;
  messages: number;
};

type Message = Tracking & {
  id: string;
  direction: "in" | "out";
  source: "outreach" | "inbox" | "auto" | "app" | "gmail";
  from_email: string;
  to_email: string;
  subject: string;
  body_text: string;
  at: string;
  read: boolean;
  attachment: string;
};

type Thread = Omit<ThreadSummary, "last_at" | "last_direction" | "last_snippet" | "unread" | "messages"> & {
  messages: Message[];
};

const SOURCE_LABEL: Record<Message["source"], string> = {
  outreach: "Your outreach email",
  inbox: "Their reply",
  auto: "Automatic reply (not from a person)",
  app: "You replied from the app",
  gmail: "You replied from Gmail",
};

/** One conversation: the outreach email, their replies, your answers, and a reply box. */
export function ConversationView({ rootId, onChange }: { rootId: string; onChange?: () => void }) {
  const [thread, setThread] = useState<Thread | null>(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);

  const load = useCallback(
    () =>
      api<Thread>(`/api/conversations/${rootId}`).then((data) => {
        setThread(data);
        if (data.messages.some((m) => m.direction === "in" && !m.read)) {
          api(`/api/conversations/${rootId}/read`, { method: "POST" })
            .then(() => onChange?.())
            .catch(() => undefined);
        }
      }),
    [rootId, onChange]
  );

  useEffect(() => {
    load().catch((e) => toast.error(e.message));
  }, [load]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "nearest" });
  }, [thread?.messages.length]);

  async function send() {
    if (!draft.trim()) return;
    setSending(true);
    try {
      setThread(
        await api<Thread>(`/api/conversations/${rootId}/reply`, {
          method: "POST",
          body: JSON.stringify({ body: draft }),
          signal: AbortSignal.timeout(120_000),
        })
      );
      setDraft("");
      toast.success("Reply sent from your mailbox, in the same email thread.");
      onChange?.();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSending(false);
    }
  }

  if (!thread) return <p className="p-4 text-sm text-muted">Loading conversation...</p>;
  const name = thread.contact_name || thread.business_name;

  return (
    <div className="flex min-h-0 flex-col">
      <div className="border-b border-border p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="min-w-0">
            <div className="font-semibold">{thread.business_name}</div>
            <div className="truncate text-sm text-muted">
              {thread.email}
              {thread.website_url ? ` · ${thread.website_url.replace(/^https?:\/\/(www\.)?/, "")}` : ""}
            </div>
          </div>
          {thread.lead_id ? (
            <Link href={`/leads/${thread.lead_id}`}>
              <Button variant="secondary">Lead &amp; audit</Button>
            </Link>
          ) : null}
        </div>
        <div className="mt-1 text-sm">{thread.subject}</div>
      </div>

      <div className="space-y-3 overflow-y-auto p-4">
        {thread.messages.map((m) => {
          const mine = m.direction === "out";
          return (
            <div key={m.id} className={`flex ${mine ? "justify-end" : "justify-start"}`}>
              <div
                className={`max-w-[85%] rounded-xl border px-4 py-3 text-sm ${
                  mine ? "border-yellow-300 bg-yellow-50 dark:border-yellow-700 dark:bg-yellow-950/30" : "border-border bg-card"
                }`}
              >
                <div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-muted">
                  <span className="font-medium text-foreground">{mine ? "You" : name}</span>
                  <span>{SOURCE_LABEL[m.source]}</span>
                  <span>· {formatDate(m.at)}</span>
                  {!mine && !m.read ? <Badge tone="warn">New</Badge> : null}
                </div>
                <div className="whitespace-pre-wrap leading-relaxed">{m.body_text || "(no text)"}</div>
                {m.attachment ? (
                  <div className="mt-2 flex items-center gap-1 text-xs text-muted">
                    <Paperclip className="h-3 w-3" /> {m.attachment}
                  </div>
                ) : null}
                {mine && m.source !== "gmail" ? (
                  <div className="mt-2">
                    <TrackingBadges t={m} sent />
                  </div>
                ) : null}
              </div>
            </div>
          );
        })}
        <div ref={bottom} />
      </div>

      <div className="space-y-2 border-t border-border p-4">
        <Textarea
          rows={5}
          value={draft}
          placeholder={`Reply to ${name}... It goes out from your mailbox as a reply in the same thread.`}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
              e.preventDefault();
              send();
            }
          }}
        />
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-xs text-muted">Ctrl+Enter to send. Their last message is quoted underneath, like in Gmail.</span>
          <Button disabled={sending || !draft.trim()} onClick={send}>
            {sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
            Send reply
          </Button>
        </div>
      </div>
    </div>
  );
}

/** All conversations with one lead (shown on the lead page). Nothing renders until the lead has replied. */
export function LeadConversations({ leadId }: { leadId: string }) {
  const [threads, setThreads] = useState<ThreadSummary[]>([]);

  useEffect(() => {
    api<ThreadSummary[]>(`/api/conversations?lead_id=${leadId}`)
      .then(setThreads)
      .catch(() => setThreads([]));
  }, [leadId]);

  if (!threads.length) return null;
  return (
    <div className="space-y-3">
      {threads.map((t) => (
        <div key={t.root_id} className="rounded-xl border border-border bg-card">
          <div className="border-b border-border px-4 py-2 text-sm font-medium">Conversation</div>
          <ConversationView rootId={t.root_id} />
        </div>
      ))}
    </div>
  );
}
