import type { Metadata } from "next";
import "./globals.css";
import "./workspace.css";
import Navbar from "@/components/navbar";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Astra Research",
  description: "An evidence-led workspace for company research, financial analysis and clear investment thinking.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      data-scroll-behavior="smooth"
      className="h-full antialiased"
    >
      <body>
        <Navbar>{children}</Navbar>
      </body>
    </html>
  );
}
