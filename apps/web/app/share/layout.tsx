import type { Metadata } from "next";
import type { ReactNode } from "react";

/** The certifier page: never send anything on as a referrer, and keep it out of search indexes. */
export const metadata: Metadata = { referrer: "no-referrer", robots: { index: false, follow: false } };

export default function ShareLayout({ children }: { children: ReactNode }) {
  return <>{children}</>;
}
