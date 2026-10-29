import { headers } from "next/headers";
import { redirect } from "next/navigation";
import Link from "next/link";
import { LockKeyhole, ArrowRight } from "lucide-react";
import { accessConfiguration, requestAuthorization } from "@/lib/server-security.mjs";
import "./login.css";

export default async function LoginPage({ searchParams }: { searchParams: Promise<{ error?: string }> }) {
  const requestHeaders = await headers();
  const access = requestAuthorization({ url: "http://internal.invalid/login", headers: requestHeaders });
  if (access.authenticated) redirect("/models");
  const configured = accessConfiguration().ready && access.configured;
  const { error } = await searchParams;
  return <main className="private-entry">
    <Link href="/models" className="private-brand"><span>A</span> Astra<span className="private-brand-sub">RESEARCH</span></Link>
    <section className="private-card" aria-labelledby="access-title">
      <div className="private-lock"><LockKeyhole size={22} aria-hidden="true" /></div>
      <p className="eyebrow">PRIVATE WORKSPACE</p>
      <h1 id="access-title">Research with clarity.</h1>
      <p className="private-description">Sign in to analyze real company financials and prepare your valuation workbook and equity report.</p>
      {configured ? <form action="/api/auth/login" method="post" className="private-form">
        <label htmlFor="workspace-password">Workspace password</label>
        <input id="workspace-password" name="password" type="password" autoComplete="current-password" minLength={20} maxLength={512} required autoFocus />
        {error === "invalid" && <p role="alert" className="private-error">The password was not accepted. Please try again.</p>}
        {error === "limited" && <p role="alert" className="private-error">Too many attempts. Please try again in a few minutes.</p>}
        <button type="submit" className="button button-primary">Open workspace <ArrowRight size={15} aria-hidden="true" /></button>
      </form> : <div className="private-unavailable" role="status">Private access has not been configured. The owner must set the server access password and session secret before this workspace can open.</div>}
      <p className="private-footnote">Your data-provider credentials stay on the server.</p>
    </section>
    <p className="private-copyright">Astra Research · Company analysis, from evidence to value.</p>
  </main>;
}
