"use client";

import { Button, Card, Input, Label } from "@/components/ui";
import { authApi } from "@/lib/api";
import { setTokens } from "@/lib/auth";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    try {
      const tokens = (await authApi.login({ email, password })) as {
        access_token: string;
        refresh_token: string;
      };
      setTokens(tokens.access_token, tokens.refresh_token);
      window.location.assign("/dashboard");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-md">
        <h1 className="mb-1 text-2xl font-semibold">Sign in</h1>
        <p className="mb-6 text-sm text-muted">Access your isolated lead generation workspace.</p>
        <form onSubmit={submit} className="space-y-4">
          <div>
            <Label>Email</Label>
            <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </div>
          <div>
            <Label>Password</Label>
            <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
          </div>
          <Button className="w-full" disabled={loading}>
            {loading ? "Signing in…" : "Sign in"}
          </Button>
        </form>
        <div className="mt-4 flex justify-between text-sm">
          <Link href="/forgot-password" className="text-primary">
            Forgot password
          </Link>
          <Link href="/register" className="text-primary">
            Create account
          </Link>
        </div>
      </Card>
    </div>
  );
}
