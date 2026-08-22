import type { Metadata } from "next";

import { SessionProvider } from "@/components/SessionProvider";
import "./globals.css";

export const metadata: Metadata = {
  title: "Pramana reviewer console",
  description:
    "Prior-authorization cases the automated gate referred to a clinician, with the evidence assembled.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <SessionProvider>{children}</SessionProvider>
      </body>
    </html>
  );
}
