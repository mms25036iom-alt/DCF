"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { ArrowRight, ArrowUpRight, Building2, Calculator, CheckCircle2, Download, FileText, Info, LoaderCircle, RefreshCw, RotateCcw, SlidersHorizontal, TriangleAlert } from "lucide-react";
import { readApiError } from "@/lib/api";
import { safeReportUrl } from "@/lib/safe-url.mjs";
import { LIVE_MODEL_FIELDS, liveModelDrafts, liveModelNumber, liveModelOverrides, parseLiveModelDrafts } from "@/lib/live-model-inputs.mjs";
import "./live-model-viewer.css";

type DisplayUnit = "auto" | "crore" | "lakh" | "million" | "absolute";
type ModelRow = Record<string, string | number | null> & { year: number; period?: string };
type Assumptions = Record<string, number>;
type StockModel = {
  model_type: "LIVE"; company: string; ticker: string; currency: string; snapshot_id?: string; input_overrides?: Assumptions | null;
  unit: { key: string; label: string; divisor: number; amount_label: string };
  source_snapshot: { sector?: string; industry?: string; business_summary?: string; website?: string; current_price?: number | null; price_as_of?: string | null; retrieved_at?: string; cache_hit?: boolean; citations?: { title?: string; uri?: string; retrieved_label?: string }[] };
  historical: ModelRow[]; base_period: string | null; base_year: number | null;
  assumptions: Assumptions; assumption_notes: Record<string, string>;
  forecast: ModelRow[]; valuation: Record<string, number | null> | null;
  sensitivity: { discount_rates: number[]; terminal_growth_rates: number[]; values: (number | null)[][] };
  warnings: string[]; withheld_reason: string | null;
  valuation_date?: string | null;
  quality?: {status:string;issues:string[]};
  terminal?: {revenue:number;ebit:number;nopat:number;growth:number;roic:number;reinvestment_rate:number;reinvestment:number;free_cash_flow:number;method:string} | null;
  wacc?: {method: string; inputs: Assumptions; cost_of_equity: number; after_tax_cost_of_debt: number; calculated_wacc: number; active_wacc: number};
  working_capital?: {method: string; base_balance: number | null; base_observations?: boolean; base_components?: Record<string, number | null>; forecast: ModelRow[]};
};
type ModelRequest = { ticker: string; display_unit: DisplayUnit; assumptions: Assumptions | null; refresh?: boolean; snapshot_id?: string };
type View = "forecast" | "history" | "working-capital" | "wacc" | "valuation" | "sensitivity";
const VIEWS: { key: View; name: string }[] = [{ key: "forecast", name: "Forecast" }, { key: "history", name: "Historical financials" }, { key: "working-capital", name: "Working capital" }, { key: "wacc", name: "WACC" }, { key: "valuation", name: "DCF valuation" }, { key: "sensitivity", name: "Sensitivity" }];
const UNITS: { value: DisplayUnit; label: string }[] = [{ value: "auto", label: "Auto · INR crore / other millions" }, { value: "crore", label: "Crore · 1,00,00,000" }, { value: "lakh", label: "Lakh · 1,00,000" }, { value: "million", label: "Million · 1,000,000" }, { value: "absolute", label: "Absolute currency units" }];
const historicalRows = [{ key: "revenue", label: "Revenue" }, { key: "ebit", label: "Operating profit / EBIT" }, { key: "ebitda", label: "EBITDA" }, { key: "net_income", label: "Net income" }, { key: "operating_cash_flow", label: "Operating cash flow" }, { key: "capital_expenditure", label: "Capital expenditure" }, { key: "depreciation", label: "Depreciation & amortisation" }, { key: "total_debt", label: "Total debt" }, { key: "cash_and_equivalents", label: "Cash & equivalents" }, { key: "shareholders_equity", label: "Shareholders’ equity" }, { key: "income_tax", label: "Income tax" }, { key: "pretax_income", label: "Profit before tax" }];
const forecastRows = [{ key: "revenue", label: "Revenue" }, { key: "ebit", label: "Operating profit / EBIT" }, { key: "nopat", label: "After-tax operating profit" }, { key: "depreciation", label: "Add: depreciation" }, { key: "capital_expenditure", label: "Less: capital expenditure" }, { key: "change_in_working_capital", label: "Less: change in working capital" }, { key: "free_cash_flow", label: "Full-year free cash flow to firm" }, { key: "valuation_free_cash_flow", label: "Cash flow used in valuation (first-year stub)" }, { key: "present_value", label: "Present value of cash flow" }];
const date = (value?: string | null) => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString("en-IN", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "Not supplied";
const rate = (value?: number | null) => typeof value === "number" && Number.isFinite(value) ? `${liveModelNumber(value * 100, 1, 1)}%` : "—";

async function retrieveModel(request: ModelRequest, signal: AbortSignal): Promise<StockModel> {
  const response = await fetch("/api/v1/stocks/model", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request), signal, cache: "no-store" });
  if (!response.ok) throw new Error(await readApiError(response));
  const model = await response.json() as StockModel;
  if (model.model_type !== "LIVE" || !model.ticker || !Array.isArray(model.historical) || !model.unit?.divisor) throw new Error("The provider did not return a valid live financial model.");
  return model;
}

function FinancialTable({ model, view }: { model: StockModel; view: "history" | "forecast" }) {
  const observations = view === "history" ? model.historical : model.forecast;
  const rows = view === "history" ? historicalRows : forecastRows;
  if (!observations.length) return <div className="live-model-unavailable"><Calculator size={27} /><h3>{view === "forecast" ? "DCF forecast unavailable" : "Annual statements unavailable"}</h3><p>{model.withheld_reason || "The source did not return the observations needed for this view. Missing financials are kept blank."}</p></div>;
  return <><div className="live-financial-table" role="region" aria-label={`${view === "history" ? "Historical" : "Forecast"} financials, ${model.unit.amount_label}`} tabIndex={0}><table><caption className="sr-only">{model.company} {view === "history" ? "reported annual observations" : "illustrative forecast"}; amounts in {model.unit.amount_label}</caption><thead><tr><th scope="col">Financial metric</th>{observations.map((row, index) => <th key={`${row.year}-${index}`} scope="col">{view === "history" ? row.period || row.year : `${row.year}E`}</th>)}</tr></thead><tbody>{rows.map(row => <tr key={row.key} className={["revenue", "free_cash_flow"].includes(row.key) ? "live-total-row" : ""}><th scope="row">{row.label}</th>{observations.map((observation, index) => <td key={index}>{liveModelNumber(observation[row.key], model.unit.divisor)}</td>)}</tr>)}</tbody></table></div><p className="live-table-footnote">{view === "history" ? "Fiscal period ends are shown as supplied by Yahoo. A dash means the source did not provide a usable observation." : `E = estimate. Forecasts begin after the complete source period ${model.base_period || model.base_year || "shown above"}. The assumptions are editable; projected figures are estimates.`}</p></>;
}

function RevenueChart({ model }: { model: StockModel }) {
  const history = model.historical.filter(row => !model.base_period || !row.period || row.period <= model.base_period);
  const rows = [...history, ...model.forecast];
  const values = rows.map(row => typeof row.revenue === "number" ? row.revenue / model.unit.divisor : null);
  const finite = values.filter((value): value is number => value !== null && Number.isFinite(value));
  if (finite.length < 2) return null;
  const low = Math.min(0, ...finite), high = Math.max(...finite) * 1.12 || 1;
  const x = (index: number) => 55 + index * 540 / Math.max(1, values.length - 1);
  const y = (value: number) => 177 - (value - low) / (high - low || 1) * 148;
  function path(start: number, end: number) { let result = "", gap = true; for (let i = start; i <= end; i++) { const value = values[i]; if (value === null || value === undefined) { gap = true; continue; } result += `${gap ? "M" : "L"}${x(i)},${y(value)} `; gap = false; } return result; }
  return <section className="panel live-revenue-chart"><div className="panel-header"><div><p className="eyebrow">OBSERVATIONS & EXPECTATIONS</p><h2>Revenue trajectory</h2><p>{model.unit.amount_label} · historical observations and the active forecast</p></div><div className="live-chart-legend"><span><i />Reported</span>{model.forecast.length > 0 && <span><i />Forecast</span>}</div></div><div className="live-chart-canvas"><svg viewBox="0 0 625 213" role="img" aria-label={`Revenue for ${model.company}. Reported financials and forecast values are available in the tables below.`}>{[0, 1, 2, 3].map(index => { const value = low + (high - low) * index / 3; return <g key={index}><line x1="55" x2="600" y1={y(value)} y2={y(value)} stroke="#e1e9e2" strokeDasharray="3 5" /><text x="44" y={y(value) + 4} textAnchor="end">{liveModelNumber(value, 1, 0)}</text></g>; })}<path d={path(0, history.length - 1)} fill="none" stroke="#879d91" strokeWidth="2.5" />{model.forecast.length > 0 && <><line x1={x(history.length - 1)} x2={x(history.length - 1)} y1="22" y2="179" stroke="#c9d8cc" strokeDasharray="4 4" /><path d={path(Math.max(0, history.length - 1), values.length - 1)} fill="none" stroke="#18786b" strokeWidth="2.8" strokeDasharray="6 4" /></>}{rows.map((row, index) => index === 0 || index === rows.length - 1 || index === history.length - 1 || (rows.length < 7 && index % 2 === 0) ? <text key={index} x={x(index)} y="205" textAnchor="middle">{row.year}{index >= history.length ? "E" : ""}</text> : null)}</svg></div></section>;
}

const INPUT_SECTIONS = [
  { title: "Operating forecast", keys: ["revenue_growth", "revenue_growth_end", "ebit_margin", "tax_rate", "depreciation_rate", "capex_rate"] },
  { title: "Working capital", keys: ["working_capital_method", "working_capital_rate", "cogs_rate", "receivable_days", "inventory_days", "payable_days"] },
  { title: "Cost of capital", keys: ["wacc_override", "risk_free_rate", "equity_risk_premium", "beta", "pretax_cost_of_debt", "debt_weight", "discount_rate", "terminal_growth", "terminal_roic"] },
  { title: "Capital structure", keys: ["net_debt", "shares_outstanding"] },
];

function TerminalBuild({ model }: { model: StockModel }) {
  const terminal = model.terminal;
  if (!terminal) return null;
  return <section aria-label="Stable-growth terminal calculation"><h4>Stable-growth terminal calculation</h4><dl className="live-valuation-bridge">
    <div><dt>Terminal operating profit after tax</dt><dd>{liveModelNumber(terminal.nopat, model.unit.divisor)}</dd></div>
    <div><dt>Stable ROIC / perpetual growth</dt><dd>{rate(terminal.roic)} / {rate(terminal.growth)}</dd></div>
    <div><dt>Reinvestment rate <span>Perpetual growth ÷ stable ROIC</span></dt><dd>{rate(terminal.reinvestment_rate)}</dd></div>
    <div><dt>Required terminal reinvestment</dt><dd>{liveModelNumber(terminal.reinvestment, model.unit.divisor)}</dd></div>
    <div className="is-total"><dt>Sustainable terminal FCFF</dt><dd>{liveModelNumber(terminal.free_cash_flow, model.unit.divisor)}</dd></div>
  </dl><p className="live-table-footnote">Amounts in {model.unit.amount_label}. Terminal FCFF = NOPAT × (1 − growth / ROIC). {terminal.method === "roic_equals_wacc" ? "Stable ROIC is assumed equal to WACC, so no perpetual excess return is assumed." : "Stable ROIC uses your explicit scenario input."} Review growth and reinvestment together.</p></section>;
}

function CalculationSchedule({ model, view }: { model: StockModel; view: "working-capital" | "wacc" }) {
  const wacc = model.wacc;
  const wc = model.working_capital;
  if (view === "wacc") return wacc ? <><dl className="live-valuation-bridge">
    <div><dt>Cost of equity <span>Risk-free rate + beta × equity risk premium</span></dt><dd>{rate(wacc.cost_of_equity)}</dd></div>
    <div><dt>After-tax cost of debt <span>Pre-tax debt cost × (1 − tax rate)</span></dt><dd>{rate(wacc.after_tax_cost_of_debt)}</dd></div>
    <div><dt>Debt weight / equity weight</dt><dd>{rate(wacc.inputs.debt_weight)} / {rate(1 - wacc.inputs.debt_weight)}</dd></div>
    <div className="is-total"><dt>Calculated WACC <span>Weighted equity cost + weighted after-tax debt cost</span></dt><dd>{rate(wacc.calculated_wacc)}</dd></div>
    <div className="is-value"><dt>Active discount rate <span>{wacc.method === "manual" ? "Explicit manual override" : "Linked CAPM and debt calculation"}</span></dt><dd>{rate(wacc.active_wacc)}</dd></div>
  </dl><p className="live-table-footnote">Risk-free rates, equity risk premiums, borrowing costs, and target weights can be assumptions. Inspect each driver’s source note; a default is not presented as a current market observation.</p></> : <div className="live-model-unavailable"><Info size={24} /><h3>WACC schedule unavailable</h3><p>The financial inputs required for this calculation were not available.</p></div>;
  if (!wc?.forecast.length) return <div className="live-model-unavailable"><Info size={24} /><h3>Working-capital schedule unavailable</h3><p>{model.withheld_reason || "A supported operating forecast is required."}</p></div>;
  const rows = [{key:"receivables",label:"Accounts receivable"},{key:"inventory",label:"Inventory"},{key:"payables",label:"Accounts payable"},{key:"working_capital_balance",label:"Noncash operating working capital"},{key:"change_in_working_capital",label:"Change in working capital"}];
  return <><div className="live-financial-table" role="region" aria-label="Forecast working-capital schedule" tabIndex={0}><table><caption className="sr-only">Working capital in {model.unit.amount_label}</caption><thead><tr><th scope="col">Working-capital metric</th>{wc.forecast.map(row=><th key={row.year} scope="col">{row.year}E</th>)}</tr></thead><tbody>{rows.map(row=><tr key={row.key}><th scope="row">{row.label}</th>{wc.forecast.map(year=><td key={year.year}>{liveModelNumber(year[row.key],model.unit.divisor)}</td>)}</tr>)}</tbody></table></div><p className="live-table-footnote">{wc.method === "operating_days" ? "Receivables = revenue × DSO / 365; inventory = cost of revenue × DIO / 365; payables = cost of revenue × DPO / 365. Operating working capital = receivables + inventory − payables." : "Operating working capital uses the explicit revenue-ratio assumption. Component balances are unavailable for this method and stay blank."} FCFF deducts each forecast year’s change from the preceding balance. Base balance: {liveModelNumber(wc.base_balance, model.unit.divisor)} {model.unit.amount_label}.</p></>;
}

export default function LiveModelViewer({ initialTicker = "" }: { initialTicker?: string }) {
  const [ticker, setTicker] = useState(initialTicker);
  const [displayUnit, setDisplayUnit] = useState<DisplayUnit>("auto");
  const [model, setModel] = useState<StockModel | null>(null);
  const [applied, setApplied] = useState<ModelRequest | null>(null);
  const [drafts, setDrafts] = useState<Record<string,string>>({});
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<"load" | "recalculate" | "export" | "report" | null>(initialTicker ? "load" : null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [view, setView] = useState<View>("forecast");
  const [researchBrief, setResearchBrief] = useState("");
  const [sourceUrls, setSourceUrls] = useState("");
  const [includeAi, setIncludeAi] = useState(false);
  const [capabilities, setCapabilities] = useState<{ai_available:boolean;model_provider?:string}>({ai_available:false});
  const controller = useRef<AbortController | null>(null);
  const parsed = useMemo(()=>parseLiveModelDrafts(drafts,model?.unit.divisor || 1),[drafts,model?.unit.divisor]);
  const hasErrors = Object.keys(parsed.errors).length>0;
  const requestChanged = Boolean(model && (ticker.trim().toUpperCase()!==model.ticker.toUpperCase() || displayUnit!==applied?.display_unit));
  const stale = dirty || requestChanged;
  const usableAssumptions = Boolean(model && LIVE_MODEL_FIELDS.every(field=>typeof model.assumptions[field.key]==="number"));
  const apply = useCallback((next: StockModel, request: ModelRequest)=>{
    setModel(next); setApplied({...request,ticker:next.ticker,refresh:false,snapshot_id:next.snapshot_id});
    setTicker(next.ticker); setDisplayUnit(request.display_unit); setDrafts(liveModelDrafts(next.assumptions,next.unit.divisor)); setDirty(false); setView(next.forecast.length ? "forecast" : "history");
  },[]);

  useEffect(()=>{
    const active=new AbortController();
    void fetch("/api/v1/config",{signal:active.signal,cache:"no-store"}).then(response=>response.ok?response.json():null).then(value=>{if(!active.signal.aborted && value)setCapabilities({ai_available:value.ai_available===true,model_provider:typeof value.model_provider==="string"?value.model_provider:undefined});}).catch(()=>{});
    return ()=>active.abort();
  },[]);
  useEffect(()=>{
    if(!initialTicker)return;
    const active=new AbortController(); controller.current=active;
    const request:ModelRequest={ticker:initialTicker,display_unit:"auto",assumptions:null};
    void retrieveModel(request,active.signal).then(next=>{if(!active.signal.aborted)apply(next,request);}).catch(cause=>{if(!active.signal.aborted)setError(cause instanceof Error?cause.message:"Stock financials could not be loaded.");}).finally(()=>{if(!active.signal.aborted)setBusy(null);});
    return ()=>active.abort();
  },[initialTicker,apply]);
  useEffect(()=>()=>controller.current?.abort(),[]);

  async function load(request:ModelRequest,action:"load"|"recalculate") {
    controller.current?.abort(); const active=new AbortController(); controller.current=active;
    setBusy(action); setError(null); setNotice("");
    try { const next=await retrieveModel(request,active.signal); if(!active.signal.aborted){apply(next,request);setNotice(action==="recalculate"?"The forecast, schedules, and DCF now use your applied assumptions.":"Company financials loaded. Review the source dates and editable assumptions.");} }
    catch(cause){if(!active.signal.aborted)setError(cause instanceof Error?cause.message:"Stock financials could not be retrieved.");}
    finally{if(!active.signal.aborted)setBusy(null);}
  }
  function submit(event:FormEvent<HTMLFormElement>){event.preventDefault();if(!ticker.trim()||busy)return;void load({ticker:ticker.trim(),display_unit:displayUnit,assumptions:null},"load");}
  function recalculate(event:FormEvent<HTMLFormElement>){event.preventDefault();if(!model||busy||hasErrors||requestChanged)return;void load({ticker:model.ticker,display_unit:displayUnit,assumptions:liveModelOverrides(model.assumptions,parsed.values,applied?.assumptions,model.unit.divisor),snapshot_id:model.snapshot_id},"recalculate");}
  function reset(){if(!model)return;setDrafts(liveModelDrafts(model.assumptions,model.unit.divisor));setDirty(false);setError(null);setNotice("Unapplied edits discarded. The last calculated assumptions are shown.");}
  function update(key:string,value:string){setDrafts(current=>({...current,[key]:value}));setDirty(true);setNotice("");}

  async function download(kind:"excel"|"pdf") {
    if(!model||!applied||busy||stale)return;
    const urls=sourceUrls.split(/\r?\n/).map(value=>value.trim()).filter(Boolean);
    if(kind==="pdf" && urls.length>3){setError("Use up to three original-source URLs, one per line.");return;}
    if(kind==="pdf" && urls.some(value=>{try{const url=new URL(value);return url.protocol!=="https:"||Boolean(url.username)||Boolean(url.password);}catch{return true;}})){setError("Use complete HTTPS source URLs without embedded credentials.");return;}
    controller.current?.abort();const active=new AbortController();controller.current=active;
    setBusy(kind==="excel"?"export":"report");setError(null);setNotice("");
    try {
      const endpoint=kind==="excel"?"/api/v1/stocks/model/export":"/api/v1/equity/report";
      const payload=kind==="excel"?applied:{...applied,research_brief:researchBrief.trim(),source_urls:urls,include_ai:includeAi&&capabilities.ai_available};
      const response=await fetch(endpoint,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload),signal:active.signal,cache:"no-store"});
      if(!response.ok)throw new Error(await readApiError(response));
      const type=response.headers.get("content-type")||"";
      if(kind==="pdf"?!type.includes("application/pdf"):!type.includes("spreadsheetml"))throw new Error("The server did not return the requested document. Please sign in again if your session expired.");
      const url=URL.createObjectURL(await response.blob());const anchor=document.createElement("a");anchor.href=url;
      anchor.download=`astra-${model.ticker.replace(/[^a-z0-9_-]/gi,"-").toLowerCase()}-${kind==="pdf"?"equity-research.pdf":"dcf-model.xlsx"}`;
      document.body.appendChild(anchor);anchor.click();anchor.remove();window.setTimeout(()=>URL.revokeObjectURL(url),1000);
      setNotice(kind==="pdf"?"Equity research PDF downloaded using the applied model and source observations.":"Excel workbook downloaded with editable assumptions and linked calculation schedules.");
    }catch(cause){if(!active.signal.aborted)setError(cause instanceof Error?cause.message:"The document could not be exported.");}
    finally{if(!active.signal.aborted)setBusy(null);}
  }
  function tabKeys(event:KeyboardEvent<HTMLButtonElement>,index:number){let next=index;if(event.key==="ArrowRight")next=(index+1)%VIEWS.length;else if(event.key==="ArrowLeft")next=(index+VIEWS.length-1)%VIEWS.length;else if(event.key==="Home")next=0;else if(event.key==="End")next=VIEWS.length-1;else return;event.preventDefault();setView(VIEWS[next].key);document.getElementById(`live-model-tab-${VIEWS[next].key}`)?.focus();}
  const currency=model?.currency||"";
  const money=(value?:number|null)=>liveModelNumber(value,model?.unit.divisor||1);
  const valuation=model?.valuation;
  const title={forecast:"Five-year operating forecast",history:"Historical financial statements","working-capital":"Operating working-capital schedule",wacc:"Cost of capital build",valuation:"Enterprise value to equity value",sensitivity:"WACC and terminal-growth sensitivity"}[view];
  function visibleDriver(key:string){if(key==="working_capital_rate")return Number(drafts.working_capital_method)===0;if(["cogs_rate","receivable_days","inventory_days","payable_days"].includes(key))return Number(drafts.working_capital_method)===1;if(key==="discount_rate")return Number(drafts.wacc_override)===1;if(["risk_free_rate","equity_risk_premium","beta","pretax_cost_of_debt","debt_weight"].includes(key))return Number(drafts.wacc_override)===0;return true;}

  return <div className="page-container live-stock-model">
    <header className="page-header"><div><p className="eyebrow">EQUITY RESEARCH & VALUATION</p><h1>Understand the business. Follow the value.</h1><p>Actual financial observations, an editable DCF workbook, and a documented equity research report.</p></div><span className="badge badge-teal"><span className="status-dot"/>Real company data</span></header>
    <form className="panel live-stock-search" onSubmit={submit}>
      <div className="field"><label className="field-label" htmlFor="live-stock-ticker">Company ticker</label><input id="live-stock-ticker" className="input" value={ticker} required maxLength={40} autoCapitalize="characters" spellCheck={false} placeholder="e.g. RELIANCE.NS or TCS.NS" disabled={Boolean(busy)} onChange={event=>{setTicker(event.target.value);setError(null);}}/><p className="field-help">Use .NS for NSE or .BO for BSE. A bare symbol is interpreted as NSE.</p></div>
      <div className="field"><label className="field-label" htmlFor="live-model-unit">Financial statement units</label><select id="live-model-unit" className="select" value={displayUnit} disabled={Boolean(busy)} onChange={event=>setDisplayUnit(event.target.value as DisplayUnit)}>{UNITS.map(unit=><option value={unit.value} key={unit.value}>{unit.label}</option>)}</select><p className="field-help">Presentation scale only. Prices and share counts remain unscaled.</p></div>
      <button type="submit" className="button button-primary" disabled={Boolean(busy)||!ticker.trim()}>{busy==="load"?<LoaderCircle size={16} className="animate-spin"/>:<Calculator size={16}/>} {busy==="load"?"Loading financials…":"Analyze stock"}<ArrowRight size={15}/></button>
    </form>
    {error&&<div className="alert alert-error live-model-alert" role="alert"><TriangleAlert size={18}/><div><strong>Request could not be completed</strong><p>{error}</p>{model&&<p>The last calculated results remain below. Apply your current inputs successfully before exporting.</p>}</div></div>}
    {notice&&<p className="live-model-notice" role="status"><CheckCircle2 size={15}/>{notice}</p>}
    {busy==="load"&&!model&&<section className="panel live-stock-loading" role="status"><LoaderCircle size={30} className="animate-spin"/><h2>Retrieving company financials</h2><p>Checking the requested listing and available annual statements. The data provider may take several seconds.</p></section>}
    {!model&&!busy&&<section className="panel live-stock-intro live-stock-intro-compact"><span className="live-intro-symbol"><Building2 size={28} strokeWidth={1.5}/></span><p className="eyebrow">START WITH YOUR COMPANY</p><h2>A clear financial model.<br/>A research record you can question.</h2><p>Enter a ticker above. Historical financials, operating forecasts, working capital, WACC, and DCF calculations stay connected from the browser to your Excel workbook.</p><div><span><CheckCircle2 size={15}/>Sourced financial observations</span><span><CheckCircle2 size={15}/>Linked Excel schedules</span><span><CheckCircle2 size={15}/>Multi-page research PDF</span></div></section>}
    {model&&<>
      <section className="live-stock-identity"><div><div className="live-stock-meta"><span className="badge badge-teal">REPORTED OBSERVATIONS</span><span>{model.ticker}</span>{model.source_snapshot.sector&&<span>{model.source_snapshot.sector}</span>}</div><h2>{model.company}</h2><p>Amounts: <strong>{model.unit.amount_label}</strong> · per-share values: <strong>{currency} per share</strong> · model base: <strong>{model.base_period||"Not established"}</strong></p></div><div className="page-actions"><button className="button button-secondary" type="button" disabled={Boolean(busy)||stale} onClick={()=>void load({ticker:model.ticker,display_unit:displayUnit,assumptions:null,refresh:true},"load")}><RefreshCw size={15}/>Refresh data</button><button className="button button-primary" type="button" disabled={Boolean(busy)||stale} onClick={()=>void download("excel")}>{busy==="export"?<LoaderCircle size={16} className="animate-spin"/>:<Download size={16}/>}Download Excel</button></div></section>
      {stale&&<div className="alert alert-info live-model-alert" role="status"><Info size={18}/><div><strong>{requestChanged?"Load the selected stock and units":"Apply your assumption changes"}</strong><p>{requestChanged?"The results below still belong to the last loaded model. Loading resets assumptions from the source observations.":"The results below use the last applied inputs. Recalculate or discard your edits before downloading."}</p></div></div>}
      {model.withheld_reason&&<div className="alert alert-info live-model-alert"><Info size={18}/><div><strong>A supported operating-company valuation is unavailable</strong><p>{model.withheld_reason}</p><p>The available source financials remain downloadable.</p></div></div>}
      {model.forecast.length>0&&<div className="alert alert-info live-model-alert"><Info size={18}/><div><strong>{model.quality?.status === "sourced_inputs" ? "Dated market inputs · forecasts require judgment" : "Assumption and source review required"}</strong><p>Valuation date: {model.valuation_date || "Not established"}. This is an analytical scenario. A difference from the observed share price is not an accuracy score or a verified mispricing signal.</p>{Boolean(model.quality?.issues.length)&&<details><summary>Review the model basis</summary><ul>{model.quality?.issues.map((issue,index)=><li key={index}>{issue}</li>)}</ul></details>}</div></div>}
      <div className="live-stock-kpis"><article className="panel"><span className="section-label">OBSERVED PRICE / SHARE</span><strong>{liveModelNumber(model.source_snapshot.current_price)}</strong><p>{currency} · {date(model.source_snapshot.price_as_of)}</p></article><article className="panel"><span className="section-label">DCF ENTERPRISE VALUE</span><strong>{money(valuation?.enterprise_value)}</strong><p>{model.unit.amount_label} · applied assumptions</p></article><article className="panel live-value-card"><span className="section-label">MODEL VALUE / SHARE</span><strong>{liveModelNumber(valuation?.per_share)}</strong><p>{currency} per share · analytical scenario</p></article><article className="panel"><span className="section-label">ACTIVE WACC</span><strong>{rate(model.assumptions.discount_rate)}</strong><p>{model.wacc?.method==="manual"?"Explicit manual override":"Linked cost-of-capital build"}</p></article></div>
      <div className="live-model-grid"><aside className="panel live-model-assumptions" aria-label="DCF assumptions"><div className="panel-header"><div><p className="eyebrow">THE MODEL DRIVERS</p><h2>Assumptions</h2></div><SlidersHorizontal size={18}/></div>{usableAssumptions?<form onSubmit={recalculate}><fieldset disabled={Boolean(busy)||requestChanged}>{INPUT_SECTIONS.map((section,index)=><details className="live-driver-section" key={section.title} open={index===0}><summary>{section.title}</summary><div className="live-assumption-fields">{section.keys.filter(visibleDriver).map(key=>{const field=LIVE_MODEL_FIELDS.find(item=>item.key===key)!;return <div className="field" key={key}><label className="field-label" htmlFor={`live-assumption-${key}`}>{field.label}</label>{field.kind==="mode"?<select id={`live-assumption-${key}`} className="select" value={drafts[key]??"0"} onChange={event=>update(key,event.target.value)}>{key==="wacc_override"?<><option value="0">Linked CAPM + debt cost</option><option value="1">Manual WACC override</option></>:<><option value="0">Working capital / revenue</option><option value="1">Receivable, inventory & payable days</option></>}</select>:<div className={`live-assumption-input ${dirty&&parsed.errors[key]?"has-error":""}`}><input id={`live-assumption-${key}`} type="number" step="any" value={drafts[key]??""} aria-invalid={dirty&&Boolean(parsed.errors[key])} aria-describedby={`live-assumption-note-${key}`} onChange={event=>update(key,event.target.value)}/><span>{field.kind==="percent"?"%":field.kind==="money"?model.unit.label:field.kind==="days"?"days":field.kind==="shares"?"shares":"×"}</span></div>}<p id={`live-assumption-note-${key}`} className={dirty&&parsed.errors[key]?"live-input-error":"live-input-note"}>{dirty&&parsed.errors[key]?parsed.errors[key]:model.assumption_notes[key]||"Editable model assumption."}</p></div>;})}</div></details>)}</fieldset>{dirty&&hasErrors&&<p className="live-input-error live-driver-validation" role="alert">Review the highlighted inputs before recalculating. {parsed.errors.terminal_growth||""}</p>}<div className="live-assumptions-actions"><button type="submit" className="button button-primary" disabled={Boolean(busy)||!dirty||hasErrors||requestChanged}>{busy==="recalculate"?<LoaderCircle size={15} className="animate-spin"/>:<RefreshCw size={15}/>}Recalculate model</button><button className="button button-ghost" type="button" disabled={Boolean(busy)||!dirty} onClick={reset}><RotateCcw size={14}/>Discard edits</button></div></form>:<div className="live-assumption-missing"><Info size={21}/><p>Comparable source inputs are incomplete or this operating-company method is unsuitable.</p><p>Available historical observations remain in the export.</p></div>}</aside>
      <div className="live-model-results"><RevenueChart model={model}/><section className="panel live-stock-tables"><div className="live-model-tabs" role="tablist" aria-label="Financial model schedules">{VIEWS.map((tab,index)=><button type="button" key={tab.key} id={`live-model-tab-${tab.key}`} role="tab" aria-selected={view===tab.key} aria-controls={`live-model-panel-${tab.key}`} tabIndex={view===tab.key?0:-1} onKeyDown={event=>tabKeys(event,index)} onClick={()=>setView(tab.key)}>{tab.name}</button>)}</div><div id={`live-model-panel-${view}`} role="tabpanel" aria-labelledby={`live-model-tab-${view}`}><div className="live-table-title"><div><h3>{title}</h3><p>{view==="sensitivity"?`${currency} per share`:view==="wacc"?"Linked capital-cost assumptions":`${model.unit.amount_label} · ${view==="history"?"source observations":"analytical calculations"}`}</p></div><span className="badge badge-neutral">{view==="history"?"ACTUALS":"MODEL"}</span></div>
        {(view==="history"||view==="forecast")&&<FinancialTable model={model} view={view}/>}
        {(view==="working-capital"||view==="wacc")&&<CalculationSchedule model={model} view={view}/>}
        {view==="valuation"&&(valuation?<><dl className="live-valuation-bridge">{[{key:"present_value_fcff",label:"PV of forecast FCFF"},{key:"terminal_value",label:"Undiscounted terminal value"},{key:"present_value_terminal",label:"PV of terminal value"},{key:"enterprise_value",label:"Enterprise value"},{key:"noncontrolling_interest",label:"Less: non-controlling interests"},{key:"net_debt",label:"Less: net debt / (cash)"},{key:"equity_value",label:"Equity value"}].map(row=><div key={row.key} className={["enterprise_value","equity_value"].includes(row.key)?"is-total":""}><dt>{row.label}</dt><dd>{money(valuation[row.key])}</dd></div>)}<div><dt>Shares outstanding<span>Absolute shares</span></dt><dd>{liveModelNumber(valuation.shares_outstanding,1,0)}</dd></div><div className="is-value"><dt>Model value per share<span>{currency} per share</span></dt><dd>{liveModelNumber(valuation.per_share)}</dd></div></dl><p className="live-table-footnote">FCFF = EBIT minus cash tax on positive EBIT + depreciation minus capital spending and working-capital investment. Actual-day discounting uses the valuation date; the first incomplete fiscal year is prorated assuming uniform cash generation. Active WACC {rate(model.assumptions.discount_rate)}; terminal growth {rate(model.assumptions.terminal_growth)}.{valuation.noncontrolling_interest == null && " Reported non-controlling interests were unavailable; that adjustment was omitted, rather than verified as zero."}</p><TerminalBuild model={model}/></>:<div className="live-model-unavailable"><Info size={25}/><h3>Valuation withheld</h3><p>{model.withheld_reason||"A supported DCF could not be calculated."}</p></div>)}
        {view==="sensitivity"&&(model.sensitivity.values.length?<><div className="live-financial-table live-sensitivity-table" role="region" aria-label="DCF per-share sensitivity" tabIndex={0}><table><caption className="sr-only">Value per share in {currency}; WACC by rows and terminal growth by columns</caption><thead><tr><th scope="col">WACC / growth</th>{model.sensitivity.terminal_growth_rates.map((value,index)=><th key={index} scope="col">{rate(value)}</th>)}</tr></thead><tbody>{model.sensitivity.discount_rates.map((value,row)=><tr key={row}><th scope="row">{rate(value)}</th>{model.sensitivity.terminal_growth_rates.map((growth,column)=><td key={column} className={Math.abs(value-model.assumptions.discount_rate)<1e-8&&Math.abs(growth-model.assumptions.terminal_growth)<1e-8?"is-active-value":""}>{liveModelNumber(model.sensitivity.values[row]?.[column])}</td>)}</tr>)}</tbody></table></div><p className="live-table-footnote">Operating cash-flow assumptions are held constant. Terminal reinvestment is recomputed for each growth assumption. Default stable ROIC follows each row’s WACC unless you override ROIC. Invalid WACC / growth pairs remain unavailable. The highlighted cell uses the applied valuation assumptions.</p></>:<div className="live-model-unavailable"><Info size={25}/><h3>Sensitivity unavailable</h3><p>A supported DCF is required before varying WACC and perpetual growth.</p></div>)}
      </div></section>
      <section className="panel live-report-builder" aria-labelledby="equity-export-title"><div className="panel-header"><div><p className="eyebrow">THE RESEARCH RECORD</p><h2 id="equity-export-title">Download equity research</h2><p>Use the same applied financial observations and assumptions as your workbook.</p></div><FileText size={23}/></div><div className="panel-body"><div className="field"><label className="field-label" htmlFor="equity-research-brief">Research brief <span className="live-optional">Optional</span></label><textarea id="equity-research-brief" className="textarea" rows={3} maxLength={4000} value={researchBrief} disabled={Boolean(busy)} onChange={event=>setResearchBrief(event.target.value)} placeholder="Describe the business questions, operating risks, and assumptions you want the report to examine."/><p className="field-help">The PDF separates available evidence, model assumptions, valuation, risks, and missing information.</p></div><div className="field"><label className="field-label" htmlFor="equity-source-urls">Original-source URLs <span className="live-optional">Optional · up to 3</span></label><textarea id="equity-source-urls" className="textarea live-source-url-input" rows={2} value={sourceUrls} maxLength={3000} disabled={Boolean(busy)} onChange={event=>setSourceUrls(event.target.value)} placeholder="https://www.sec.gov/Archives/..."/><p className="field-help">One HTTPS link per line from the official company website, sec.gov, nseindia.com or bseindia.com. Maximum 1 MiB and 30 PDF pages per document. Use the company’s annual reports, filings, or investor disclosures. Retrieved context is identified separately from the model’s observations.</p></div>{capabilities.ai_available&&<label className="live-ai-consent"><input type="checkbox" checked={includeAi} disabled={Boolean(busy)} onChange={event=>setIncludeAi(event.target.checked)}/><span>Include optional AI narrative using the configured {capabilities.model_provider||"model"} provider.<small>This sends your research brief, financial observations, and public-source context to that provider. Review generated claims against their evidence.</small></span></label>}<div className="live-report-actions"><p>{includeAi&&capabilities.ai_available?"AI narrative supplements the sourced data and calculations.":"Data-only research needs no language-model API key."}</p><button className="button button-primary" type="button" disabled={Boolean(busy)||stale} onClick={()=>void download("pdf")}>{busy==="report"?<LoaderCircle size={16} className="animate-spin"/>:<FileText size={16}/>} {busy==="report"?"Preparing research PDF…":"Download research PDF"}</button></div></div></section>
      </div></div>
      <section className="panel live-model-provenance"><div><h3>Source record & methodology</h3><p>Retrieved {date(model.source_snapshot.retrieved_at)} · {model.source_snapshot.cache_hit?"Provider cache":"Provider retrieval"}. Yahoo Finance is a secondary data source; prices may be delayed and statement coverage may be incomplete.</p><p>Financial observations retain their reporting currency. Changing units applies no currency conversion. Forecast drivers and DCF outputs are assumptions and calculations; missing observations remain unavailable.</p></div><div className="live-source-links">{model.source_snapshot.citations?.map((citation,index)=>{const href=safeReportUrl(citation.uri);return href?<a href={href} target="_blank" rel="noopener noreferrer" key={index}>{citation.title||"Source reference"}<ArrowUpRight size={13}/></a>:null;})}</div>{model.warnings.length>0&&<details><summary>{model.warnings.length} source and model {model.warnings.length===1?"limitation":"limitations"}</summary><ul>{model.warnings.map((warning,index)=><li key={index}>{warning}</li>)}</ul></details>}</section>
    </>}
  </div>;
}
