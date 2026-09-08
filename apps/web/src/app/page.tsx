"use client";

import { FormEvent, useState } from "react";
import type { AssuranceRun, DecisionCode } from "@/lib/types";

const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

const demoRequirement =
  "Assess changing the strategic discount policy and the Deal Workbench layout";

function decisionLabel(code?: DecisionCode) {
  return code?.replaceAll("_", " ") ?? "AWAITING RUN";
}

function ratio(numerator: number, denominator: number) {
  return Math.round((numerator / Math.max(1, denominator)) * 100);
}

export default function Dashboard() {
  const [requirement, setRequirement] = useState(demoRequirement);
  const [paths, setPaths] = useState("");
  const [run, setRun] = useState<AssuranceRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`${apiBase}/api/v1/assurance-runs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          requirement,
          changed_paths: paths
            .split("\n")
            .map((path) => path.trim())
            .filter(Boolean),
        }),
      });
      if (!response.ok) throw new Error(`Analysis failed (${response.status})`);
      setRun((await response.json()) as AssuranceRun);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Analysis failed");
    } finally {
      setBusy(false);
    }
  }

  const decision = run?.decision?.code;
  const highRisk = run?.impacts.filter((impact) => impact.severity === "HIGH").length ?? 0;

  return (
    <main>
      <nav className="nav shell">
        <div className="brand"><span className="brandMark">N</span><span>Neo SF Q-Intel</span></div>
        <div className="navMeta"><span className="pulse" /> Evidence engine online</div>
      </nav>

      <section className="hero shell">
        <div>
          <p className="eyebrow">Salesforce change assurance</p>
          <h1>Know the blast radius<br /><span>before it reaches production.</span></h1>
          <p className="lede">Four cooperating agents turn source metadata into traceable impact, test, healing, and governance decisions.</p>
        </div>
        <div className={`decisionCard ${decision?.toLowerCase() ?? "idle"}`}>
          <p>Release posture</p>
          <strong>{decisionLabel(decision)}</strong>
          <span>{run?.decision?.reasons[0] ?? "Run an analysis to establish an evidence-backed posture."}</span>
        </div>
      </section>

      <section className="workspace shell">
        <form className="composer panel" onSubmit={submit}>
          <div className="panelTitle"><span>01</span><div><h2>Describe the change</h2><p>Plain language, source paths, or both</p></div></div>
          <label>Requirement</label>
          <textarea value={requirement} onChange={(event) => setRequirement(event.target.value)} rows={4} />
          <label>Changed paths <em>optional · one per line</em></label>
          <textarea className="paths" value={paths} onChange={(event) => setPaths(event.target.value)} rows={3} placeholder="force-app/main/default/..." />
          <button disabled={busy || requirement.trim().length < 3}>{busy ? "Agents are reasoning…" : "Analyze change"}<span>→</span></button>
          {error && <p className="error">{error}. Confirm the API is running on port 8000.</p>}
        </form>

        <div className="summaryGrid">
          <article className="metric panel"><span>Impacted entities</span><strong>{run?.impacts.length ?? "—"}</strong><small>{highRisk} high risk</small></article>
          <article className="metric panel"><span>Selected tests</span><strong>{run?.selected_tests.length ?? "—"}</strong><small>graph connected</small></article>
          <article className="metric panel"><span>Healing proposals</span><strong>{run?.healing_proposals.length ?? "—"}</strong><small>approval gated</small></article>
          <article className="metric panel"><span>Evidence items</span><strong>{run?.evidence.length ?? "—"}</strong><small>snapshot grounded</small></article>
        </div>
      </section>

      <section className="shell lowerGrid">
        <article className="panel agents">
          <div className="panelTitle"><span>02</span><div><h2>Cooperating agents</h2><p>Bounded roles, shared typed state</p></div></div>
          <div className="agentFlow">
            {(run?.activities ?? [
              { agent: "Change Analyst", summary: "Maps dependencies", status: "ABSTAINED", evidence_ids: [] },
              { agent: "Test Intelligence", summary: "Selects validations", status: "ABSTAINED", evidence_ids: [] },
              { agent: "UI Healing", summary: "Proposes recovery", status: "ABSTAINED", evidence_ids: [] },
              { agent: "Governance Review", summary: "Applies release gates", status: "ABSTAINED", evidence_ids: [] },
            ]).map((activity, index) => (
              <div className="agent" key={activity.agent}>
                <div className={`agentIcon ${run ? "active" : ""}`}>{index + 1}</div>
                <div><strong>{activity.agent}</strong><p>{activity.summary}</p></div>
                <span className="agentStatus">{run ? activity.status : "READY"}</span>
              </div>
            ))}
          </div>
        </article>

        <article className="panel governance">
          <div className="panelTitle"><span>03</span><div><h2>Governance signals</h2><p>Measured before narrative</p></div></div>
          {(run?.governance?.metrics ?? []).map((metric) => {
            const value = ratio(metric.numerator, metric.denominator);
            return <div className="gauge" key={metric.metric}><div><span>{metric.metric.replaceAll("_", " ")}</span><strong>{value}%</strong></div><div className="track"><i style={{ width: `${value}%` }} /></div><small>Target {metric.target * 100}%</small></div>;
          })}
          {!run && <div className="emptyState"><span>◎</span><p>Coverage, grounding, and policy gates appear after analysis.</p></div>}
        </article>
      </section>

      <section className="shell panel findings">
        <div className="panelTitle"><span>04</span><div><h2>Impact & evidence</h2><p>Every material finding links back to source</p></div></div>
        {run?.impacts.length ? <div className="tableWrap"><table><thead><tr><th>Entity</th><th>Type</th><th>Relationship</th><th>Risk</th><th>Confidence</th></tr></thead><tbody>{run.impacts.slice(0, 12).map((impact) => <tr key={impact.entity_id}><td><strong>{impact.label}</strong><small>{impact.entity_id}</small></td><td>{impact.kind}</td><td>{impact.relation}</td><td><span className={`risk ${impact.severity.toLowerCase()}`}>{impact.severity}</span></td><td>{Math.round(impact.confidence * 100)}%</td></tr>)}</tbody></table></div> : <div className="emptyState horizontal"><span>⌁</span><p>No inferred impact is shown until the evidence graph supports it.</p></div>}
        {run && <footer className="trace">Trace <code>{run.trace_id}</code><span>{new Date(run.created_at).toLocaleString()}</span></footer>}
      </section>
    </main>
  );
}
