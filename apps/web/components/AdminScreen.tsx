"use client";
import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { AdminOverview } from "@/lib/types";

const ROLES = ["designer", "checker", "approver"] as const;

function refusal(e: unknown): string {
  if (e instanceof ApiError) return e.code ? `${e.code}: ${e.message}` : e.message;
  return e instanceof Error ? e.message : String(e);
}

/** Firm administration: people and roles, invitations, the firm's settings and its drafting templates. Only an administrator sees it; the server checks again. */
export function AdminScreen() {
  const [data, setData] = useState<AdminOverview | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<string>("designer");
  const load = useCallback(async () => {
    try { setData(await api.adminOverview()); } catch (e) { setMessage(refusal(e)); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const act = async (fn: () => Promise<unknown>, ok: string) => {
    setMessage(null);
    try { await fn(); setMessage(ok); await load(); } catch (e) { setMessage(refusal(e)); }
  };
  if (!data) return <main><h1>Firm administration</h1>{message ? <p role="alert">{message}</p> : <p>Loading…</p>}</main>;
  const f = data.firm;
  return (
    <main>
      <h1>Firm administration</h1>
      {message && <p role="status" data-testid="admin-message">{message}</p>}

      <section aria-label="People">
        <h2>People</h2>
        <table data-testid="admin-users">
          <thead><tr><th>Person</th><th>Role</th><th>Administrator</th><th>Registration</th><th>Status</th></tr></thead>
          <tbody>{data.users.map((u) => (
            <tr key={u.id}>
              <td>{u.email ?? u.id}</td>
              <td><select aria-label={`Role of ${u.email ?? u.id}`} value={u.role} onChange={(e) => void act(() => api.adminPatchUser(u.id, { role: e.target.value }), "Role changed.")}>
                {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}</select></td>
              <td><input type="checkbox" aria-label={`Administrator ${u.email ?? u.id}`} checked={u.is_admin}
                         onChange={(e) => void act(() => api.adminPatchUser(u.id, { is_admin: e.target.checked }), "Administrator changed.")} /></td>
              <td>{u.registration_no ?? "none"}</td>
              <td><button type="button" onClick={() => void act(() => api.adminPatchUser(u.id, { active: !u.active }), u.active ? "Deactivated." : "Reactivated.")}>
                {u.active ? "Deactivate" : "Reactivate"}</button></td>
            </tr>
          ))}</tbody>
        </table>
      </section>

      <section aria-label="Invitations">
        <h2>Invite someone</h2>
        <label>E-mail <input type="email" aria-label="Invite e-mail" value={email} onChange={(e) => setEmail(e.target.value)} /></label>{" "}
        <label>Role <select aria-label="Invite role" value={role} onChange={(e) => setRole(e.target.value)}>{ROLES.map((r) => <option key={r} value={r}>{r}</option>)}</select></label>{" "}
        <button type="button" disabled={!email.includes("@")} onClick={() => void act(async () => { await api.adminInvite(email, role); setEmail(""); }, "Invitation sent.")}>Invite</button>
        {data.invitations.length > 0 && (
          <ul data-testid="admin-invitations">{data.invitations.map((i) => (
            <li key={i.id}>{i.email} as {i.role}, until {i.expires_at.slice(0, 10)}{" "}
              <button type="button" onClick={() => void act(() => api.adminRevokeInvite(i.id), "Invitation withdrawn.")}>Withdraw</button></li>
          ))}</ul>
        )}
        <p>The person signs in with this address (a link is e-mailed) and then joins the firm in the role above. Registration numbers are verified separately (see Registrations).</p>
      </section>

      <FirmSettings key={f.id + f.sample_size + f.signer_mode} firm={f} onSave={(v) => void act(() => api.adminSaveFirm(v), "Settings saved.")} />

      <section aria-label="Templates">
        <h2>Drafting templates</h2>
        <p>A title block is a DXF drawing; a layer standard is JSON like {"{"}"layers": {"{"}"A-DUCT": {"{"}"color": 3{"}"}{"}"}{"}"}. The newest of each kind is used by every drafting skill.</p>
        <TemplateUpload onUpload={(kind, name, file) => void act(() => api.adminAddTemplate(kind, name, file), "Template uploaded.")} />
        <ul data-testid="admin-templates">{data.templates.map((t) => <li key={t.id}>{t.kind.replace("_", " ")}: {t.name} ({t.sha256.slice(0, 10)}…) {t.created_at.slice(0, 10)}</li>)}</ul>
      </section>
    </main>
  );
}

function FirmSettings({ firm, onSave }: { firm: AdminOverview["firm"]; onSave: (v: { name: string; signer_mode: string; sample_size: number; near_miss_default: number | null }) => void }) {
  const [name, setName] = useState(firm.name);
  const [mode, setMode] = useState<string>(firm.signer_mode);
  const [sample, setSample] = useState(String(firm.sample_size));
  const [near, setNear] = useState(firm.near_miss_default === null ? "" : String(firm.near_miss_default));
  return (
    <section aria-label="Firm settings">
      <h2>Firm settings</h2>
      <label>Firm name <input aria-label="Firm name" value={name} onChange={(e) => setName(e.target.value)} /></label><br />
      <label>Signer independence{" "}
        <select aria-label="Signer independence" value={mode} onChange={(e) => setMode(e.target.value)}>
          <option value="strict">strict: three different people sign</option>
          <option value="small_firm">small firm: one person may hold several gates (every package says NOT INDEPENDENTLY CHECKED)</option>
        </select></label><br />
      <label>Spot-check sample size <input aria-label="Sample size" type="number" min={1} max={1000} value={sample} onChange={(e) => setSample(e.target.value)} /></label><br />
      <label>Near-miss default (fraction, blank = rules decide) <input aria-label="Near miss default" type="number" step="0.01" min={0} max={0.5} value={near} onChange={(e) => setNear(e.target.value)} /></label><br />
      <button type="button" onClick={() => onSave({ name, signer_mode: mode, sample_size: Number(sample), near_miss_default: near.trim() === "" ? null : Number(near) })}>Save settings</button>
    </section>
  );
}

function TemplateUpload({ onUpload }: { onUpload: (kind: string, name: string, file: File) => void }) {
  const [kind, setKind] = useState("layer_standard");
  const [name, setName] = useState("");
  return (
    <p>
      <select aria-label="Template kind" value={kind} onChange={(e) => setKind(e.target.value)}>
        <option value="layer_standard">layer standard (JSON)</option><option value="title_block">title block (DXF)</option>
      </select>{" "}
      <input aria-label="Template name" placeholder="name" value={name} onChange={(e) => setName(e.target.value)} />{" "}
      <input type="file" aria-label="Template file" disabled={!name.trim()} onChange={(e) => { const f = e.target.files?.[0]; if (f) onUpload(kind, name.trim(), f); e.target.value = ""; }} />
    </p>
  );
}
