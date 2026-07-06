"use client";

/**
 * Login page. Email/password → auth.login(); on success route home; on ApiError render the
 * typed code inline (invalid_credentials → "Email or password is incorrect."). No signup —
 * this is a captive firm with seeded users. If the auth endpoint isn't wired yet (404/501),
 * we say so rather than pretending it worked.
 */

import { type FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "@/lib/api";
import { login } from "@/lib/auth";
import type { UserView } from "@/lib/types";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

function loginErrorMessage(error: ApiError): string {
  switch (error.body.error) {
    case "invalid_credentials":
      return "Email or password is incorrect.";
    case "unauthenticated":
      return "Email or password is incorrect.";
    default:
      if (error.status === 404 || error.status === 501) {
        return "Sign-in isn't available yet in this environment.";
      }
      return error.body.detail ?? "Could not sign in. Please try again.";
  }
}

export default function LoginPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const mutation = useMutation<UserView, ApiError, { email: string; password: string }>({
    mutationFn: ({ email, password }) => login(email, password),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["me"] });
      router.push("/");
    },
  });

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    mutation.mutate({ email: email.trim(), password });
  }

  return (
    <div className="mx-auto max-w-sm">
      <Card>
        <CardHeader>
          <CardTitle>Sign in</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="flex flex-col gap-4" aria-label="Sign in">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                name="email"
                type="email"
                autoComplete="username"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                name="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>
            {mutation.error && (
              <p role="alert" data-testid="login-error" className="text-sm text-danger">
                {loginErrorMessage(mutation.error)}
              </p>
            )}
            <Button type="submit" disabled={mutation.isPending}>
              {mutation.isPending ? "Signing in…" : "Sign in"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
