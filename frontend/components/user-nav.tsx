"use client";

/**
 * UserNav — the top-nav right-side auth chip. Driven by auth.me(): a signed-in user shows
 * their display name + role badge and a Logout button; logged-out (or auth-not-wired) shows
 * a "Sign in" link. Because me() degrades a 401/404/network to null, this renders "Sign in"
 * cleanly on the current dev stub without erroring.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { logout, me } from "@/lib/auth";
import type { UserRole } from "@/lib/types";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

const ROLE_VARIANT: Record<UserRole, BadgeProps["variant"]> = {
  attorney: "default",
  paralegal: "info",
  admin: "warning",
};

export function UserNav() {
  const router = useRouter();
  const queryClient = useQueryClient();

  const meQuery = useQuery({
    queryKey: ["me"],
    queryFn: me,
    staleTime: 30_000,
  });

  const logoutMutation = useMutation({
    mutationFn: logout,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["me"] });
      router.push("/login");
    },
  });

  if (meQuery.isLoading) {
    return <span className="text-sm text-ink-muted">…</span>;
  }

  const user = meQuery.data ?? null;

  if (!user) {
    return (
      <Link
        href="/login"
        className="text-sm font-medium text-accent hover:underline"
      >
        Sign in
      </Link>
    );
  }

  return (
    <div className="flex items-center gap-3">
      <span className="flex items-center gap-2 text-sm text-ink">
        {user.display_name}
        <Badge variant={ROLE_VARIANT[user.role] ?? "secondary"}>{user.role}</Badge>
      </span>
      <Button
        variant="ghost"
        size="sm"
        disabled={logoutMutation.isPending}
        onClick={() => logoutMutation.mutate()}
      >
        Logout
      </Button>
    </div>
  );
}
