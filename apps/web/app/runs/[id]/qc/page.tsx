import { redirect } from "next/navigation";

/**
 * Kept as a deep link, not as a page.
 *
 * A run is one document now — see `app/runs/[id]/page.tsx` for why. This
 * route still exists because links to it exist: in saved bookmarks, in the
 * browser tests, and in `docs/copilotkit_product_architecture.md`. Redirecting
 * to the anchor lands the reader in the same content, in its place in the
 * argument, rather than at a 404.
 */
export default async function QualityRedirect({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  redirect(`/runs/${encodeURIComponent(id)}#quality`);
}
