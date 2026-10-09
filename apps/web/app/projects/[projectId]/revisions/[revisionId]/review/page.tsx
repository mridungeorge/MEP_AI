import { ReviewScreen } from "@/components/ReviewScreen";

export default function ReviewPage({ params }: { params: { projectId: string; revisionId: string } }) {
  return <ReviewScreen revisionId={params.revisionId} />;
}
