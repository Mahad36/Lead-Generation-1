"use client";

import { Button, Card, Input, Label } from "@/components/ui";
import { authApi } from "@/lib/api";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    try {
      const res = (await authApi.forgot(email)) as { message: string };
      setMessage(res.message);
      toast.success("If the account exists, a reset link was created.");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Request failed");
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-md">
        <h1 className="mb-4 text-2xl font-semibold">Forgot password</h1>
        <form onSubmit={submit} className="space-y-4">
          <div>
            <Label>Email</Label>
            <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </div>
          <Button className="w-full">Send reset link</Button>
        </form>
        {message ? <p className="mt-4 break-all text-sm text-muted">{message}</p> : null}
        <Link href="/login" className="mt-4 inline-block text-sm text-primary">
          Back to login
        </Link>
      </Card>
    </div>
  );
}
