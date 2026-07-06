/**
 * Home — the workbench entry. The matters router has no LIST endpoint at M2, so the home
 * page is (a) a matter-create form (the real POST /api/matters) and (b) a client-side
 * "recent matters" convenience list. Both children are client components (they fetch /
 * mutate / read localStorage); this page stays a thin server shell that composes them.
 */

import { MatterCreateForm } from "@/components/matter-create-form";
import { RecentMattersList } from "@/components/recent-matters-list";

export default function HomePage() {
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-ink">Workbench</h1>
        <p className="text-sm text-ink-muted">
          Create a matter to begin, or reopen a recent one.
        </p>
      </div>
      <div className="grid gap-6 md:grid-cols-2">
        <MatterCreateForm />
        <RecentMattersList />
      </div>
    </div>
  );
}
