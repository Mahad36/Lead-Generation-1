"use client";

import { Button, Card } from "@/components/ui";
import { api } from "@/lib/api";
import { useParams } from "next/navigation";
import { useState } from "react";

export default function UnsubscribePage() {
  const { token } = useParams<{ token: string }>();
  const [done, setDone] = useState("");
  const [error, setError] = useState("");

  async function confirm() {
    try {
      const res = await api<{ message: string }>(`/api/unsubscribe?token=${encodeURIComponent(token)}`, { auth: false });
      setDone(res.message);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unsubscribe failed");
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <Card className="max-w-md space-y-4">
        <h1 className="text-xl font-semibold">Unsubscribe</h1>
        <p className="text-sm text-muted">You will be removed from future outreach from this sender only.</p>
        {done ? <p>{done}</p> : <Button onClick={confirm}>Confirm unsubscribe</Button>}
        {error ? <p className="text-danger">{error}</p> : null}
      </Card>
    </div>
  );
}
