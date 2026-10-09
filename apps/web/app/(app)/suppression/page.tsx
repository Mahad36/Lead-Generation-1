"use client";

import { Button, Card, Input, Label } from "@/components/ui";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/utils";
import { useEffect, useState } from "react";
import { toast } from "sonner";

export default function SuppressionPage() {
  const [items, setItems] = useState<any[]>([]);
  const [email, setEmail] = useState("");
  const [reason, setReason] = useState("manual");

  async function load() {
    setItems(await api("/api/suppression"));
  }

  useEffect(() => {
    load().catch((e) => toast.error(e.message));
  }, []);

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Suppression list</h1>
      <Card className="flex flex-wrap items-end gap-3">
        <div>
          <Label>Email</Label>
          <Input value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div>
          <Label>Reason</Label>
          <select className="rounded-md border border-border bg-card px-3 py-2 text-sm" value={reason} onChange={(e) => setReason(e.target.value)}>
            <option value="manual">manual</option>
            <option value="user_unsubscribed">unsubscribe</option>
            <option value="bounce">bounce</option>
            <option value="invalid">invalid</option>
          </select>
        </div>
        <Button
          onClick={async () => {
            await api("/api/suppression", { method: "POST", body: JSON.stringify({ email, reason }) });
            setEmail("");
            load();
          }}
        >
          Add
        </Button>
      </Card>
      <Card className="space-y-2 text-sm">
        {items.map((i) => (
          <div key={i.id} className="flex items-center justify-between">
            <span>{i.email} · {i.reason} · {formatDate(i.created_at)}</span>
            <Button variant="ghost" onClick={() => api(`/api/suppression/${i.id}`, { method: "DELETE" }).then(load)}>
              Remove
            </Button>
          </div>
        ))}
      </Card>
    </div>
  );
}
