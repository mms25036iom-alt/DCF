import { notFound } from "next/navigation";
import LiveModelViewer from "@/components/live-model-viewer";

export default async function ModelPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (id.toLowerCase() === "demo") notFound();
  return <LiveModelViewer key={id} initialTicker={id} />;
}
