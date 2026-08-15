import Link from "next/link";
import { notFound } from "next/navigation";
import { getReport } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export default async function ReportPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const report = await getReport(id);
  if (!report) notFound();

  return (
    <main>
      <p>
        <Link href={`/runs/${encodeURIComponent(id)}`}>&larr; {id}</Link>
      </p>
      <h1>Report — {id}</h1>
      {report.available ? (
        <pre style={{ whiteSpace: "pre-wrap", border: "1px solid #ddd", padding: "1rem" }}>
          {report.content}
        </pre>
      ) : (
        <p>Not available: {report.reason}</p>
      )}
    </main>
  );
}
