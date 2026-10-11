"use client";

import { NewProjectForm } from "@/components/NewProjectForm";
import { deriveSteps } from "@/lib/onboarding";
import type { ProjectRow } from "@/lib/types";

/** First-project guide for a firm with no projects. Progress comes from the project rows; every step after the first is done on the pages it links to.
 *  The demo button stays disabled until the lead adds a browser-safe endpoint and passes demoEnabled (today the demo is loaded by an administrator with scripts/seed_demo.py). */
export function OnboardingWizard({ projects, onCreated, demoEnabled = false, onDemo }: {
  projects: ProjectRow[]; onCreated: (r: { project_id: string; revision_id: string }) => void; demoEnabled?: boolean; onDemo?: () => void;
}) {
  const steps = deriveSteps(projects);
  return (
    <section aria-label="Get started" data-testid="onboarding">
      <h2>Your first project</h2>
      <ol>
        {steps.map((s) => (
          <li key={s.id} data-status={s.status} aria-current={s.status === "current" ? "step" : undefined}>
            <strong>{s.title}</strong> ({s.status === "done" ? "done" : s.status === "current" ? "next" : "later"}). {s.detail}{" "}
            {s.href && s.status !== "done" && <a href={s.href}>Open</a>}
            {s.id === "create" && s.status === "current" && <NewProjectForm defaultOpen onCreated={onCreated} />}
          </li>
        ))}
      </ol>
      <p>
        <button type="button" disabled={!demoEnabled || !onDemo} onClick={onDemo} aria-describedby="demo-note" data-testid="demo-button">Try the sample demo project</button>{" "}
        <span id="demo-note">{demoEnabled && onDemo ? "Loads a synthetic office project." : "Ask your administrator to load the demo."}</span>
      </p>
    </section>
  );
}
