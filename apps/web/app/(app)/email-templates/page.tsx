"use client";

import { Button, Card, Empty } from "@/components/ui";
import { api } from "@/lib/api";
import Link from "next/link";
import { useEffect, useState } from "react";
import { toast } from "sonner";

export default function TemplatesPage() {
  const [items, setItems] = useState<any[]>([]);

  useEffect(() => {
    api<any[]>("/api/templates").then(setItems).catch((e) => toast.error(e.message));
  }, []);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Email templates</h1>
        <Link href="/email-templates/new">
          <Button>New template</Button>
        </Link>
      </div>
      {items.length === 0 ? (
        <Empty title="No templates yet" hint="Open Send emails — a starter template is created for you automatically." />
      ) : (
        items.map((t) => (
          <Card key={t.id} className="flex items-center justify-between">
            <div>
              <Link href={`/email-templates/${t.id}`} className="font-medium text-primary">
                {t.name}
              </Link>
              <p className="text-sm text-muted">{t.subject}</p>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted">{t.is_active ? "Active" : "Inactive"}</span>
              <Link href={`/email-templates/${t.id}`}>
                <Button variant="secondary">Edit</Button>
              </Link>
              <Button
                variant="danger"
                onClick={() =>
                  api(`/api/templates/${t.id}`, { method: "DELETE" })
                    .then(() => {
                      toast.success("Deleted");
                      setItems((rows) => rows.filter((row) => row.id !== t.id));
                    })
                    .catch((e) => toast.error(e.message))
                }
              >
                Delete
              </Button>
            </div>
          </Card>
        ))
      )}
    </div>
  );
}
