import { RevisionScreen } from "@/components/RevisionScreen";

export default function RevisionPage({ params }: { params: { projectId: string; revisionId: string } }) {
  return <RevisionScreen projectId={params.projectId} revisionId={params.revisionId} />;
}
