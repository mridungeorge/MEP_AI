import { DraftingScreen } from "@/components/DraftingScreen";

export default function DraftingPage({ params }: { params: { projectId: string; revisionId: string } }) {
  return <DraftingScreen projectId={params.projectId} revisionId={params.revisionId} />;
}
