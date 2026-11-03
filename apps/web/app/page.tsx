"use client";

import { FormEvent, KeyboardEvent as ReactKeyboardEvent, useCallback, useEffect, useRef, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
type AnyRecord = Record<string, any>;
type Workspace = { id: number; name: string; role: string };
type NoticeTone = "error" | "info" | "success";
type MessageDialogOptions = {
  kind: "confirm" | "text" | "textarea" | "select";
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "primary" | "danger";
  initialValue?: string;
  placeholder?: string;
  required?: boolean;
  minLength?: number;
  maxLength?: number;
  options?: { label: string; value: string }[];
};
type MessageDialogState = MessageDialogOptions & {
  value: string;
  error: string;
  resolve: (value: string | null) => void;
};
const tabs = ["Overview", "Brand", "Campaigns", "Research", "AI Studio", "Content", "Approvals", "Accounts", "Publishing", "Analytics", "Community", "Agent Activity", "Usage", "Team", "Audit"];

export default function Home() {
  const [token, setToken] = useState("");
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [user, setUser] = useState<AnyRecord | null>(null);
  const [tab, setTab] = useState("Overview");
  const [notice, setNoticeState] = useState<{ message: string; tone: NoticeTone } | null>(null);
  const [data, setData] = useState<AnyRecord[]>([]);
  const [brand, setBrand] = useState<AnyRecord>({ name: "", value_proposition: "", audience: "", tone: "", preferred_vocabulary: [], prohibited_claims: [] });
  const [usage, setUsage] = useState<AnyRecord | null>(null);
  const [aiResult, setAiResult] = useState<AnyRecord | null>(null);
  const [preview, setPreview] = useState<AnyRecord | null>(null);
  const [analyticsMeta, setAnalyticsMeta] = useState<AnyRecord | null>(null);
  const [analyticsQuery, setAnalyticsQuery] = useState("");
  const [experiments, setExperiments] = useState<AnyRecord[]>([]);
  const [accounts, setAccounts] = useState<AnyRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [approvalRequest, setApprovalRequest] = useState<{
    content: AnyRecord;
    accounts: AnyRecord[];
    selectedAccountId: number | null;
    confirmedMock: boolean;
    submitting: boolean;
    error: string;
  } | null>(null);
  const [messageDialog, setMessageDialog] = useState<MessageDialogState | null>(null);
  const previousDialogFocus = useRef<HTMLElement | null>(null);

  function setNotice(message: string, tone: NoticeTone = "info") {
    setNoticeState(message ? { message, tone } : null);
  }

  function openMessageDialog(options: MessageDialogOptions): Promise<string | null> {
    rememberDialogFocus();
    return new Promise((resolve) => setMessageDialog({
      ...options,
      value: options.initialValue ?? options.options?.[0]?.value ?? "",
      error: "",
      resolve,
    }));
  }

  function rememberDialogFocus() {
    if (typeof document !== "undefined" && document.activeElement instanceof HTMLElement) {
      previousDialogFocus.current = document.activeElement;
    }
  }

  function handleDialogKeyDown(event: ReactKeyboardEvent<HTMLDivElement>, canClose = true) {
    if (event.key === "Escape" && canClose) {
      finishMessageDialog(null);
      setApprovalRequest((current) => current && !current.submitting ? null : current);
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = event.currentTarget.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
    );
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function finishMessageDialog(value: string | null) {
    if (!messageDialog) return;
    if (value !== null && messageDialog.kind !== "confirm") {
      const trimmed = value.trim();
      if (messageDialog.required && !trimmed) {
        setMessageDialog({ ...messageDialog, error: "Enter a value to continue." });
        return;
      }
      if (messageDialog.minLength && trimmed.length < messageDialog.minLength) {
        setMessageDialog({ ...messageDialog, error: `Enter at least ${messageDialog.minLength} characters.` });
        return;
      }
    }
    messageDialog.resolve(value);
    setMessageDialog(null);
  }

  function handleInvalidFormField(event: FormEvent<HTMLElement>) {
    const field = event.target;
    if (!(field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement || field instanceof HTMLSelectElement)) return;
    event.preventDefault();
    const invalidField = field.form?.querySelector<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(":invalid") ?? field;
    invalidField.focus();
    const label = invalidField.labels?.[0]?.textContent?.trim() || invalidField.getAttribute("aria-label") || "This field";
    const detail = invalidField.validity.valueMissing
      ? "is required"
      : invalidField.validity.tooShort
        ? `must contain at least ${"minLength" in invalidField ? invalidField.minLength : 0} characters`
        : invalidField.validity.tooLong
          ? `must contain no more than ${"maxLength" in invalidField ? invalidField.maxLength : 0} characters`
          : invalidField.validity.typeMismatch
            ? `must be a valid ${invalidField.type}`
            : invalidField.validity.rangeUnderflow || invalidField.validity.rangeOverflow
              ? "is outside the allowed range"
              : "needs attention";
    setNotice(`${label} ${detail}. Review the field and try again.`, "error");
  }

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
        Community: `/api/v1/workspaces/${workspace.id}/community`,
        Research: `/api/v1/workspaces/${workspace.id}/research`,
        "Agent Activity": `/api/v1/workspaces/${workspace.id}/agent-runs`,
        Team: `/api/v1/workspaces/${workspace.id}/members`,
        Audit: `/api/v1/workspaces/${workspace.id}/audit-logs`,
      };
      if (tab === "Brand") setBrand(await request(`/api/v1/workspaces/${workspace.id}/brand`));
      if (tab === "Usage") {
        setUsage(await request(`/api/v1/workspaces/${workspace.id}/usage`));
        setData([]);
      } else if (tab === "Analytics") {
        const result = await request(`/api/v1/workspaces/${workspace.id}/analytics${analyticsQuery ? `?${analyticsQuery}` : ""}`);
        setAnalyticsMeta(result);
        setData(result.snapshots);
        setExperiments(await request(`/api/v1/workspaces/${workspace.id}/experiments`));
        setAccounts(await request(`/api/v1/workspaces/${workspace.id}/social-accounts`));
      } else if (tab === "Community") {
        setAnalyticsMeta(null);
        setData(await request(routes[tab]));
        setAccounts(await request(`/api/v1/workspaces/${workspace.id}/social-accounts`));
      } else {
        setAnalyticsMeta(null);
        setUsage(null);
        setData(routes[tab] ? await request(routes[tab]) : []);
      }
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Could not load workspace", "error");
    } finally {
      setLoading(false);
    }
  }, [analyticsQuery, request, tab, workspace]);

  useEffect(() => {
    const existing = window.localStorage.getItem("acm-token");
    if (existing) setToken(existing);
  }, []);

  useEffect(() => {
    const connectionStatus = new URLSearchParams(window.location.search).get("linkedin");
    if (connectionStatus) {
      setNotice(
        connectionStatus === "connected" ? "LinkedIn account connected. Review its granted capabilities."
          : connectionStatus === "cancelled" ? "LinkedIn sign-in was cancelled. Start the connection again if you want to connect."
            : connectionStatus === "permission_denied"
              ? "LinkedIn authorization was declined or a requested permission is unavailable. Check the app's approved Products/scopes in the LinkedIn Developer Portal, then try again."
              : "LinkedIn did not complete authorization. Check that the exact callback URL is registered and the requested app permissions are approved, then retry. No account was connected.",
        connectionStatus === "connected" ? "success" : connectionStatus === "cancelled" ? "info" : "error",
      );
      window.history.replaceState({}, "", window.location.pathname);
    }
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
  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNoticeState(null), notice.tone === "error" ? 10000 : 7000);
    return () => window.clearTimeout(timer);
  }, [notice]);
  useEffect(() => {
    if (messageDialog || approvalRequest || !previousDialogFocus.current) return;
    previousDialogFocus.current.focus();
    previousDialogFocus.current = null;
  }, [approvalRequest, messageDialog]);

  async function submit(event: FormEvent<HTMLFormElement>, path: string, method = "POST", payload?: AnyRecord) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = payload ?? Object.fromEntries(new FormData(form).entries());
    if (values.campaign_id === "") values.campaign_id = null;
    try {
      await request(path, { method, body: JSON.stringify(values) });
      setNotice("Saved successfully.", "success");
      await loadWorkspace();
      form.reset();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Request failed", "error");
    }
  }

  async function runWorkflow(path: string, payload: AnyRecord) {
    try {
      const result = await request(path, { method: "POST", body: JSON.stringify(payload) });
      setAiResult(result);
      setNotice("Workflow completed. Review the structured output before using it.", "success");
      await loadWorkspace();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "AI workflow failed", "error");
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
      setNotice(error instanceof Error ? error.message : "Authentication failed", "error");
    }
  }

  async function refresh() {
    await loadWorkspace();
    setNotice("");
  }

  async function openApprovalRequest(content: AnyRecord) {
    rememberDialogFocus();
    try {
      const workspaceAccounts = await request(`${base}/social-accounts`);
      const platformAccounts = workspaceAccounts.filter((account: AnyRecord) => account.platform === content.platform);
      setApprovalRequest({
        content,
        accounts: platformAccounts,
        selectedAccountId: null,
        confirmedMock: false,
        submitting: false,
        error: "",
      });
    } catch (error) {
      previousDialogFocus.current = null;
      setNotice(error instanceof Error ? error.message : "Could not load accounts for approval", "error");
    }
  }

  async function submitApprovalRequest() {
    if (!approvalRequest) return;
    const account = approvalRequest.accounts.find((item) => item.id === approvalRequest.selectedAccountId);
    const publishCapability = account?.capabilities?.find((item: AnyRecord) => item.capability === "publish");
    if (!account || !account.active || publishCapability?.enabled !== true || publishCapability.status !== "available") {
      setApprovalRequest((current) => current ? { ...current, error: "Select an active account with publishing enabled." } : current);
      return;
    }
    const hasLiveAccount = approvalRequest.accounts.some((item) =>
      item.is_mock === false && item.active && item.capabilities?.some(
        (capability: AnyRecord) => capability.capability === "publish" && capability.enabled === true && capability.status === "available",
      ),
    );
    if (account.is_mock && hasLiveAccount && !approvalRequest.confirmedMock) {
      setApprovalRequest((current) => current ? { ...current, error: "Confirm that you want to use this mock destination instead of the available live account." } : current);
      return;
    }
    setApprovalRequest((current) => current ? { ...current, submitting: true, error: "" } : current);
    try {
      await request(`${base}/content/${approvalRequest.content.id}/submit-for-approval`, {
        method: "POST",
        body: JSON.stringify({
          social_account_id: account.id,
          destination: account.external_account_id,
          action: "publish",
        }),
      });
      setApprovalRequest(null);
      setNotice(account.is_mock
        ? `Submitted for approval to mock account "${account.display_name}". This will not publish externally.`
        : `Submitted for approval to live LinkedIn account "${account.display_name}". A separate authorized approver must approve before the worker can publish.`, "success");
      await loadWorkspace();
    } catch (error) {
      setApprovalRequest((current) => current ? {
        ...current,
        submitting: false,
        error: error instanceof Error ? error.message : "Could not submit for approval",
      } : current);
    }
  }

  if (!token || !workspace) {
    return <main className="auth-page" onInvalidCapture={handleInvalidFormField}>
      <section className="auth-card">
        <div className="brand-mark">AC</div>
        <p className="eyebrow">YOUR CONTENT WORKSPACE</p>
        <h1>Make good content.<br /><span>Keep humans in control.</span></h1>
        <p className="muted">Plan campaigns, review drafts, and publish only after an authorized person approves.</p>
        <AuthForms onSubmit={authenticate} />
        {notice && <p className={`auth-notice ${notice.tone}`} role={notice.tone === "error" ? "alert" : "status"}>{notice.message}</p>}
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
      <div className="sidebar-foot">LinkedIn publishing may be live<br />External posts require server-side human approval.</div>
    </aside>
    <section className="main-area" onInvalidCapture={handleInvalidFormField}>
      <header className="topbar"><div><span className="muted">Workspace / </span>{tab}</div><div className="user-chip">{workspaces.length > 1 && <select aria-label="Switch workspace" value={workspace.id} onChange={(event) => setWorkspace(workspaces.find((item) => item.id === Number(event.target.value)) ?? null)}>{workspaces.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select>}{user?.name}<button onClick={async () => { await request("/api/v1/auth/session", { method: "DELETE" }).catch(() => null); window.localStorage.removeItem("acm-token"); setToken(""); setWorkspace(null); setWorkspaces([]); }}>Sign out</button></div></header>
      <div className="page-content">
        <div className="page-heading"><div><p className="eyebrow">CONTENT OPERATIONS</p><h1>{tab}</h1><p className="muted">Prepare, review, and publish content with a human approval gate.</p></div><button className="secondary" onClick={() => void refresh()}>Refresh</button></div>
        {notice && <div className={`toast ${notice.tone}`} role={notice.tone === "error" ? "alert" : "status"} aria-live={notice.tone === "error" ? "assertive" : "polite"}>
          <span className="toast-indicator" aria-hidden="true">{notice.tone === "error" ? "!" : notice.tone === "success" ? "✓" : "i"}</span>
          <p>{notice.message}</p>
          <button aria-label="Dismiss notification" onClick={() => setNotice("")}>Dismiss</button>
        </div>}
        {messageDialog && <div
          className="modal-backdrop"
          onClick={(event) => {
            if (event.target === event.currentTarget) finishMessageDialog(null);
          }}
          onKeyDown={(event) => {
            handleDialogKeyDown(event);
          }}
        >
          <form
            className="message-dialog panel"
            role="dialog"
            aria-modal="true"
            aria-labelledby="message-dialog-title"
            aria-describedby="message-dialog-description"
            onSubmit={(event) => {
              event.preventDefault();
              finishMessageDialog(messageDialog.kind === "confirm" ? "confirmed" : messageDialog.value);
            }}
          >
            <div className="panel-heading">
              <h2 id="message-dialog-title">{messageDialog.title}</h2>
            </div>
            <p id="message-dialog-description" className="dialog-message">{messageDialog.message}</p>
            {messageDialog.kind === "text" && <input
              autoFocus
              aria-label={messageDialog.title}
              aria-invalid={Boolean(messageDialog.error)}
              aria-describedby={messageDialog.error ? "message-dialog-error" : "message-dialog-description"}
              placeholder={messageDialog.placeholder}
              value={messageDialog.value}
              maxLength={messageDialog.maxLength}
              onChange={(event) => setMessageDialog({ ...messageDialog, value: event.target.value, error: "" })}
            />}
            {messageDialog.kind === "textarea" && <textarea
              autoFocus
              aria-label={messageDialog.title}
              aria-invalid={Boolean(messageDialog.error)}
              aria-describedby={messageDialog.error ? "message-dialog-error" : "message-dialog-description"}
              placeholder={messageDialog.placeholder}
              value={messageDialog.value}
              maxLength={messageDialog.maxLength}
              rows={7}
              onChange={(event) => setMessageDialog({ ...messageDialog, value: event.target.value, error: "" })}
            />}
            {messageDialog.kind === "select" && <label className="dialog-select-label">
              <span>Verified outcome</span>
              <select autoFocus aria-invalid={Boolean(messageDialog.error)} aria-describedby={messageDialog.error ? "message-dialog-error" : "message-dialog-description"} value={messageDialog.value} onChange={(event) => setMessageDialog({ ...messageDialog, value: event.target.value, error: "" })}>
                {messageDialog.options?.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </label>}
            {messageDialog.error && <p id="message-dialog-error" className="dialog-error" role="alert">{messageDialog.error}</p>}
            <div className="dialog-actions">
              <button className="secondary" type="button" autoFocus={messageDialog.kind === "confirm"} onClick={() => finishMessageDialog(null)}>{messageDialog.cancelLabel ?? "Cancel"}</button>
              <button className={messageDialog.tone === "danger" ? "danger" : "primary"} type="submit">{messageDialog.confirmLabel ?? "Continue"}</button>
            </div>
          </form>
        </div>}
        {approvalRequest && <div
          className="modal-backdrop"
          onClick={(event) => {
            if (event.target === event.currentTarget && !approvalRequest.submitting) setApprovalRequest(null);
          }}
          onKeyDown={(event) => {
            handleDialogKeyDown(event, !approvalRequest.submitting);
          }}
        >
          <section className="approval-dialog panel" role="dialog" aria-modal="true" aria-labelledby="approval-dialog-title" aria-describedby="approval-dialog-description">
            <div className="panel-heading"><div><p className="eyebrow">CONTENT APPROVAL</p><h2 id="approval-dialog-title">Choose a destination</h2></div></div>
            <p id="approval-dialog-description" className="muted">Select the account for <strong>{approvalRequest.content.title}</strong> in <strong>{workspace.name}</strong>. Approval will be tied to this exact account and destination.</p>
            <div className="approval-account-list">
              {approvalRequest.accounts.map((account) => {
                const capability = account.capabilities?.find((item: AnyRecord) => item.capability === "publish");
                const ready = account.active && capability?.enabled === true && capability.status === "available";
                const firstReadyAccountId = approvalRequest.accounts.find((candidate) =>
                  candidate.active && candidate.capabilities?.some((item: AnyRecord) =>
                    item.capability === "publish" && item.enabled === true && item.status === "available",
                  ),
                )?.id;
                const label = !account.active ? "INACTIVE" : account.is_mock ? "MOCK" : "LIVE";
                const reason = !account.active
                  ? "This account is inactive or disconnected."
                  : ready
                    ? account.is_mock ? "Mock destination; no external post will be sent." : "Publishing is enabled for this connected account."
                    : capability?.reason || "Publishing is currently unavailable for this account.";
                return <label key={account.id} className={`approval-account${ready ? "" : " unavailable"}${approvalRequest.selectedAccountId === account.id ? " selected" : ""}`}>
                  <input
                    type="radio"
                    name="approval-destination"
                    value={account.id}
                    autoFocus={account.id === firstReadyAccountId}
                    checked={approvalRequest.selectedAccountId === account.id}
                    disabled={!ready || approvalRequest.submitting}
                    onChange={() => setApprovalRequest((current) => current ? {
                      ...current,
                      selectedAccountId: account.id,
                      confirmedMock: false,
                      error: "",
                    } : current)}
                  />
                  <span className="approval-account-details">
                    <span className="approval-account-heading"><strong>{account.display_name}</strong><span className={`account-badge ${account.is_mock ? "mock" : "live"}`}>{label}{!ready && account.active ? " · UNAVAILABLE" : ""}</span></span>
                    <code>{account.external_account_id}</code>
                    <span className="helper">{reason}</span>
                  </span>
                </label>;
              })}
            </div>
            {!approvalRequest.accounts.length && <div className="callout">
              No {approvalRequest.content.platform} accounts are connected to this workspace. Connect an account from Accounts, then return to this content item.
            </div>}
            {approvalRequest.accounts.length > 0 && !approvalRequest.accounts.some((account) =>
              account.is_mock === false && account.active && account.capabilities?.some(
                (capability: AnyRecord) => capability.capability === "publish" && capability.enabled === true && capability.status === "available",
              ),
            ) && <div className="callout">
              No live publishing account is available in <strong>{workspace.name}</strong>. If you connected LinkedIn in another workspace, switch to that workspace and use its content item; accounts are workspace-specific.
            </div>}
            {approvalRequest.selectedAccountId !== null && (() => {
              const selected = approvalRequest.accounts.find((account) => account.id === approvalRequest.selectedAccountId);
              const hasReadyLive = approvalRequest.accounts.some((account) =>
                account.is_mock === false && account.active && account.capabilities?.some(
                  (capability: AnyRecord) => capability.capability === "publish" && capability.enabled === true && capability.status === "available",
                ),
              );
              if (!selected) return null;
              if (selected.is_mock && hasReadyLive) return <label className="confirmation-check">
                <input
                  type="checkbox"
                  checked={approvalRequest.confirmedMock}
                  disabled={approvalRequest.submitting}
                  onChange={(event) => setApprovalRequest((current) => current ? { ...current, confirmedMock: event.target.checked } : current)}
                />
                <span>I understand this will use a mock destination and will not publish externally.</span>
              </label>;
              if (!selected.is_mock) return <div className="callout live-warning">
                If a separate authorized approver approves this request, the publishing worker may send the post to this live LinkedIn account.
              </div>;
              return null;
            })()}
            {approvalRequest.error && <p className="dialog-error" role="alert">{approvalRequest.error}</p>}
            <div className="dialog-actions">
              <button className="secondary" autoFocus={!approvalRequest.accounts.some((account) => account.active && account.capabilities?.some((capability: AnyRecord) => capability.capability === "publish" && capability.enabled === true && capability.status === "available"))} disabled={approvalRequest.submitting} onClick={() => setApprovalRequest(null)}>Cancel</button>
              {approvalRequest.accounts.length === 0 && <button className="secondary" onClick={() => { setApprovalRequest(null); setTab("Accounts"); }}>Open Accounts</button>}
              <button
                className="primary"
                disabled={approvalRequest.submitting || !approvalRequest.accounts.some((account) => account.id === approvalRequest.selectedAccountId && account.active && account.capabilities?.some((capability: AnyRecord) => capability.capability === "publish" && capability.enabled === true && capability.status === "available")) || (() => {
                  const selected = approvalRequest.accounts.find((account) => account.id === approvalRequest.selectedAccountId);
                  const hasReadyLive = approvalRequest.accounts.some((account) => account.is_mock === false && account.active && account.capabilities?.some((capability: AnyRecord) => capability.capability === "publish" && capability.enabled === true && capability.status === "available"));
                  return Boolean(selected?.is_mock && hasReadyLive && !approvalRequest.confirmedMock);
                })()}
                onClick={() => void submitApprovalRequest()}
              >{approvalRequest.submitting ? "Submitting…" : "Submit for approval"}</button>
            </div>
          </section>
        </div>}
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
        {tab === "Research" && <><form className="panel form-grid" onSubmit={async (event) => {
          event.preventDefault();
          const form = event.currentTarget;
          const values = Object.fromEntries(new FormData(form).entries());
          const excerpt = String(values.excerpt ?? "").trim();
          const sources = excerpt ? [{
            title: values.source_title,
            url: values.source_url,
            source_type: values.source_type,
            excerpt,
          }] : [];
          await submit(event, `${base}/research`, "POST", {
            question: values.question,
            campaign_id: values.campaign_id ? Number(values.campaign_id) : null,
            sources,
          });
        }}>
          <h2>Source-linked research</h2>
          <label>Research question<input name="question" required minLength={3} /></label>
          <label>Campaign ID (optional)<input name="campaign_id" type="number" /></label>
          <label>Source title (optional)<input name="source_title" /></label>
          <label>Source URL<input name="source_url" type="url" /></label>
          <label>Source type<select name="source_type"><option value="web">Web</option><option value="document">Document</option><option value="interview">Interview</option><option value="internal">Internal</option></select></label>
          <label className="wide">Excerpt<textarea name="excerpt" rows={4} maxLength={6000} /></label>
          <button className="primary">Analyze supplied sources</button>
          <p className="helper wide">Mock mode does not fetch URLs. Add excerpts you have permission to use; source text is treated as untrusted data and never as instructions.</p>
        </form><RecordList rows={data} kind="research" /></>}
        {tab === "AI Studio" && <div className="ai-studio">
          <div className="callout"><strong>AI provider:</strong> Mock by default. Configure a trusted OpenAI-compatible provider on the server to send workflow inputs to that provider. Generated content remains a draft and still requires quality checks and human approval.</div>
          <form className="panel form-grid" onSubmit={(event) => {
            event.preventDefault();
            const values = Object.fromEntries(new FormData(event.currentTarget).entries());
            void runWorkflow(`${base}/content/generate`, {
              platform: values.platform,
              brief: values.brief,
              campaign_id: values.campaign_id ? Number(values.campaign_id) : null,
              research_record_ids: parseIds(values.research_record_ids),
            });
          }}>
            <h2>Cross-platform copywriter</h2>
            <label>Platform<select name="platform"><option value="linkedin">LinkedIn</option><option value="instagram">Instagram</option><option value="facebook">Facebook</option><option value="youtube">YouTube</option><option value="x">X</option><option value="tiktok">TikTok</option></select></label>
            <label>Campaign ID (optional)<input name="campaign_id" type="number" /></label>
            <label className="wide">Brief<textarea name="brief" required minLength={3} maxLength={2000} rows={3} /></label>
            <label className="wide">Research record IDs (comma separated)<input name="research_record_ids" placeholder="1, 2" /></label>
            <button className="primary">Generate editable draft</button>
          </form>
          <form className="panel form-grid" onSubmit={(event) => {
            event.preventDefault();
            const values = Object.fromEntries(new FormData(event.currentTarget).entries());
            const path = `${base}/campaigns/${Number(values.campaign_id)}/strategy`;
            void runWorkflow(path, { research_record_ids: parseIds(values.research_record_ids) });
          }}>
            <h2>Content strategist</h2>
            <label>Campaign ID<input name="campaign_id" type="number" required /></label>
            <label>Research record IDs<input name="research_record_ids" placeholder="1, 2" /></label>
            <button className="primary">Create campaign strategy</button>
          </form>
          <form className="panel form-grid" onSubmit={(event) => {
            event.preventDefault();
            const values = Object.fromEntries(new FormData(event.currentTarget).entries());
            const path = `${base}/campaigns/${Number(values.campaign_id)}/creative-concepts`;
            void runWorkflow(path, {
              brief: values.brief,
              research_record_ids: parseIds(values.research_record_ids),
            });
          }}>
            <h2>Creative director</h2>
            <label>Campaign ID<input name="campaign_id" type="number" required /></label>
            <label>Research record IDs<input name="research_record_ids" placeholder="1, 2" /></label>
            <label className="wide">Creative brief<textarea name="brief" minLength={3} maxLength={2000} required rows={3} /></label>
            <button className="primary">Generate concepts</button>
          </form>
          {aiResult && <pre className="panel output-json">{JSON.stringify(aiResult, null, 2)}</pre>}
        </div>}
        {tab === "Content" && <>{contentForm}<RecordList rows={data} kind="content" onAction={async (row, action) => {
          try {
            if (action === "revise") {
              const body = await openMessageDialog({
                kind: "textarea",
                title: "Create a new content version",
                message: "Edit the draft copy. Saving creates an immutable version and invalidates earlier approvals.",
                initialValue: row.version?.body ?? "",
                required: true,
                maxLength: 3000,
                confirmLabel: "Save new version",
              });
              if (body === null) return;
              await request(`${base}/content/${row.id}/versions`, {
                method: "POST",
                body: JSON.stringify({
                  title: row.title,
                  platform: row.platform,
                  body,
                  campaign_id: row.campaign_id,
                  media_manifest: row.version?.media_manifest ?? [],
                  source_references: row.version?.source_references ?? [],
                }),
              });
              setNotice("New version saved. Any earlier approval has been invalidated.", "success");
            } else if (action === "preview") {
              setPreview(await request(`${base}/content/${row.id}/platform-preview`));
              setNotice("Preview is approximate and text-only; confirm platform capability before publishing.");
            } else if (action === "ai-review") {
              const result = await request(`${base}/content/${row.id}/ai-review`, { method: "POST" });
              setNotice(result.passed ? "Advisory AI review complete; verify its findings." : "AI review requires changes before approval.", result.passed ? "success" : "error");
            } else if (action === "quality") {
              const result = await request(`${base}/content/${row.id}/quality-check`, { method: "POST" });
              setNotice(result.passed ? "Quality checks passed with a review reminder." : `Review blocked: ${result.findings.filter((f: AnyRecord) => f.severity === "blocking").map((f: AnyRecord) => f.message).join(" ")}`, result.passed ? "success" : "error");
            } else {
              await openApprovalRequest(row);
              return;
            }
            await loadWorkspace();
          } catch (error) { setNotice(error instanceof Error ? error.message : "Action failed", "error"); }
        }} />{preview && <div className="panel preview-panel"><div className="panel-heading"><h2>Approximate {preview.platform} preview</h2><button className="link-button" onClick={() => setPreview(null)}>Close</button></div><p>{preview.preview_text}</p><small>{preview.character_count}/{preview.character_limit} characters · Not an official platform rendering</small><ul>{preview.warnings.map((warning: string) => <li key={warning}>{warning}</li>)}</ul></div>}</>}
        {tab === "Approvals" && <RecordList rows={data} kind="approval" onAction={async (row, action) => {
          try {
            if (action === "approved" && !row.account_is_mock) {
              const confirmed = await openMessageDialog({
                kind: "confirm",
                title: "Approve live LinkedIn publishing?",
                message: "Approving this exact content version and destination will queue it for live publishing. The worker may send it immediately after server-side validation.",
                confirmLabel: "Approve and queue",
                tone: "danger",
              });
              if (confirmed === null) return;
            }
            const result = await request(`${base}/approvals/${row.id}/decision`, { method: "POST", body: JSON.stringify({ decision: action, comment: "" }) });
            if (action === "approved") {
              await request(`${base}/publish-jobs`, {
                method: "POST",
                body: JSON.stringify({ approval_id: row.id, idempotency_key: `approval-${row.id}` }),
              });
              setNotice(row.account_is_mock
                ? "Approved and queued for mock publishing."
                : "Approved and queued for LinkedIn publishing; the worker may dispatch immediately.", "success");
            } else {
              setNotice(`Approval ${result.status}.`);
            }
            await loadWorkspace();
          } catch (error) { setNotice(error instanceof Error ? error.message : "Decision failed", "error"); }
        }} />}
        {tab === "Agent Activity" && <RecordList rows={data} kind="agent" emptyText="Completed and failed AI runs will appear here with provider, model, timing, outputs, and usage." />}
        {tab === "Usage" && <div className="metric-grid"><Metric label="AI runs with usage" value={usage?.event_count ?? 0} /><Metric label="Input tokens" value={usage?.input_tokens ?? 0} /><Metric label="Output tokens" value={usage?.output_tokens ?? 0} /><Metric label="Estimated token records" value={usage?.estimated_events ?? 0} />{usage && <div className="callout wide">{usage.cost_note}</div>}</div>}
        {tab === "Accounts" && <><div className="callout"><strong>LinkedIn integration:</strong> connect via official OAuth. Publishing is enabled only when LinkedIn grants w_member_social; only approved text-only member posts are supported. Other platforms remain mock-only.</div><div className="panel"><button className="primary" onClick={async () => {
          try {
            const result = await request(`${base}/integrations/linkedin/connect`, { method: "POST" });
            window.location.assign(result.authorization_url);
          } catch (error) { setNotice(error instanceof Error ? error.message : "Could not start LinkedIn connection", "error"); }
        }}>Connect LinkedIn</button></div><form className="panel form-grid" onSubmit={(event) => submit(event, `${base}/social-accounts`)}><h2>Add local mock account</h2><label>Platform<select name="platform"><option value="linkedin">LinkedIn</option><option value="instagram">Instagram</option><option value="facebook">Facebook</option><option value="youtube">YouTube</option><option value="x">X</option><option value="tiktok">TikTok</option></select></label><label>Account name<input name="display_name" required /></label><label>Destination ID<input name="external_account_id" required /></label><input type="hidden" name="is_mock" value="true" /><button className="primary">Add mock account</button></form><RecordList rows={data} kind="account" onAction={async (row) => {
          const confirmed = await openMessageDialog({
            kind: "confirm",
            title: "Disconnect LinkedIn account?",
            message: `Disconnect ${row.display_name}? The saved LinkedIn credentials for this workspace will be deleted.`,
            confirmLabel: "Disconnect account",
            tone: "danger",
          });
          if (confirmed === null) return;
          try {
            await request(`${base}/social-accounts/${row.id}/connection`, { method: "DELETE" });
            setNotice("LinkedIn connection disconnected and stored credentials deleted.", "success");
            await loadWorkspace();
          } catch (error) { setNotice(error instanceof Error ? error.message : "Disconnect failed", "error"); }
        }} /></>}
        {tab === "Publishing" && <RecordList rows={data} kind="job" onAction={async (row, action) => {
          try {
            if (action === "reconcile") {
              const rateLimited = row.status === "blocked";
              const outcome = await openMessageDialog({
                kind: "select",
                title: "Reconcile LinkedIn publish outcome",
                message: rateLimited
                  ? "Wait for LinkedIn's Retry-After period and verify that no post was created before authorizing a retry."
                  : "Choose only the outcome you verified directly in LinkedIn.",
                options: rateLimited
                  ? [{ label: "No post exists — authorize retry", value: "retry" }, { label: "Cancel", value: "cancel" }]
                  : [{ label: "Post exists", value: "published" }, { label: "Verified no post exists", value: "not_published" }],
                confirmLabel: "Continue",
              });
              if (outcome === null || outcome === "cancel") return;
              const evidence = await openMessageDialog({
                kind: "textarea",
                title: "Record verification evidence",
                message: "Summarize how you verified the outcome (20–500 characters). Never enter passwords, access tokens, or other credentials.",
                required: true,
                minLength: 20,
                maxLength: 500,
                confirmLabel: "Continue",
              });
              if (evidence === null) return;
              let payload: AnyRecord = { evidence_summary: evidence };
              if (rateLimited && outcome === "retry") {
                payload.outcome = "retry_after_rate_limit";
                payload.retry_confirmation = "I waited for Retry-After and confirmed no LinkedIn post was created";
              } else if (!rateLimited && outcome === "published") {
                const providerPostId = await openMessageDialog({
                  kind: "text",
                  title: "Enter the confirmed LinkedIn post ID",
                  message: "Enter the post URN shown by LinkedIn (urn:li:share:… or urn:li:ugcPost:…).",
                  placeholder: "urn:li:share:…",
                  required: true,
                  maxLength: 240,
                  confirmLabel: "Record published post",
                });
                if (providerPostId === null) return;
                payload.outcome = "confirmed_published";
                payload.provider_post_id = providerPostId;
              } else if (!rateLimited && outcome === "not_published") {
                const confirmed = await openMessageDialog({
                  kind: "confirm",
                  title: "Authorize a LinkedIn retry?",
                  message: "You confirmed no post exists. Retrying may create a new live post; ensure your verification is accurate before continuing.",
                  confirmLabel: "Authorize retry",
                  tone: "danger",
                });
                if (confirmed === null) return;
                payload.outcome = "confirmed_not_published";
                payload.retry_confirmation = "I confirmed no LinkedIn post was created";
              } else {
                setNotice("Reconciliation choice was not valid for this job.");
                return;
              }
              const result = await request(`${base}/publish-jobs/${row.id}/reconcile`, {
                method: "POST",
                body: JSON.stringify(payload),
              });
              setNotice(result.status === "queued"
                ? "Retry authorized and queued after operator confirmation."
                : result.status === "published"
                  ? "LinkedIn post recorded as published."
                  : `LinkedIn reconciliation saved with status ${result.status}.`);
            } else {
              if (!row.account_is_mock) {
                const confirmed = await openMessageDialog({
                  kind: "confirm",
                  title: "Publish to LinkedIn now?",
                  message: "This approved content will be sent to the connected LinkedIn account. The provider call can create a public post.",
                  confirmLabel: "Publish now",
                  tone: "danger",
                });
                if (confirmed === null) return;
              }
              await request(`${base}/publish-jobs/${row.id}/dispatch`, { method: "POST" });
              setNotice(`${row.platform === "linkedin" ? "LinkedIn" : "Mock provider"} confirmed the post.`, "success");
            }
            await loadWorkspace();
          } catch (error) { setNotice(error instanceof Error ? error.message : "Publishing action failed", "error"); }
        }} />}
        {tab === "Analytics" && <>
          <div className="callout"><strong>Snapshot-based reporting.</strong> Data below is mock or manually imported, not a live provider sync. Check each metric definition and capture time; engagement rates and reach are not assumed comparable across platforms. Latest capture: {analyticsMeta?.freshness?.latest_snapshot_at ?? "none"}.</div>
          <form className="panel form-grid" onSubmit={async (event) => {
            event.preventDefault();
            const values = Object.fromEntries(new FormData(event.currentTarget).entries());
            const params = new URLSearchParams();
            if (values.start) params.set("start", new Date(String(values.start)).toISOString());
            if (values.end) params.set("end", new Date(String(values.end)).toISOString());
            if (values.platform) params.set("platform", String(values.platform));
            if (values.social_account_id) params.set("social_account_id", String(values.social_account_id));
            if (values.campaign_id) params.set("campaign_id", String(values.campaign_id));
            if (values.content_item_id) params.set("content_item_id", String(values.content_item_id));
            const query = params.toString();
            try {
              const result = await request(`/api/v1/workspaces/${workspace.id}/analytics${query ? `?${query}` : ""}`);
              setAnalyticsQuery(query);
              setAnalyticsMeta(result);
              setData(result.snapshots);
            } catch (error) { setNotice(error instanceof Error ? error.message : "Could not filter analytics", "error"); }
          }}>
            <h2>Filter reporting period and scope</h2>
            <label>From<input name="start" type="datetime-local" /></label>
            <label>Through<input name="end" type="datetime-local" /></label>
            <label>Platform<select name="platform"><option value="">All platforms</option><option value="linkedin">LinkedIn</option><option value="instagram">Instagram</option><option value="facebook">Facebook</option><option value="youtube">YouTube</option><option value="x">X</option><option value="tiktok">TikTok</option></select></label>
            <label>Account<select name="social_account_id"><option value="">All accounts</option>{accounts.map((account) => <option key={account.id} value={account.id}>{account.display_name}</option>)}</select></label>
            <label>Campaign ID<input name="campaign_id" type="number" min="1" /></label>
            <label>Content ID<input name="content_item_id" type="number" min="1" /></label>
            <button className="secondary">Apply filters</button>
            <button className="link-button" type="button" onClick={() => setAnalyticsQuery("")}>Clear filters</button>
          </form>
          <form className="panel form-grid" onSubmit={async (event) => {
            event.preventDefault();
            const form = event.currentTarget;
            const values = Object.fromEntries(new FormData(form).entries());
            try {
              const metrics = JSON.parse(String(values.metrics_json || "{}"));
              await request(`${base}/analytics/snapshots`, {
                method: "POST",
                body: JSON.stringify({
                  social_account_id: Number(values.social_account_id),
                  content_item_id: values.content_item_id ? Number(values.content_item_id) : null,
                  campaign_id: values.campaign_id ? Number(values.campaign_id) : null,
                  period_start: new Date(String(values.period_start)).toISOString(),
                  period_end: new Date(String(values.period_end)).toISOString(),
                  metrics,
                  unavailable_metrics: String(values.unavailable_metrics ?? "").split(",").map((name) => name.trim()).filter(Boolean),
                  source: values.source,
                  source_reference: values.source_reference,
                  source_note: values.source_note,
                }),
              });
              setNotice("Analytics snapshot saved.", "success");
              form.reset();
              await loadWorkspace();
            } catch (error) { setNotice(error instanceof Error ? error.message : "Could not import analytics snapshot", "error"); }
          }}>
            <h2>Record an analytics snapshot</h2>
            <label>Social account<select name="social_account_id" required>{accounts.map((account) => <option key={account.id} value={account.id}>{account.display_name} · {account.platform}{account.is_mock ? " · mock" : ""}</option>)}</select></label>
            <label>Source<select name="source"><option value="manual_import">Manual import</option><option value="mock">Mock data (mock accounts only)</option></select></label>
            <label>Content item ID (optional)<input name="content_item_id" type="number" min="1" /></label>
            <label>Campaign ID (optional)<input name="campaign_id" type="number" min="1" /></label>
            <label>Period start<input name="period_start" type="datetime-local" required /></label>
            <label>Period end<input name="period_end" type="datetime-local" required /></label>
            <label className="wide">Metric values (JSON)<textarea name="metrics_json" defaultValue='{"impressions":0,"clicks":0}' rows={3} /></label>
            <label>Unavailable metric keys<input name="unavailable_metrics" placeholder="reach, engagement_rate" /></label>
            <label>Import reference<input name="source_reference" minLength={8} maxLength={160} required /></label>
            <label className="wide">Source/provenance note<textarea name="source_note" minLength={8} maxLength={500} required rows={2} /></label>
            <button className="primary">Save labelled snapshot</button>
            <p className="helper wide">Supported keys: {analyticsMeta?.definitions?.map((metric: AnyRecord) => metric.key).join(", ")}. No provider data is fetched.</p>
          </form>
          <div className="panel"><div className="panel-heading"><h2>Snapshots</h2><button className="secondary" onClick={async () => {
            try {
              const response = await fetch(`${API}${base}/analytics/export.csv${analyticsQuery ? `?${analyticsQuery}` : ""}`, {
                headers: { Authorization: "Bearer " + token },
              });
              if (!response.ok) {
                const error = await response.json();
                throw new Error(error?.detail ?? "CSV export failed");
              }
              const url = URL.createObjectURL(await response.blob());
              const link = document.createElement("a");
              link.href = url;
              link.download = "analytics.csv";
              link.click();
              URL.revokeObjectURL(url);
            } catch (error) { setNotice(error instanceof Error ? error.message : "CSV export failed", "error"); }
          }}>Export CSV</button></div><RecordList rows={data} kind="analytics" /></div>
          <form className="panel form-grid" onSubmit={async (event) => {
            event.preventDefault();
            const form = event.currentTarget;
            const values = Object.fromEntries(new FormData(form).entries());
            try {
              await request(`${base}/experiments`, {
                method: "POST",
                body: JSON.stringify({
                  campaign_id: values.campaign_id ? Number(values.campaign_id) : null,
                  platform: values.platform || null,
                  name: values.name,
                  hypothesis: values.hypothesis,
                  variant: values.variant,
                  primary_metric: values.primary_metric,
                  period_start: new Date(String(values.period_start)).toISOString(),
                  period_end: new Date(String(values.period_end)).toISOString(),
                  status: values.status,
                  outcome: values.outcome,
                  next_action: values.next_action,
                }),
              });
              setNotice("Experiment log saved. Outcomes are observations, not guarantees.", "success");
              form.reset();
              await loadWorkspace();
            } catch (error) { setNotice(error instanceof Error ? error.message : "Could not save experiment", "error"); }
          }}>
            <h2>Experiment log</h2>
            <label>Name<input name="name" required maxLength={200} /></label>
            <label>Campaign ID (optional)<input name="campaign_id" type="number" min="1" /></label>
            <label>Platform<select name="platform"><option value="">All/unspecified</option><option value="linkedin">LinkedIn</option><option value="instagram">Instagram</option><option value="facebook">Facebook</option><option value="youtube">YouTube</option><option value="x">X</option><option value="tiktok">TikTok</option></select></label>
            <label>Primary metric<select name="primary_metric">{(analyticsMeta?.definitions ?? []).map((metric: AnyRecord) => <option key={metric.key} value={metric.key}>{metric.label}</option>)}</select></label>
            <label className="wide">Hypothesis<textarea name="hypothesis" required minLength={3} rows={2} /></label>
            <label className="wide">Variant/intervention<textarea name="variant" required rows={2} /></label>
            <label>Period start<input name="period_start" type="datetime-local" required /></label>
            <label>Period end<input name="period_end" type="datetime-local" required /></label>
            <label>Status<select name="status"><option value="planned">Planned</option><option value="running">Running</option><option value="completed">Completed</option><option value="paused">Paused</option></select></label>
            <label>Outcome<textarea name="outcome" rows={2} /></label>
            <label className="wide">Next action<textarea name="next_action" rows={2} /></label>
            <button className="primary">Save experiment</button>
          </form>
          <RecordList rows={experiments} kind="experiment" emptyText="Log a hypothesis, variant, period, metric, outcome, and next action." />
        </>}
        {tab === "Community" && <>
          <div className="callout"><strong>Drafts only; no external replies are sent.</strong> Real provider community APIs are not enabled. Add a mock interaction or manually import one from a source you are authorized to use. Reply drafts require human review.</div>
          <form className="panel form-grid" onSubmit={async (event) => {
            event.preventDefault();
            const form = event.currentTarget;
            const values = Object.fromEntries(new FormData(form).entries());
            try {
              await request(`${base}/community`, {
                method: "POST",
                body: JSON.stringify({
                  social_account_id: Number(values.social_account_id),
                  external_message_id: values.external_message_id,
                  conversation_id: values.conversation_id,
                  author_name: values.author_name,
                  body: values.body,
                  source: values.source,
                  received_at: new Date(String(values.received_at)).toISOString(),
                }),
              });
              setNotice("Community interaction imported.", "success");
              form.reset();
              await loadWorkspace();
            } catch (error) { setNotice(error instanceof Error ? error.message : "Could not import interaction", "error"); }
          }}>
            <h2>Import an interaction</h2>
            <label>Social account<select name="social_account_id" required>{accounts.map((account) => <option key={account.id} value={account.id}>{account.display_name} · {account.platform}{account.is_mock ? " · mock" : ""}</option>)}</select></label>
            <label>Import type<select name="source"><option value="mock">Mock interaction</option><option value="manual_import">Manual import</option></select></label>
            <label>External message ID<input name="external_message_id" required maxLength={160} /></label>
            <label>Conversation ID<input name="conversation_id" required maxLength={160} /></label>
            <label>Author<input name="author_name" required maxLength={160} /></label>
            <label>Received at<input name="received_at" type="datetime-local" required /></label>
            <label className="wide">Message<textarea name="body" required maxLength={5000} rows={3} /></label>
            <button className="primary">Import message</button>
          </form>
          <RecordList rows={data} kind="community" onAction={async (row, action) => {
            try {
              if (action === "reply") {
                const body = await openMessageDialog({
                  kind: "textarea",
                  title: "Draft a community reply",
                  message: "This creates a draft for human review. It will not be sent externally.",
                  required: true,
                  maxLength: 5000,
                  confirmLabel: "Save draft",
                });
                if (body === null) return;
                await request(`${base}/community/${row.id}/replies`, { method: "POST", body: JSON.stringify({ body }) });
              } else if (action === "assign") {
                const value = await openMessageDialog({
                  kind: "text",
                  title: "Assign conversation",
                  message: "Enter the user ID of an active workspace member. Leave blank to unassign.",
                  placeholder: "Workspace member user ID",
                  maxLength: 12,
                  confirmLabel: "Save assignment",
                });
                if (value === null) return;
                if (value.trim() && (!/^\d+$/.test(value.trim()) || Number(value) < 1)) {
                  setNotice("Enter a valid positive workspace member ID.", "error");
                  return;
                }
                await request(`${base}/community/${row.id}/assignment`, {
                  method: "PATCH",
                  body: JSON.stringify({ assigned_to: value.trim() ? Number(value) : null }),
                });
              } else if (action === "resolve" || action === "reopen") {
                await request(`${base}/community/${row.id}/status`, {
                  method: "PATCH",
                  body: JSON.stringify({ status: action === "resolve" ? "resolved" : "open" }),
                });
              } else if (action?.startsWith("submit:")) {
                await request(`${base}/community/replies/${action.split(":")[1]}/submit`, { method: "POST" });
              } else if (action?.startsWith("decision:")) {
                const [, replyId, decision] = action.split(":");
                await request(`${base}/community/replies/${replyId}/decision`, {
                  method: "POST",
                  body: JSON.stringify({ decision, comment: "" }),
                });
              }
              setNotice("Community inbox updated. Approved replies remain drafts and are not sent.", "success");
              await loadWorkspace();
            } catch (error) { setNotice(error instanceof Error ? error.message : "Community action failed", "error"); }
          }} />
        </>}
        {tab === "Team" && <><form className="panel form-grid" onSubmit={(event) => submit(event, `${base}/members`)}><h2>Add registered workspace member</h2><label>Email<input name="email" type="email" required /></label><label>Role<select name="role"><option>approver</option><option>creator</option><option>strategist</option><option>admin</option><option>analyst</option><option>viewer</option></select></label><p className="helper wide">The person must register first. Authors cannot approve their own content.</p><button className="primary">Add member</button></form><RecordList rows={data} kind="member" /></>}
        {tab === "Audit" && <RecordList rows={data} kind="audit" />}
      </div>
    </section>
  </main>;
}

function stringList(value: unknown): string {
  return Array.isArray(value) ? value.join(", ") : String(value ?? "");
}

function parseIds(value: unknown): number[] {
  return String(value ?? "").split(",").map((item) => Number(item.trim())).filter((item) => Number.isInteger(item) && item > 0);
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
    <div className="callout"><strong>Publishing is protected.</strong> The API revalidates the approved immutable version, account, destination, and action immediately before dispatch and again before any provider call.</div>
  </>;
}

function Metric({ label, value, onClick }: { label: string; value: string | number; onClick?: () => void }) {
  return <button className="metric panel" onClick={onClick}><span>{label}</span><strong>{value}</strong></button>;
}

function RecordList({ rows, kind, onAction, emptyText }: { rows: AnyRecord[]; kind: string; onAction?: (row: AnyRecord, action?: string) => void; emptyText?: string }) {
  if (!rows.length) return <div className="empty panel"><span className="empty-icon">○</span><strong>Nothing here yet</strong><p>{emptyText ?? `New ${kind} records will appear here.`}</p></div>;
  return <div className="record-list">{rows.map((row) => {
    const version = row.version;
    const label = row.content_title || row.account_name || row.author_name || row.title || row.name || row.display_name || row.question || row.agent_name || `${kind} #${row.id}`;
    const contentSummary = row.body ?? row.content_body ?? version?.body ?? row.objective ?? row.email ?? row.action ?? row.destination ?? row.error_message ?? row.external_account_id ?? row.comment ?? row.resource_type ?? (row.output ? JSON.stringify(row.output) : "");
    return <article className="record panel" key={`${kind}-${row.id ?? row.user_id}`}>
      <div className="record-main"><strong>{label}</strong><p>{contentSummary}</p>
        <small>ID {row.id ?? row.user_id}{row.platform ? ` · ${row.platform}` : ""}{row.role ? ` · ${row.role}` : ""}{row.provider ? ` · ${row.provider}/${row.model}` : ""}{row.duration_ms ? ` · ${row.duration_ms} ms` : ""}{row.status ? ` · ${row.status}` : ""}{row.account_name ? ` · ${row.account_name}` : ""}{row.destination ? ` · Destination: ${row.destination}` : ""}{row.version_number ? ` · Version ${row.version_number}` : ""}{version?.number ? ` · Version ${version.number}` : ""}{row.is_mock === true || row.account_is_mock === true ? " · MOCK - no external post" : row.is_mock === false || row.account_is_mock === false ? " · LIVE ACCOUNT" : ""}</small>
        {kind === "research" && row.findings?.length > 0 && <p className="helper">{row.findings.map((finding: AnyRecord) => `${finding.classification}: ${finding.statement} [source IDs: ${finding.source_ids.join(", ") || "none"}]`).join(" · ")}</p>}
        {kind === "analytics" && <div className="helper"><p>Period: {row.period_start} → {row.period_end} · Captured: {row.fetched_at} · Source: {row.source} · {row.source_note}</p><p>{Object.entries(row.metrics ?? {}).map(([key, value]) => `${key}: ${value}`).join(" · ") || "No metric values supplied"}{row.unavailable_metrics?.length ? ` · Unavailable: ${row.unavailable_metrics.join(", ")}` : ""}</p>{row.campaign_name && <p>Campaign: {row.campaign_name}</p>}</div>}
        {kind === "experiment" && <div className="helper"><p>Hypothesis: {row.hypothesis}</p><p>Variant: {row.variant} · Primary metric: {row.primary_metric} · Period: {row.period_start} → {row.period_end}</p>{row.outcome && <p>Outcome: {row.outcome}</p>}{row.next_action && <p>Next action: {row.next_action}</p>}</div>}
        {kind === "community" && <div className="helper"><p>Conversation {row.conversation_id} · Received: {row.received_at} · Source: {row.source} · {row.assigned_to ? `Assigned to user ${row.assigned_to}` : "Unassigned"}</p><p>Reply sending is unavailable; approved replies remain drafts.</p>{row.replies?.map((reply: AnyRecord) => <div key={reply.id}><p><strong>Reply #{reply.id} · {reply.status}:</strong> {reply.body}</p><div className="record-actions">{reply.status === "draft" || reply.status === "changes_requested" ? <button className="secondary" onClick={() => onAction?.(row, `submit:${reply.id}`)}>Submit draft for review</button> : null}{reply.status === "pending_review" && <><button className="primary" onClick={() => onAction?.(row, `decision:${reply.id}:approved`)}>Approve draft</button><button className="secondary" onClick={() => onAction?.(row, `decision:${reply.id}:changes_requested`)}>Request changes</button><button className="danger" onClick={() => onAction?.(row, `decision:${reply.id}:rejected`)}>Reject draft</button></>}</div></div>)}</div>}
        {kind === "campaign" && row.latest_strategy?.output && <div className="helper">
          <strong>Latest strategy · {row.latest_strategy.output.audience}</strong>
          <p>Message pillars: {row.latest_strategy.output.message_pillars.map((pillar: AnyRecord) => pillar.name).join(", ")}</p>
          <p>CTA: {row.latest_strategy.output.call_to_action}</p>
          <small>Strategy #{row.latest_strategy.id} · Run #{row.latest_strategy.agent_run_id} · Source IDs: {row.latest_strategy.source_ids.join(", ") || "none"}</small>
        </div>}
        {version?.source_references?.length > 0 && <p className="helper">Source IDs: {version.source_references.join(", ")}</p>}
        {kind === "research" && row.sources?.length > 0 && <p className="helper">{row.sources.map((source: AnyRecord) => `Source ${source.id}: ${source.title} — ${source.url}`).join(" · ")}</p>}
        {kind === "account" && row.capabilities?.length > 0 && <p className="helper">{row.capabilities.map((capability: AnyRecord) => `${capability.capability}: ${capability.status}${capability.enabled ? " (enabled)" : ""} — ${capability.reason}`).join(" · ")}</p>}
        {kind === "approval" && row.source_references?.length > 0 && <p className="helper">{row.source_references.map((source: AnyRecord) => `Source ${source.id}: ${source.title} — ${source.url}`).join(" · ")}</p>}
        {kind === "approval" && row.quality_findings?.length > 0 && <p className="helper">{row.quality_findings.map((finding: AnyRecord) => `${finding.review_type}: ${finding.message}${finding.remediation ? ` (${finding.remediation})` : ""}`).join(" ")}</p>}
      </div>
      {kind === "content" && onAction && <div className="record-actions"><button className="secondary" onClick={() => onAction(row, "revise")}>New version</button><button className="secondary" onClick={() => onAction(row, "preview")}>Preview</button><button className="secondary" onClick={() => onAction(row, "quality")}>Quality check</button><button className="secondary" onClick={() => onAction(row, "ai-review")}>AI review</button><button className="primary" onClick={() => onAction(row, "submit")}>Request approval</button></div>}
      {kind === "account" && !row.is_mock && onAction && <button className="danger" onClick={() => onAction(row, "disconnect")}>Disconnect LinkedIn</button>}
      {kind === "approval" && row.status === "pending" && onAction && <div className="record-actions"><button className="primary" onClick={() => onAction(row, "approved")}>{row.account_is_mock === true ? "Approve mock post" : row.account_is_mock === false ? "Approve LIVE LinkedIn post" : "Approve"}</button><button className="secondary" onClick={() => onAction(row, "changes_requested")}>Request changes</button><button className="danger" onClick={() => onAction(row, "rejected")}>Reject</button></div>}
      {kind === "job" && row.status === "queued" && onAction && <button className="primary" onClick={() => onAction(row, "dispatch")}>Dispatch post</button>}
      {kind === "job" && (row.status === "reconciliation_required" || (row.status === "blocked" && String(row.error_message ?? "").startsWith("LinkedIn rate limited this publish request (HTTP 429)"))) && onAction && <button className="secondary" onClick={() => onAction(row, "reconcile")}>{row.status === "blocked" ? "Retry after rate limit" : "Reconcile LinkedIn outcome"}</button>}
      {kind === "community" && onAction && <div className="record-actions">{row.status === "open" && <button className="primary" onClick={() => onAction(row, "reply")}>Draft reply</button>}<button className="secondary" onClick={() => onAction(row, "assign")}>Assign</button><button className="secondary" onClick={() => onAction(row, row.status === "open" ? "resolve" : "reopen")}>{row.status === "open" ? "Resolve" : "Reopen"}</button></div>}
      {row.status && <span className={`status ${row.status}`}>{row.status.replaceAll("_", " ")}</span>}
    </article>;
  })}</div>;
}
