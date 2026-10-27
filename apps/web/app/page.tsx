"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
type AnyRecord = Record<string, any>;
type Workspace = { id: number; name: string; role: string };
const tabs = ["Overview", "Brand", "Campaigns", "Content", "Approvals", "Accounts", "Publishing", "Team", "Audit"];

export default function Home() {
  const [token, setToken] = useState("");
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [user, setUser] = useState<AnyRecord | null>(null);
  const [tab, setTab] = useState("Overview");
  const [notice, setNotice] = useState("");
  const [data, setData] = useState<AnyRecord[]>([]);
  const [brand, setBrand] = useState<AnyRecord>({ name: "", value_proposition: "", audience: "", tone: "", preferred_vocabulary: [], prohibited_claims: [] });
  const [loading, setLoading] = useState(false);

  const request = useCallback(async (path: string, init: RequestInit = {}) => {
    const response = await fetch(`${API}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}), ...init.headers },
    });
    const result = response.status === 204 ? null : await response.json();
    if (!response.ok) throw new Error(result?.detail ?? "Request failed");
    return result;
  }, [token]);

  const loadWorkspace = useCallback(async () => {
    if (!workspace) return;
    setLoading(true);
    try {
      const routes: Record<string, string> = {
        Overview: `/api/v1/workspaces/${workspace.id}/content`,
        Campaigns: `/api/v1/workspaces/${workspace.id}/campaigns`,
        Content: `/api/v1/workspaces/${workspace.id}/content`,
        Approvals: `/api/v1/workspaces/${workspace.id}/approvals`,
        Accounts: `/api/v1/workspaces/${workspace.id}/social-accounts`,
        Publishing: `/api/v1/workspaces/${workspace.id}/publish-jobs`,
        Team: `/api/v1/workspaces/${workspace.id}/members`,
        Audit: `/api/v1/workspaces/${workspace.id}/audit-logs`,
      };
      if (tab === "Brand") setBrand(await request(`/api/v1/workspaces/${workspace.id}/brand`));
      setData(routes[tab] ? await request(routes[tab]) : []);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Could not load workspace");
    } finally {
      setLoading(false);
    }
  }, [request, tab, workspace]);

  useEffect(() => {
    const existing = window.localStorage.getItem("acm-token");
    if (existing) setToken(existing);
  }, []);

  useEffect(() => {
    if (!token) return;
    request("/api/v1/auth/me").then((result) => {
      setUser(result.user);
      setWorkspaces(result.workspaces);
      setWorkspace((previous) => result.workspaces.find((item: Workspace) => item.id === previous?.id) ?? result.workspaces[0] ?? null);
    }).catch(() => {
      window.localStorage.removeItem("acm-token");
      setToken("");
    });
  }, [request, token]);

  useEffect(() => { void loadWorkspace(); }, [loadWorkspace]);

  async function submit(event: FormEvent<HTMLFormElement>, path: string, method = "POST", payload?: AnyRecord) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = payload ?? Object.fromEntries(new FormData(form).entries());
    if (values.campaign_id === "") values.campaign_id = null;
    try {
      await request(path, { method, body: JSON.stringify(values) });
      setNotice("Saved successfully.");
      await loadWorkspace();
      form.reset();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Request failed");
    }
  }

  async function authenticate(event: FormEvent<HTMLFormElement>, mode: "login" | "register") {
    event.preventDefault();
    const payload = Object.fromEntries(new FormData(event.currentTarget).entries());
    try {
      const result = await request(`/api/v1/auth/${mode === "login" ? "login" : "register"}`, {
        method: "POST", body: JSON.stringify(payload),
      });
      window.localStorage.setItem("acm-token", result.access_token);
      setToken(result.access_token);
      setUser(result.user);
      const workspaces = result.workspaces ?? [result.workspace];
      setWorkspaces(workspaces);
      setWorkspace(workspaces[0]);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Authentication failed");
    }
  }

  async function refresh() {
    await loadWorkspace();
    setNotice("");
  }

  if (!token || !workspace) {
    return <main className="auth-page">
      <section className="auth-card">
        <div className="brand-mark">AC</div>
        <p className="eyebrow">YOUR CONTENT WORKSPACE</p>
        <h1>Make good content.<br /><span>Keep humans in control.</span></h1>
        <p className="muted">Plan campaigns, review drafts, and publish only after an authorized person approves.</p>
        <AuthForms onSubmit={authenticate} />
        {notice && <p className="notice" role="status">{notice}</p>}
      </section>
    </main>;
  }

  const base = `/api/v1/workspaces/${workspace.id}`;
  const contentForm = <form className="panel form-grid" onSubmit={(event) => submit(event, `${base}/content`)}>
    <h2>Create a content draft</h2>
    <label>Title<input name="title" required maxLength={240} /></label>
    <label>Platform<select name="platform"><option value="linkedin">LinkedIn</option><option value="instagram">Instagram</option><option value="facebook">Facebook</option><option value="youtube">YouTube</option><option value="x">X</option><option value="tiktok">TikTok</option></select></label>
    <label className="wide">Content<textarea name="body" required maxLength={3000} rows={5} /></label>
    <label>Campaign ID (optional)<input name="campaign_id" type="number" /></label>
    <button className="primary">Save draft</button>
    <p className="helper wide">Drafts are immutable versions. Editing creates a new version and invalidates earlier approvals.</p>
  </form>;

  return <main className="app-shell">
    <aside className="sidebar">
      <a className="logo" href="#"><span className="brand-mark">AC</span><span>Content Manager<small>Workspace</small></span></a>
      <div className="workspace-label">WORKSPACE</div>
      <div className="workspace-name">{workspace.name}<small>{workspace.role}</small></div>
      <nav aria-label="Main navigation">{tabs.map((name) => <button key={name} className={tab === name ? "nav-item selected" : "nav-item"} onClick={() => setTab(name)}>{name}</button>)}</nav>
      <div className="sidebar-foot">Mock integrations enabled<br />No external posts are sent.</div>
    </aside>
    <section className="main-area">
      <header className="topbar"><div><span className="muted">Workspace / </span>{tab}</div><div className="user-chip">{workspaces.length > 1 && <select aria-label="Switch workspace" value={workspace.id} onChange={(event) => setWorkspace(workspaces.find((item) => item.id === Number(event.target.value)) ?? null)}>{workspaces.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select>}{user?.name}<button onClick={async () => { await request("/api/v1/auth/session", { method: "DELETE" }).catch(() => null); window.localStorage.removeItem("acm-token"); setToken(""); setWorkspace(null); setWorkspaces([]); }}>Sign out</button></div></header>
      <div className="page-content">
        <div className="page-heading"><div><p className="eyebrow">CONTENT OPERATIONS</p><h1>{tab}</h1><p className="muted">Prepare, review, and publish content with a human approval gate.</p></div><button className="secondary" onClick={() => void refresh()}>Refresh</button></div>
        {notice && <div className="notice" role="status">{notice}<button onClick={() => setNotice("")}>Dismiss</button></div>}
        {loading && <p className="helper">Loading…</p>}
        {tab === "Overview" && <Overview data={data} setTab={setTab} />}
        {tab === "Brand" && <form className="panel form-grid" onSubmit={(event) => submit(event, `${base}/brand`, "PUT", { ...brand, preferred_vocabulary: stringList(brand.preferred_vocabulary), prohibited_claims: stringList(brand.prohibited_claims) })}>
          <h2>Brand profile</h2><label>Brand name<input value={brand.name} onChange={(e) => setBrand({ ...brand, name: e.target.value })} required /></label>
          <label>Audience<input value={brand.audience} onChange={(e) => setBrand({ ...brand, audience: e.target.value })} /></label>
          <label className="wide">Value proposition<textarea value={brand.value_proposition} onChange={(e) => setBrand({ ...brand, value_proposition: e.target.value })} rows={3} /></label>
          <label className="wide">Tone and voice<textarea value={brand.tone} onChange={(e) => setBrand({ ...brand, tone: e.target.value })} rows={3} /></label>
          <label>Preferred vocabulary (comma separated)<input value={stringList(brand.preferred_vocabulary)} onChange={(e) => setBrand({ ...brand, preferred_vocabulary: e.target.value })} /></label>
          <label>Prohibited claims (comma separated)<input value={stringList(brand.prohibited_claims)} onChange={(e) => setBrand({ ...brand, prohibited_claims: e.target.value })} /></label>
          <button className="primary">Save brand</button>
        </form>}
        {tab === "Campaigns" && <><form className="panel form-grid" onSubmit={(event) => submit(event, `${base}/campaigns`)}><h2>New campaign</h2><label>Name<input name="name" required /></label><label>Objective<input name="objective" /></label><label className="wide">Audience<textarea name="audience" rows={2} /></label><button className="primary">Create campaign</button></form><RecordList rows={data} kind="campaign" /></>}
        {tab === "Content" && <>{contentForm}<RecordList rows={data} kind="content" onAction={async (row, action) => {
          try {
            if (action === "revise") {
              const body = window.prompt("Edit the draft copy. Saving creates a new immutable version.", row.version?.body ?? "");
              if (body === null) return;
              await request(`${base}/content/${row.id}/versions`, {
                method: "POST",
                body: JSON.stringify({
                  title: row.title,
                  platform: row.platform,
                  body,
                  campaign_id: row.campaign_id,
                  media_manifest: row.version?.media_manifest ?? [],
                }),
              });
              setNotice("New version saved. Any earlier approval has been invalidated.");
            } else if (action === "quality") {
              const result = await request(`${base}/content/${row.id}/quality-check`, { method: "POST" });
              setNotice(result.passed ? "Quality checks passed with a review reminder." : `Review blocked: ${result.findings.filter((f: AnyRecord) => f.severity === "blocking").map((f: AnyRecord) => f.message).join(" ")}`);
            } else {
              const accounts = await request(`${base}/social-accounts`);
              const account = accounts.find((a: AnyRecord) => a.platform === row.platform && a.active);
              if (!account) throw new Error("Create an active mock account for this platform first.");
              await request(`${base}/content/${row.id}/submit-for-approval`, { method: "POST", body: JSON.stringify({ social_account_id: account.id, destination: account.external_account_id, action: "publish" }) });
              setNotice("Submitted for human approval.");
            }
            await loadWorkspace();
          } catch (error) { setNotice(error instanceof Error ? error.message : "Action failed"); }
        }} /></>}
        {tab === "Approvals" && <RecordList rows={data} kind="approval" onAction={async (row, action) => {
          try {
            const result = await request(`${base}/approvals/${row.id}/decision`, { method: "POST", body: JSON.stringify({ decision: action, comment: "" }) });
            if (action === "approved") {
              await request(`${base}/publish-jobs`, {
                method: "POST",
                body: JSON.stringify({ approval_id: row.id, idempotency_key: `approval-${row.id}` }),
              });
              setNotice("Approved and queued for mock publishing.");
            } else {
              setNotice(`Approval ${result.status}.`);
            }
            await loadWorkspace();
          } catch (error) { setNotice(error instanceof Error ? error.message : "Decision failed"); }
        }} />}
        {tab === "Accounts" && <><form className="panel form-grid" onSubmit={(event) => submit(event, `${base}/social-accounts`)}><h2>Add local mock account</h2><label>Platform<select name="platform"><option value="linkedin">LinkedIn</option><option value="instagram">Instagram</option><option value="facebook">Facebook</option><option value="youtube">YouTube</option><option value="x">X</option><option value="tiktok">TikTok</option></select></label><label>Account name<input name="display_name" required /></label><label>Destination ID<input name="external_account_id" required /></label><input type="hidden" name="is_mock" value="true" /><button className="primary">Add mock account</button></form><RecordList rows={data} kind="account" /></>}
        {tab === "Publishing" && <RecordList rows={data} kind="job" onAction={async (row) => {
          try { await request(`${base}/publish-jobs/${row.id}/dispatch`, { method: "POST" }); setNotice("Mock provider confirmed the post."); await loadWorkspace(); }
          catch (error) { setNotice(error instanceof Error ? error.message : "Dispatch failed"); }
        }} />}
        {tab === "Team" && <><form className="panel form-grid" onSubmit={(event) => submit(event, `${base}/members`)}><h2>Add registered workspace member</h2><label>Email<input name="email" type="email" required /></label><label>Role<select name="role"><option>approver</option><option>creator</option><option>strategist</option><option>admin</option><option>analyst</option><option>viewer</option></select></label><p className="helper wide">The person must register first. Authors cannot approve their own content.</p><button className="primary">Add member</button></form><RecordList rows={data} kind="member" /></>}
        {tab === "Audit" && <RecordList rows={data} kind="audit" />}
      </div>
    </section>
  </main>;
}

function stringList(value: unknown): string {
  return Array.isArray(value) ? value.join(", ") : String(value ?? "");
}

function AuthForms({ onSubmit }: { onSubmit: (event: FormEvent<HTMLFormElement>, mode: "login" | "register") => void }) {
  const [mode, setMode] = useState<"login" | "register">("register");
  return <form className="auth-form" onSubmit={(event) => onSubmit(event, mode)}>
    {mode === "register" && <><label>Your name<input name="name" required autoComplete="name" /></label><label>Workspace name<input name="organization_name" required /></label></>}
    <label>Email<input name="email" type="email" required autoComplete="email" /></label>
    <label>Password<input name="password" type="password" minLength={mode === "register" ? 12 : 1} required autoComplete={mode === "register" ? "new-password" : "current-password"} /></label>
    <button className="primary">{mode === "register" ? "Create workspace" : "Sign in"}</button>
    <button className="link-button" type="button" onClick={() => setMode(mode === "register" ? "login" : "register")}>{mode === "register" ? "Already registered? Sign in" : "New here? Create a workspace"}</button>
  </form>;
}

function Overview({ data, setTab }: { data: AnyRecord[]; setTab: (tab: string) => void }) {
  return <><div className="metric-grid"><Metric label="Content items" value={data.length} /><Metric label="Needs review" value="Approvals" onClick={() => setTab("Approvals")} /><Metric label="Mock publishing" value="Enabled" /><Metric label="Human approval" value="Required" /></div>
    <div className="panel"><div className="panel-heading"><h2>Recent content</h2><button className="link-button" onClick={() => setTab("Content")}>Open content →</button></div><RecordList rows={data.slice(0, 5)} kind="content" emptyText="Create your first draft to start the review and publishing workflow." /></div>
    <div className="callout"><strong>Publishing is protected.</strong> The API revalidates the approved immutable version, account, destination, and action before dispatch and again before calling the mock provider.</div>
  </>;
}

function Metric({ label, value, onClick }: { label: string; value: string | number; onClick?: () => void }) {
  return <button className="metric panel" onClick={onClick}><span>{label}</span><strong>{value}</strong></button>;
}

function RecordList({ rows, kind, onAction, emptyText }: { rows: AnyRecord[]; kind: string; onAction?: (row: AnyRecord, action?: string) => void; emptyText?: string }) {
  if (!rows.length) return <div className="empty panel"><span className="empty-icon">○</span><strong>Nothing here yet</strong><p>{emptyText ?? `New ${kind} records will appear here.`}</p></div>;
  return <div className="record-list">{rows.map((row) => {
    const version = row.version;
    const label = row.content_title ?? row.title ?? row.name ?? row.display_name ?? `${kind} #${row.id}`;
    const contentSummary = row.content_body ?? version?.body ?? row.objective ?? row.email ?? row.action ?? row.destination ?? row.error_message ?? row.external_account_id ?? row.comment ?? row.resource_type ?? "";
    return <article className="record panel" key={`${kind}-${row.id ?? row.user_id}`}>
      <div className="record-main"><strong>{label}</strong><p>{contentSummary}</p>
        <small>{row.platform ?? row.role ?? row.status ?? row.action ?? ""}{row.account_name ? ` · ${row.account_name}` : ""}{row.destination ? ` · Destination: ${row.destination}` : ""}{row.version_number ? ` · Version ${row.version_number}` : ""}{version?.number ? ` · Version ${version.number}` : ""}{row.is_mock ? " · MOCK" : ""}</small>
        {kind === "approval" && row.quality_findings?.length > 0 && <p className="helper">{row.quality_findings.map((finding: AnyRecord) => finding.message).join(" ")}</p>}
      </div>
      {kind === "content" && onAction && <div className="record-actions"><button className="secondary" onClick={() => onAction(row, "revise")}>New version</button><button className="secondary" onClick={() => onAction(row, "quality")}>Quality check</button><button className="primary" onClick={() => onAction(row, "submit")}>Request approval</button></div>}
      {kind === "approval" && row.status === "pending" && onAction && <div className="record-actions"><button className="primary" onClick={() => onAction(row, "approved")}>Approve</button><button className="secondary" onClick={() => onAction(row, "changes_requested")}>Request changes</button><button className="danger" onClick={() => onAction(row, "rejected")}>Reject</button></div>}
      {kind === "job" && row.status === "queued" && onAction && <button className="primary" onClick={() => onAction(row)}>Dispatch mock post</button>}
      {row.status && <span className={`status ${row.status}`}>{row.status.replaceAll("_", " ")}</span>}
    </article>;
  })}</div>;
}
