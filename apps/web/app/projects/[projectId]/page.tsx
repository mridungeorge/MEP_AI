import { ProjectHistoryScreen } from "@/components/ProjectHistoryScreen";

export default function ProjectPage({ params }: { params: { projectId: string } }) {
  return <ProjectHistoryScreen projectId={params.projectId} />;
}
