import type { ProjectRow } from "@/lib/types";

export type StepStatus = "done" | "current" | "todo";
export interface OnboardingStep { id: "create" | "upload" | "gate1" | "run" | "review"; title: string; detail: string; status: StepStatus; href: string | null }

/** Short help text per screen. Each states what the product actually does; none says anything is compliant. */
export const HELP = {
  gate1: "Values read from the architect model or a drawing are shown as 'extracted': they are only what the file says and the rules will not use them. "
    + "A designer checks each row and confirms it; only 'confirmed' values (and values you typed yourself) are ever used by the rules.",
  run: "Every rule in the library is a DRAFT until a human engineer approves it. A run shows each result with its clause reference and the DRAFT banner; "
    + "the software applies the encoded rules but never decides compliance.",
  review: "Approve: you have checked the line and agree with it. Reject: the line is wrong and must be fixed. Request changes: send it back to the designer with your reason. "
    + "A reason is required for every decision. An accepted FAIL needs a category and is acknowledged again by the approver.",
  signoff: "The approver types the registration number again at Gate 3 and it must match the number already on file for them, which a platform administrator "
    + "verified against the public register. It is recorded in the signed package and the audit ledger to show who took responsibility.",
  drafting: "A card must be confirmed (every required field filled by a person) before it can be built. Files are released to you only if every validator passes; "
    + "otherwise the run is listed as not released and no file is returned.",
  services: "Warnings only. Sizing, ceiling-void margins and clash-lite results are design aids for you to check; none of them is a compliance result.",
  commissioning: "The sheet lists each terminal with its design airflow from the signed revision. Measured values you import are records of what was measured; you set the tolerance and nothing is judged against an assumed one.",
} as const;
export type HelpKey = keyof typeof HELP;

const LATEST = (rows: ProjectRow[]) => [...rows].sort((a, b) => b.created_at.localeCompare(a.created_at))[0] ?? null;

/** Derive the first-project steps from real project data. Upload, confirm and run are not separately visible on a project row,
 *  so they read as done once Gate 1 is signed (the revision is frozen); until then the first of them is the current step. */
export function deriveSteps(projects: ProjectRow[]): OnboardingStep[] {
  const p = LATEST(projects);
  const rev = p?.latest_revision ?? null;
  const g1 = rev?.gates_signed.includes("gate1") ?? false;
  const g3 = rev?.gates_signed.includes("gate3") ?? false;
  const gate1 = p && rev ? `/projects/${p.id}/revisions/${rev.id}/gate1` : null;
  const review = p && rev ? `/projects/${p.id}/revisions/${rev.id}/review` : null;
  const done = [p !== null, g1, g1, g1, g3];
  const steps: Omit<OnboardingStep, "status">[] = [
    { id: "create", title: "Create a project", detail: "Address, state, NCC edition and climate zone. One NCC edition per project.", href: null },
    { id: "upload", title: "Upload the architect model", detail: "An IFC (preferred), DXF or PDF drawing. What is read is shown as extracted, not confirmed.", href: gate1 },
    { id: "gate1", title: "Confirm inputs at Gate 1", detail: "A designer checks each extracted value and confirms it.", href: gate1 },
    { id: "run", title: "Run the rules", detail: "Results carry clause references and a DRAFT banner until engineers approve the rules.", href: gate1 },
    { id: "review", title: "Review and sign", detail: "A checker reviews at Gate 2, then the approver signs Gate 3 with their registration number.", href: review },
  ];
  const cur = done.indexOf(false);
  return steps.map((s, i) => ({ ...s, status: done[i] ? "done" : i === cur ? "current" : "todo" }));
}
