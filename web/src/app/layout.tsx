import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-jetbrains" });

export const metadata: Metadata = {
  title: "tracepin",
  description: "Trace viewer for tracepin findings: waterfall, findings, root cause.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${mono.variable}`}>
      <body className="min-h-screen">
        <header className="border-b hairline bg-panel">
          <nav className="mx-auto flex max-w-[1400px] items-baseline gap-6 px-4 py-2">
            <Link href="/" className="font-semibold tracking-tight">tracepin</Link>
            <Link href="/" className="text-ink-2">runs</Link>
            <Link href="/compare/" className="text-ink-2">compare</Link>
            <span className="ml-auto text-ink-3 mono">agent reliability · OTel GenAI spans · deterministic detectors</span>
          </nav>
        </header>
        <main className="mx-auto max-w-[1400px] px-4 py-4">{children}</main>
      </body>
    </html>
  );
}
