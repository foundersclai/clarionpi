"use client";

/**
 * TanStack Query provider. `retry: false` for 4xx — a typed refusal (matter_not_found,
 * jurisdiction_unsupported, ...) is a deterministic answer, not a transient failure, so
 * retrying it just delays rendering the error inline. (We retry nothing here for
 * simplicity; the M3-D wave can widen this to retry 5xx/network only if needed.)
 */

import {
  QueryClient,
  QueryClientProvider,
  isServer,
} from "@tanstack/react-query";
import { type ReactNode, useState } from "react";

function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        refetchOnWindowFocus: false,
        staleTime: 5_000,
      },
      mutations: {
        retry: false,
      },
    },
  });
}

let browserQueryClient: QueryClient | undefined;

/** One client on the server per request; a singleton in the browser (survives re-renders). */
function getQueryClient(): QueryClient {
  if (isServer) {
    return makeQueryClient();
  }
  if (browserQueryClient === undefined) {
    browserQueryClient = makeQueryClient();
  }
  return browserQueryClient;
}

/** Wrap the app so components can use `useQuery` / `useMutation`. */
export function QueryProvider({ children }: { children: ReactNode }): ReactNode {
  // useState so the client is created once per component instance and not re-made on render.
  const [queryClient] = useState(getQueryClient);
  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}
