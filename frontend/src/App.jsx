import React, { useState, useEffect } from "react";

// Point this at your deployed backend. Defaults to local dev.
const API_BASE = import.meta.env?.VITE_API_BASE || "http://localhost:8000";

const C = {
  bg: "#0A0F1A", panel: "#111A2B", panelAlt: "#0D1524", border: "#1E2A42",
  text: "#E7ECF3", muted: "#7F91B0", stable: "#34D399", warn: "#F5A524",
  crit: "#F0533D", info: "#38BDF8",
};
const mono = "ui-monospace, 'SF Mono', 'Cascadia Code', monospace";
const sans = "'Inter', ui-sans-serif, system-ui, -apple-system, sans-serif";

const GLOBAL_CSS = `
  * { box-sizing: border-box; }
  .sba-grid { display: grid; grid-template-columns: 1.1fr 1fr; gap: 16px; }
  @media (max-width: 720px) { .sba-grid { grid-template-columns: 1fr; } .sba-formgrid { grid-template-columns: 1fr 1fr !important; } }
  .sba-btn:focus-visible, .sba-input:focus-visible, .sba-input:focus { outline: 2px solid ${C.info}; outline-offset: 1px; }
  @keyframes sba-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.45; } }
`;

const inputStyle = (warn) => ({
  background: C.panelAlt, border: `1px solid ${warn ? C.warn + "aa" : C.border}`,
  borderRadius: 6, color: C.text, padding: "8px 10px", fontFamily: mono, fontSize: 14, width: "100%",
});

const DOMAIN_META = [
  { key: "respiratory", label: "Respiratory" }, { key: "coagulation", label: "Coagulation" },
  { key: "liver", label: "Liver" }, { key: "cardiovascular", label: "Cardiovascular" },
  { key: "cns", label: "CNS" }, { key: "renal", label: "Renal" },
];

function ScoreBar({ label, score }) {
  const pct = score == null ? 0 : (score / 4) * 100;
  const color = score == null ? C.border : score >= 3 ? C.crit : score === 2 ? C.warn : score >= 1 ? C.info : C.stable;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
      <div style={{ width: 108, fontSize: 11, color: C.muted, flexShrink: 0 }}>{label}</div>
      <div style={{ flex: 1, height: 8, background: C.panelAlt, borderRadius: 4, overflow: "hidden", border: `1px solid ${C.border}` }}>
        <div style={{ width: `${pct}%`, height: "100%", background: color, transition: "width 300ms ease" }} />
      </div>
      <div style={{ width: 18, textAlign: "right", fontFamily: mono, fontSize: 13, color: score == null ? C.muted : C.text }}>
        {score == null ? "–" : score}
      </div>
    </div>
  );
}

// ---------- Login screen ----------
function LoginScreen({ onLogin }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      const body = new URLSearchParams({ username, password });
      const resp = await fetch(`${API_BASE}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body,
      });
      if (!resp.ok) throw new Error("Invalid username or password");
      const data = await resp.json();
      onLogin({ token: data.access_token, fullName: data.full_name, role: data.role });
    } catch (err) {
      setError(err.message || "Login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ minHeight: "100vh", background: C.bg, color: C.text, fontFamily: sans, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
      <style>{GLOBAL_CSS}</style>
      <form onSubmit={submit} style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 12, padding: 28, width: 320 }}>
        <div style={{ fontSize: 11, letterSpacing: 2, color: C.info, textTransform: "uppercase", marginBottom: 6 }}>Sepsis Bundle Agent</div>
        <div style={{ fontSize: 18, fontWeight: 600, marginBottom: 20 }}>Clinician Sign-In</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <input className="sba-input" style={inputStyle(false)} placeholder="Username" value={username} onChange={(e) => setUsername(e.target.value)} />
          <input className="sba-input" style={inputStyle(false)} placeholder="Password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        {error && <div style={{ color: C.crit, fontSize: 12, marginTop: 10 }}>{error}</div>}
        <button className="sba-btn" type="submit" disabled={loading} style={{ marginTop: 16, width: "100%", background: C.info, color: "#06111F", border: "none", borderRadius: 6, padding: "10px 0", fontWeight: 600, cursor: "pointer", opacity: loading ? 0.6 : 1 }}>
          {loading ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}

// ---------- Main app ----------
export default function SepsisBundleAgent() {
  const [auth, setAuth] = useState(null); // { token, fullName, role }
  const [patientId, setPatientId] = useState("");
  const [sourceSuspected, setSourceSuspected] = useState("");
  const [vals, setVals] = useState({
    pao2_fio2: "", platelets: "", bilirubin: "", map_mmhg: "",
    pressor_drug: "none", pressor_dose: "", gcs: "", creatinine: "", urine_output_24h: "",
  });
  const [lactateInitial, setLactateInitial] = useState("");

  const [sofa, setSofa] = useState(null);
  const [sofaLoading, setSofaLoading] = useState(false);
  const [sofaError, setSofaError] = useState("");

  const [bundle, setBundle] = useState(null);
  const [confirmedByInput, setConfirmedByInput] = useState({});

  const [interpretation, setInterpretation] = useState("");
  const [interpLoading, setInterpLoading] = useState(false);
  const [interpError, setInterpError] = useState("");

  const [apiError, setApiError] = useState("");

  function authHeaders(extra = {}) {
    return { Authorization: `Bearer ${auth.token}`, ...extra };
  }

  function updateVal(key, v) {
    setVals((p) => ({ ...p, [key]: v }));
  }

  async function calculateSofa() {
    if (!patientId) { setSofaError("Enter a patient ID first."); return; }
    setSofaLoading(true);
    setSofaError("");
    try {
      const payload = { patient_id: patientId };
      Object.entries(vals).forEach(([k, v]) => {
        if (v !== "" && v !== undefined) {
          payload[k] = k === "pressor_drug" ? v : parseFloat(v);
        }
      });
      const resp = await fetch(`${API_BASE}/sofa/calculate`, {
        method: "POST", headers: authHeaders({ "Content-Type": "application/json" }), body: JSON.stringify(payload),
      });
      if (resp.status === 401) { setAuth(null); return; }
      if (!resp.ok) throw new Error((await resp.json()).detail || "Calculation failed");
      const data = await resp.json();
      setSofa(data);
    } catch (e) {
      setSofaError(e.message);
    } finally {
      setSofaLoading(false);
    }
  }

  async function refreshBundleStatus() {
    if (!patientId) return;
    try {
      const params = new URLSearchParams({ patient_id: patientId });
      if (vals.map_mmhg) params.set("map_mmhg", vals.map_mmhg);
      if (lactateInitial) params.set("lactate_initial", lactateInitial);
      const resp = await fetch(`${API_BASE}/bundle/status?${params}`, { headers: authHeaders() });
      if (resp.status === 401) { setAuth(null); return; }
      if (!resp.ok) return;
      setBundle(await resp.json());
    } catch (e) {
      setApiError("Could not reach the backend for bundle status.");
    }
  }

  useEffect(() => {
    if (auth && patientId && bundle?.recognition_time) {
      const t = setInterval(refreshBundleStatus, 15000);
      return () => clearInterval(t);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth, patientId, bundle?.recognition_time]);

  async function startBundle() {
    if (!patientId) { setApiError("Enter a patient ID first."); return; }
    setApiError("");
    try {
      const resp = await fetch(`${API_BASE}/bundle/start`, {
        method: "POST", headers: authHeaders({ "Content-Type": "application/json" }), body: JSON.stringify({ patient_id: patientId }),
      });
      if (resp.status === 401) { setAuth(null); return; }
      await refreshBundleStatus();
    } catch (e) {
      setApiError("Could not start the bundle clock.");
    }
  }

  async function confirmItem(itemKey, dualRequired) {
    setApiError("");
    try {
      const body = { patient_id: patientId, item_key: itemKey };
      if (dualRequired) {
        const confirmedBy = confirmedByInput[itemKey];
        if (!confirmedBy) { setApiError(`"${itemKey}" needs a second clinician's username in confirmed_by.`); return; }
        body.confirmed_by = confirmedBy;
      }
      const resp = await fetch(`${API_BASE}/bundle/confirm`, {
        method: "POST", headers: authHeaders({ "Content-Type": "application/json" }), body: JSON.stringify(body),
      });
      if (resp.status === 401) { setAuth(null); return; }
      if (!resp.ok) { setApiError((await resp.json()).detail || "Confirmation failed"); return; }
      await refreshBundleStatus();
    } catch (e) {
      setApiError("Could not confirm this item.");
    }
  }

  async function getInterpretation() {
    if (!patientId) return;
    setInterpLoading(true);
    setInterpError("");
    setInterpretation("");
    try {
      const resp = await fetch(`${API_BASE}/interpretation`, {
        method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ patient_id: patientId, suspected_source: sourceSuspected || undefined }),
      });
      if (resp.status === 401) { setAuth(null); return; }
      if (!resp.ok) throw new Error("Interpretation request failed");
      const data = await resp.json();
      setInterpretation(data.interpretation);
    } catch (e) {
      setInterpError("Could not reach the interpretation service.");
    } finally {
      setInterpLoading(false);
    }
  }

  if (!auth) return <LoginScreen onLogin={setAuth} />;

  const totalColor = !sofa ? C.muted : sofa.total >= 11 ? C.crit : sofa.total >= 6 ? C.warn : sofa.total >= 2 ? C.info : C.stable;
  const HIGH_RISK = new Set(["pressors", "fluids", "antibiotics"]);

  return (
    <div style={{ minHeight: "100vh", background: `radial-gradient(circle at 15% 0%, #0F1A2E 0%, ${C.bg} 45%)`, color: C.text, fontFamily: sans, padding: "28px 20px 60px" }}>
      <style>{GLOBAL_CSS}</style>
      <div style={{ maxWidth: 880, margin: "0 auto" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", marginBottom: 18, flexWrap: "wrap", gap: 12 }}>
          <div>
            <div style={{ fontSize: 11, letterSpacing: 2, color: C.info, textTransform: "uppercase", marginBottom: 4 }}>Sepsis Bundle Agent</div>
            <div style={{ fontSize: 22, fontWeight: 600 }}>Hour-1 Bundle &amp; SOFA Tracker</div>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <div style={{ fontSize: 12, color: C.muted }}>Signed in as {auth.fullName} ({auth.role})</div>
            <input className="sba-input" placeholder="Patient ID" value={patientId} onChange={(e) => setPatientId(e.target.value)} style={{ ...inputStyle(false), width: 120 }} />
            <button className="sba-btn" onClick={() => setAuth(null)} style={{ background: "transparent", border: `1px solid ${C.border}`, color: C.muted, borderRadius: 6, padding: "8px 12px", fontSize: 12, cursor: "pointer" }}>Sign out</button>
          </div>
        </div>

        {apiError && (
          <div style={{ background: C.crit + "22", border: `1px solid ${C.crit}`, borderRadius: 8, padding: "8px 12px", marginBottom: 14, fontSize: 13 }}>{apiError}</div>
        )}

        {bundle?.overdue?.length > 0 && (
          <div style={{ background: C.crit + "22", border: `1px solid ${C.crit}`, borderRadius: 10, padding: "10px 14px", marginBottom: 14, display: "flex", alignItems: "center", gap: 10, fontSize: 13 }}>
            <span style={{ width: 8, height: 8, borderRadius: "50%", background: C.crit, animation: "sba-pulse 1.4s ease-in-out infinite", flexShrink: 0 }} />
            <span><strong>{bundle.overdue.length} item(s) past the 1-hour target.</strong></span>
          </div>
        )}

        <div className="sba-grid">
          <div style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 12, padding: 18 }}>
            <div style={{ fontSize: 12, color: C.muted, letterSpacing: 1, textTransform: "uppercase", marginBottom: 14 }}>Patient Values</div>
            <div className="sba-formgrid" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <label><span style={{ fontSize: 11, color: C.muted }}>PaO₂/FiO₂</span><input className="sba-input" style={inputStyle(false)} value={vals.pao2_fio2} onChange={(e) => updateVal("pao2_fio2", e.target.value)} /></label>
              <label><span style={{ fontSize: 11, color: C.muted }}>Platelets</span><input className="sba-input" style={inputStyle(false)} value={vals.platelets} onChange={(e) => updateVal("platelets", e.target.value)} /></label>
              <label><span style={{ fontSize: 11, color: C.muted }}>Bilirubin</span><input className="sba-input" style={inputStyle(false)} value={vals.bilirubin} onChange={(e) => updateVal("bilirubin", e.target.value)} /></label>
              <label><span style={{ fontSize: 11, color: C.muted }}>MAP</span><input className="sba-input" style={inputStyle(false)} value={vals.map_mmhg} onChange={(e) => updateVal("map_mmhg", e.target.value)} /></label>
              <label><span style={{ fontSize: 11, color: C.muted }}>Vasopressor</span>
                <select className="sba-input" style={{ ...inputStyle(false), fontFamily: sans }} value={vals.pressor_drug} onChange={(e) => updateVal("pressor_drug", e.target.value)}>
                  <option value="none">None</option><option value="dopamine">Dopamine</option><option value="dobutamine">Dobutamine</option>
                  <option value="norepinephrine">Norepinephrine</option><option value="epinephrine">Epinephrine</option>
                </select>
              </label>
              <label><span style={{ fontSize: 11, color: C.muted }}>Pressor dose</span><input className="sba-input" style={inputStyle(false)} value={vals.pressor_dose} onChange={(e) => updateVal("pressor_dose", e.target.value)} disabled={vals.pressor_drug === "none"} /></label>
              <label><span style={{ fontSize: 11, color: C.muted }}>GCS</span><input className="sba-input" style={inputStyle(false)} value={vals.gcs} onChange={(e) => updateVal("gcs", e.target.value)} /></label>
              <label><span style={{ fontSize: 11, color: C.muted }}>Creatinine</span><input className="sba-input" style={inputStyle(false)} value={vals.creatinine} onChange={(e) => updateVal("creatinine", e.target.value)} /></label>
              <label><span style={{ fontSize: 11, color: C.muted }}>Urine output</span><input className="sba-input" style={inputStyle(false)} value={vals.urine_output_24h} onChange={(e) => updateVal("urine_output_24h", e.target.value)} /></label>
              <label><span style={{ fontSize: 11, color: C.muted }}>Initial lactate</span><input className="sba-input" style={inputStyle(false)} value={lactateInitial} onChange={(e) => setLactateInitial(e.target.value)} /></label>
            </div>
            <button className="sba-btn" onClick={calculateSofa} disabled={sofaLoading} style={{ marginTop: 14, background: C.info, color: "#06111F", border: "none", borderRadius: 6, padding: "9px 16px", fontWeight: 600, cursor: "pointer", opacity: sofaLoading ? 0.6 : 1 }}>
              {sofaLoading ? "Calculating…" : "Calculate SOFA (server)"}
            </button>
            {sofaError && <div style={{ color: C.crit, fontSize: 12, marginTop: 8 }}>{sofaError}</div>}
          </div>

          <div style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 12, padding: 18 }}>
            <div style={{ fontSize: 12, color: C.muted, letterSpacing: 1, textTransform: "uppercase", marginBottom: 10 }}>SOFA Score</div>
            <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 6 }}>
              <div style={{ fontFamily: mono, fontSize: 56, fontWeight: 700, color: totalColor, lineHeight: 1 }}>{sofa ? sofa.total : "–"}</div>
              <div style={{ fontSize: 12, color: C.muted }}>{sofa ? `/ 24 · ${Math.round(sofa.completeness * 100)}% complete` : "not yet calculated"}</div>
            </div>
            {sofa && (
              <div style={{ fontSize: 12, color: sofa.delta_from_baseline >= 2 ? C.crit : C.muted, marginBottom: 10, fontFamily: mono }}>
                Δ baseline: {sofa.delta_from_baseline >= 0 ? "+" : ""}{sofa.delta_from_baseline}{sofa.meets_sepsis3_criteria ? " · meets Sepsis-3" : ""}
              </div>
            )}
            <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 12 }}>
              {DOMAIN_META.map((d) => <ScoreBar key={d.key} label={d.label} score={sofa?.components?.[d.key]} />)}
            </div>
          </div>
        </div>

        <div style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 12, padding: 18, marginTop: 16 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14, flexWrap: "wrap", gap: 10 }}>
            <div style={{ fontSize: 12, color: C.muted, letterSpacing: 1, textTransform: "uppercase" }}>Hour-1 Bundle</div>
            {!bundle?.recognition_time ? (
              <button className="sba-btn" onClick={startBundle} style={{ background: C.info, color: "#06111F", border: "none", borderRadius: 6, padding: "8px 14px", fontWeight: 600, fontSize: 13, cursor: "pointer" }}>Start recognition clock</button>
            ) : (
              <button className="sba-btn" onClick={refreshBundleStatus} style={{ background: "transparent", border: `1px solid ${C.border}`, color: C.info, borderRadius: 6, padding: "6px 12px", fontSize: 12, cursor: "pointer" }}>
                Refresh ({bundle.elapsed_minutes}m elapsed)
              </button>
            )}
          </div>
          {bundle?.items && (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {bundle.items.map((it) => {
                const dual = HIGH_RISK.has(it.key);
                return (
                  <div key={it.key} style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 12px", borderRadius: 8, background: C.panelAlt, border: `1px solid ${it.done ? C.stable + "55" : C.border}`, opacity: it.required ? 1 : 0.5, flexWrap: "wrap" }}>
                    <div style={{ flex: 1, minWidth: 160, fontSize: 13.5, textDecoration: it.done ? "line-through" : "none", color: it.done ? C.muted : C.text }}>{it.label}</div>
                    {dual && !it.done && (
                      <input className="sba-input" placeholder="2nd clinician username" style={{ ...inputStyle(false), width: 160, fontSize: 12 }}
                        value={confirmedByInput[it.key] || ""} onChange={(e) => setConfirmedByInput((p) => ({ ...p, [it.key]: e.target.value }))} />
                    )}
                    <button className="sba-btn" onClick={() => confirmItem(it.key, dual)} disabled={it.done || !it.required} style={{ background: it.done ? "transparent" : C.stable, color: it.done ? C.muted : "#06210F", border: it.done ? `1px solid ${C.border}` : "none", borderRadius: 6, padding: "5px 10px", fontSize: 11.5, fontWeight: 600, cursor: it.done ? "default" : "pointer" }}>
                      {it.done ? "Confirmed" : "Confirm"}
                    </button>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 12, padding: 18, marginTop: 16 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12, flexWrap: "wrap", gap: 10 }}>
            <div style={{ fontSize: 12, color: C.muted, letterSpacing: 1, textTransform: "uppercase" }}>Clinical Interpretation</div>
            <input className="sba-input" placeholder="Suspected source" value={sourceSuspected} onChange={(e) => setSourceSuspected(e.target.value)} style={{ ...inputStyle(false), width: 160, fontFamily: sans }} />
            <button className="sba-btn" onClick={getInterpretation} disabled={interpLoading || !sofa} style={{ background: "transparent", color: C.info, border: `1px solid ${C.info}66`, borderRadius: 6, padding: "7px 14px", fontSize: 12.5, fontWeight: 600, cursor: "pointer", opacity: interpLoading || !sofa ? 0.5 : 1 }}>
              {interpLoading ? "Interpreting…" : "Get interpretation"}
            </button>
          </div>
          {interpError && <div style={{ color: C.crit, fontSize: 13 }}>{interpError}</div>}
          {interpretation && <div style={{ fontSize: 13.5, lineHeight: 1.65, whiteSpace: "pre-wrap" }}>{interpretation}</div>}
        </div>

        <div style={{ marginTop: 18, fontSize: 11, color: C.muted, textAlign: "center" }}>
          AI proposes, physician decides. All values are computed and stored server-side; this UI only displays and confirms.
        </div>
      </div>
    </div>
  );
}
