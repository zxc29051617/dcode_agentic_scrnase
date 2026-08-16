import { redirect } from "next/navigation";

/**
 * The assistant used to be its own page. It is now a panel in the shell,
 * openable from any run page, so this route only exists to keep an old link
 * or bookmark working.
 */
export default async function AssistantPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  redirect(`/runs/${encodeURIComponent(id)}`);
}
