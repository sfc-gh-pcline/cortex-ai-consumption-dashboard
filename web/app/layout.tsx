import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { Suspense } from "react";

import "./globals.css";
import { Providers } from "@/app/providers";
import { DashboardShell } from "@/components/shell/shell";

const geistSans = Geist({ variable: "--font-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Cortex AI Consumption Dashboard",
  description:
    "Credits, tokens, and requests across the Cortex AI surfaces in Snowflake, plus the warehouse compute they run up.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <Providers>
          <Suspense fallback={null}>
            <DashboardShell>{children}</DashboardShell>
          </Suspense>
        </Providers>
      </body>
    </html>
  );
}
