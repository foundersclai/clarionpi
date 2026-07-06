import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";
import { QueryProvider } from "@/lib/query";
import { UserNav } from "@/components/user-nav";
import "./globals.css";

export const metadata: Metadata = {
  title: "ClarionPI",
  description: "Personal-injury demand workbench.",
};

/**
 * Root layout. Wraps the app in the Query provider and renders the minimal top nav. The nav
 * is a server-rendered shell; the auth chip (UserNav) is a client island so it can call
 * me() and drive login/logout without turning the whole shell into a client component.
 */
export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <QueryProvider>
          <div className="flex min-h-screen flex-col">
            <header className="border-b border-border bg-surface">
              <div className="mx-auto flex h-14 w-full max-w-5xl items-center justify-between px-4">
                <Link href="/" className="text-base font-semibold text-ink">
                  ClarionPI
                </Link>
                <UserNav />
              </div>
            </header>
            <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-6">
              {children}
            </main>
          </div>
        </QueryProvider>
      </body>
    </html>
  );
}
