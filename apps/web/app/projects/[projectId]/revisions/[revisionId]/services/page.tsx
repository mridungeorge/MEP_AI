import { ServicesScreen } from "@/components/ServicesScreen";

export default function ServicesPage({ params }: { params: { projectId: string; revisionId: string } }) {
  return <ServicesScreen revisionId={params.revisionId} />;
}
