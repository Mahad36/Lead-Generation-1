"use client";

import { isAuthenticated } from "@/lib/auth";
import { useEffect } from "react";

export default function Home() {
  useEffect(() => {
    if (isAuthenticated()) {
      window.location.replace("/dashboard");
    }
  }, []);

  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-6 p-8">
      <div className="text-center">
        <h1 className="text-2xl font-semibold">Lead Generator</h1>
        <p className="mt-2 text-sm text-muted">Find leads and send outreach from your own Gmail.</p>
      </div>
      <div className="flex gap-3">
        <a
          href="/login"
          className="inline-flex items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
        >
          Sign in
        </a>
        <a
          href="/register"
          className="inline-flex items-center justify-center rounded-md border border-border bg-card px-4 py-2 text-sm font-medium"
        >
          Create account
        </a>
      </div>
    </div>
  );
}
