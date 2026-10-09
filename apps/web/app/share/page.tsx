"use client";
import { useEffect, useState } from "react";
import { PackageView } from "@/components/PackageView";
import { api } from "@/lib/api";
import type { Package } from "@/lib/types";

/** The certifier view: no sign-in, read-only. The link is `/share#<token>`. A URL fragment is never sent to any server, so it cannot
 *  appear in a request URL, an access log or a Referer. The page removes it from the address bar and exchanges it, once, for a short-lived
 *  read-only session cookie (HttpOnly: this script cannot read it either). Every opening and read is logged by the server. */
export default function SharedPackagePage() {
  const [pkg, setPkg] = useState<Package | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    async function open() {
      const token = window.location.hash.replace(/^#/, "");
      if (token) {
        window.history.replaceState(null, "", window.location.pathname);   // the token leaves the address bar and the history entry
        await api.sharedExchange(token);
      }
      return api.sharedPackage();
    }
    open().then((p) => live && setPkg(p)).catch((e: unknown) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => { live = false; };
  }, []);
  if (error) return <main><h1>Compliance package</h1><p role="alert">{error}</p></main>;
  if (!pkg) return <main><p>Loading the package…</p></main>;
  return (
    <main>
      <h1>Compliance package (read-only)</h1>
      <PackageView pkg={pkg} />
      <p><a href={api.sharedPdfUrl()} data-testid="shared-pdf">Download the PDF</a></p>
    </main>
  );
}
