import type { Metadata } from "next";
import type { ReactNode } from "react";

/** The token is in the address: never send it on as a referrer, and keep the page out of search indexes. */
export const metadata: Metadata = { referrer: "no-referrer", robots: { index: false, follow: false } };

export default function ShareLayout({ children }: { children: ReactNode }) {
  return <>{children}</>;
}
