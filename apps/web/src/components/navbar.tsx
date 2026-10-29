"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ArrowUpRight, BookOpen, Calculator } from "lucide-react";

export default function Navbar({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  if (pathname.startsWith("/login")) return <>{children}</>;
  return <div className="analysis-app">
    <a href="#main-content" className="skip-link">Skip to content</a>
    <header className="analysis-header"><div className="analysis-header-inner">
      <Link href="/models" className="analysis-brand" aria-label="Astra Research stock analysis"><span className="analysis-brand-mark"><svg viewBox="0 0 32 32" fill="none" aria-hidden="true"><path d="M16 3 29 27h-8l-5-10-5 10H3L16 3Z" fill="currentColor" /></svg></span><span>astra<span>RESEARCH & VALUATION</span></span></Link>
      <nav aria-label="Main navigation"><Link href="/models" aria-current={pathname.startsWith("/models") ? "page" : undefined}><Calculator size={16} />Stock analysis</Link><Link href="/methodology" aria-current={pathname === "/methodology" ? "page" : undefined}><BookOpen size={16} />Methodology</Link><form method="post" action="/api/auth/logout"><button type="submit">Sign out</button></form></nav>
    </div></header>
    <main id="main-content" className="workspace-content" tabIndex={-1}>{children}</main>
    <footer className="analysis-footer"><span>ASTRA RESEARCH</span><p>Sourced observations. Transparent calculations.</p><Link href="/methodology">Data & methodology <ArrowUpRight size={13} /></Link></footer>
  </div>;
}
