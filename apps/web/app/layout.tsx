import type { ReactNode } from "react";
import { Shell } from "@/components/Shell";

export const metadata = { title: "MEP Co-pilot" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body style={{ fontFamily: "system-ui, sans-serif", margin: 0, padding: 16 }}>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
