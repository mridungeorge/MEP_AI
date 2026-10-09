"use client";
import { useEffect, useState } from "react";
import { PackageView } from "@/components/PackageView";
import { api } from "@/lib/api";
import type { Package } from "@/lib/types";

/** The certifier's view: no sign-in, read-only, opened by the long random token in the address. Every opening is logged. */
export default function SharedPackagePage({ params }: { params: { token: string } }) {
  const [pkg, setPkg] = useState<Package | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.shared(params.token).then(setPkg).catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, [params.token]);
  if (error) return <main><h1>Compliance package</h1><p role="alert">{error}</p></main>;
  if (!pkg) return <main><p>Loading the package…</p></main>;
  return (
    <main>
      <h1>Compliance package (read-only)</h1>
      <PackageView pkg={pkg} />
      <p><a href={api.sharedPdfUrl(params.token)} data-testid="shared-pdf">Download the PDF</a></p>
    </main>
  );
}
