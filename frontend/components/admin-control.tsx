"use client";

import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { api, ApiError } from "@/lib/api";
import { clearCredentials, getCredentials, onAuthChange, setCredentials } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { Badge } from "@/components/ui/badge";

/** Subscribes to the in-memory/sessionStorage admin credentials. */
export function useAdmin() {
  const [username, setUsername] = useState<string | null>(null);
  useEffect(() => {
    const sync = () => setUsername(getCredentials()?.username ?? null);
    sync();
    return onAuthChange(sync);
  }, []);
  return { username, signedIn: username !== null };
}

export function AdminControl() {
  const { username, signedIn } = useAdmin();
  const [open, setOpen] = useState(false);
  const [user, setUser] = useState("");
  const [pass, setPass] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const userInput = useRef<HTMLInputElement>(null);
  const id = useId();

  useEffect(() => {
    if (!open) return;
    userInput.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    const onClick = (e: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, [open]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!user || !pass) {
      setError("Enter a username and password.");
      return;
    }
    setBusy(true);
    setError(null);
    // Store first so the verification request carries the header; roll back on failure.
    setCredentials({ username: user, password: pass });
    try {
      await api.metrics();
      setOpen(false);
      setPass("");
    } catch (err) {
      clearCredentials();
      if (err instanceof ApiError && err.unauthorized) setError("invalid credentials");
      else if (err instanceof ApiError && err.unreachable) setError("API unreachable; could not verify credentials.");
      else setError(err instanceof Error ? err.message : "sign-in failed");
    } finally {
      setBusy(false);
    }
  };

  if (signedIn) {
    return (
      <div className="inline-flex items-center gap-1.5" data-testid="admin-control">
        <Badge tone="info" title="Administrator credentials held for this browser tab">
          admin: <span className="font-mono">{username}</span>
        </Badge>
        <Button size="sm" variant="ghost" onClick={() => clearCredentials()}>
          Sign out
        </Button>
      </div>
    );
  }

  return (
    <div ref={wrap} className="relative" data-testid="admin-control">
      <Button size="sm" aria-expanded={open} aria-controls={`${id}-popover`} onClick={() => setOpen((o) => !o)}>
        Admin sign-in
      </Button>
      {open ? (
        <form
          id={`${id}-popover`}
          onSubmit={submit}
          aria-label="Administrator sign-in"
          className="absolute right-0 top-full z-40 mt-1 flex w-64 max-w-[calc(100vw-2rem)] flex-col gap-2 rounded-md border border-border bg-surface p-3 shadow-lg"
        >
          <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wide text-muted">
            Username
            <Input ref={userInput} name="username" autoComplete="username" value={user} onChange={(e) => setUser(e.target.value)} />
          </label>
          <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wide text-muted">
            Password
            <Input name="password" type="password" autoComplete="current-password" value={pass} onChange={(e) => setPass(e.target.value)} />
          </label>
          {error ? (
            <p role="alert" className="text-xs text-bad">
              {error}
            </p>
          ) : null}
          <div className="flex items-center justify-between gap-2">
            <Button type="button" size="sm" variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" size="sm" variant="primary" disabled={busy}>
              {busy ? "Verifying…" : "Sign in"}
            </Button>
          </div>
          <p className="text-[11px] text-faint">Credentials are held only for this browser tab and sent over the same origin.</p>
        </form>
      ) : null}
    </div>
  );
}

/** Inline notice for pages whose submit was rejected with 401. */
export function AdminRequiredNotice() {
  return (
    <p role="alert" className="rounded-md border border-warn/30 bg-warn-bg px-3 py-2 text-xs text-warn">
      <span className="font-semibold">Administrator sign-in required</span> (use the Admin button in the header).
    </p>
  );
}
