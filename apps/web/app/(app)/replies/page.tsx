"use client";

import { ConversationView, type ThreadSummary } from "@/components/conversation";
import { Badge, Card, Empty } from "@/components/ui";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/utils";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

function RepliesInner() {
  const params = useSearchParams();
  const router = useRouter();
  const selected = params.get("id") || "";
  const [threads, setThreads] = useState<ThreadSummary[] | null>(null);
  const [unreadOnly, setUnreadOnly] = useState(false);

  const load = useCallback(
    () => api<ThreadSummary[]>(`/api/conversations${unreadOnly ? "?unread=true" : ""}`).then(setThreads),
    [unreadOnly]
  );

  useEffect(() => {
    load().catch((e) => toast.error(e.message));
    const timer = setInterval(() => load().catch(() => undefined), 60_000);
    return () => clearInterval(timer);
  }, [load]);

  const open = (id: string) => router.push(`/replies?id=${id}`);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Replies</h1>
          <p className="text-sm text-muted">
            Leads who answered your emails. Reply here and it goes out from your mailbox in the same thread, with open
            tracking. Answers you send from Gmail show up here too (checked every few minutes).
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={unreadOnly} onChange={(e) => setUnreadOnly(e.target.checked)} />
          Unread only
        </label>
      </div>

      {threads === null ? (
        <p className="text-sm text-muted">Loading...</p>
      ) : threads.length === 0 ? (
        <Empty
          title={unreadOnly ? "No unread replies" : "No replies yet"}
          hint="When a lead answers one of your emails it appears here within a few minutes."
        />
      ) : (
        <div className="grid gap-4 lg:grid-cols-[minmax(260px,360px)_1fr]">
          <Card className="divide-y divide-border p-0">
            {threads.map((t) => (
              <button
                key={t.root_id}
                type="button"
                onClick={() => open(t.root_id)}
                className={`block w-full px-4 py-3 text-left hover:bg-background ${t.root_id === selected ? "bg-background" : ""}`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className={`truncate ${t.unread ? "font-semibold" : ""}`}>{t.business_name}</span>
                  {t.unread ? <Badge tone="warn">{t.unread} new</Badge> : null}
                </div>
                <div className="truncate text-xs text-muted">
                  {t.last_direction === "out" ? "You: " : ""}
                  {t.last_snippet}
                </div>
                <div className="mt-0.5 text-xs text-muted">{formatDate(t.last_at)}</div>
              </button>
            ))}
          </Card>
          <Card className="p-0">
            {selected ? (
              <ConversationView key={selected} rootId={selected} onChange={() => load().catch(() => undefined)} />
            ) : (
              <p className="p-6 text-sm text-muted">Choose a conversation on the left.</p>
            )}
          </Card>
        </div>
      )}
    </div>
  );
}

export default function RepliesPage() {
  return (
    <Suspense>
      <RepliesInner />
    </Suspense>
  );
}
