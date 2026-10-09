import { Gate1Screen } from "@/components/Gate1Screen";

export default function Gate1Page({ params }: { params: { projectId: string; revisionId: string } }) {
  return <Gate1Screen revisionId={params.revisionId} />;
}
