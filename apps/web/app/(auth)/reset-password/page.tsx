"use client";

import { Button, Card, Input, Label } from "@/components/ui";
import { authApi } from "@/lib/api";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { toast } from "sonner";

function ResetForm() {
  const params = useSearchParams();
  const router = useRouter();
  const [password, setPassword] = useState("");
  const token = params.get("token") || "";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    try {
      await authApi.reset(token, password);
      toast.success("Password updated");
      router.replace("/login");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Reset failed");
    }
  }

  return (
    <Card className="w-full max-w-md">
      <h1 className="mb-4 text-2xl font-semibold">Reset password</h1>
      <form onSubmit={submit} className="space-y-4">
        <div>
          <Label>New password</Label>
          <Input
            type="password"
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </div>
        <Button className="w-full" disabled={!token}>
          Update password
        </Button>
      </form>
    </Card>
  );
}

export default function ResetPasswordPage() {
  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <Suspense>
        <ResetForm />
      </Suspense>
    </div>
  );
}
