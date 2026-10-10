import { PreviewScreen } from "@/components/PreviewScreen";

export default function PreviewPage({ params }: { params: { projectId: string; revisionId: string } }) {
  return <PreviewScreen revisionId={params.revisionId} />;
}
