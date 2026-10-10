import { PerformanceScreen } from "@/components/PerformanceScreen";

export default function PerformancePage({ params }: { params: { projectId: string; revisionId: string } }) {
  return <PerformanceScreen revisionId={params.revisionId} />;
}
