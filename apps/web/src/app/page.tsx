"use client";

import { FormEvent, useState } from "react";
import type { AssuranceRun, DecisionCode } from "@/lib/types";

const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

const demoRequirement =
  "Assess changes to an approval policy, permission set, and Lightning record-page layout";

function decisionLabel(code?: DecisionCode) {
  return code?.replaceAll("_", " ") ?? "AWAITING RUN";
}

function ratio(numerator: number, denominator: number) {
  return denominator ? Math.round((numerator / denominator) * 100) : null;
}

export default function Dashboard() {
  const [requirement, setRequirement] = useState(demoRequirement);
  const [paths, setPaths] = useState("");
  const [changeIntent, setChangeIntent] = useState<"INFORMATIONAL" | "PLANNED_CHANGE" | "OBSERVED_CHANGE">("INFORMATIONAL");
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
          change_intent: changeIntent,
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

  const decision = run?.decision?.code ?? (run?.status === "FAILED" ? "INCOMPLETE" : undefined);
  const highRisk = run?.impacts.filter((impact) => impact.severity === "HIGH").length ?? 0;

  return (
    <main>
      <nav className="nav shell">
        <div className="brand"><span className="brandMark">N</span><span>Neo SF Q-Intel</span></div>
        <div className="navMeta"><span className="pulse" /> Evidence-governed workflow</div>
      </nav>

      <section className="hero shell">
        <div>
          <p className="eyebrow">Salesforce change assurance</p>
          <h1>Know the blast radius<br /><span>before it reaches production.</span></h1>
          <p className="lede">Four deterministic specialist stages turn source metadata into traceable impact, test, healing-strategy, and governance artifacts.</p>
        </div>
        <div className={`decisionCard ${decision?.toLowerCase() ?? "idle"}`}>
          <p>Release posture</p>
          <strong>{decisionLabel(decision)}</strong>
          <span>{run?.decision?.reasons[0] ?? (run?.status === "FAILED" ? "A workflow stage failed before trusted output was available." : "Run an analysis to establish an evidence-backed posture.")}</span>
        </div>
      </section>

      <section className="workspace shell">
        <form className="composer panel" onSubmit={submit}>
          <div className="panelTitle"><span>01</span><div><h2>Describe the change</h2><p>Plain language, source paths, or both</p></div></div>
          <label htmlFor="requirement">Requirement</label>
          <textarea id="requirement" value={requirement} onChange={(event) => setRequirement(event.target.value)} rows={4} />
          <label htmlFor="change-intent">Intent</label>
          <select id="change-intent" value={changeIntent} onChange={(event) => setChangeIntent(event.target.value as typeof changeIntent)}>
            <option value="INFORMATIONAL">Explore context only</option>
            <option value="PLANNED_CHANGE">Assess a planned change</option>
            <option value="OBSERVED_CHANGE">Assess a verified observed change</option>
          </select>
          <label htmlFor="changed-paths">Changed paths <em>optional · one per line</em></label>
          <textarea id="changed-paths" className="paths" value={paths} onChange={(event) => setPaths(event.target.value)} rows={3} placeholder="force-app/main/default/..." />
          <button disabled={busy || requirement.trim().length < 3}>{busy ? "Workflow is analyzing…" : "Analyze change"}<span>→</span></button>
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
          <div className="panelTitle"><span>02</span><div><h2>Specialist workflow stages</h2><p>Bounded roles, shared typed state, traceable stage summaries</p></div></div>
          <div className="agentFlow">
            {(run?.activities ?? [
              { activity_id: "ready-analysis", capability_ids: ["reasoning.graph-impact"], agent: "Change Analyst", stage: "change_analysis", summary: "Maps dependencies", status: "ABSTAINED", started_at: "", completed_at: "", duration_ms: 0, input_evidence_ids: [], output_artifact_ids: [], policy_refs: [], gap_codes: [], error_class: null },
              { activity_id: "ready-tests", capability_ids: ["quality.test-selection"], agent: "Test Intelligence", stage: "test_selection", summary: "Selects validations", status: "ABSTAINED", started_at: "", completed_at: "", duration_ms: 0, input_evidence_ids: [], output_artifact_ids: [], policy_refs: [], gap_codes: [], error_class: null },
              { activity_id: "ready-healing", capability_ids: ["automation.locator-healing"], agent: "UI Healing", stage: "healing_strategy", summary: "Proposes a strategy; browser execution is a later capability", status: "ABSTAINED", started_at: "", completed_at: "", duration_ms: 0, input_evidence_ids: [], output_artifact_ids: [], policy_refs: [], gap_codes: [], error_class: null },
              { activity_id: "ready-governance", capability_ids: ["governance.release-decision"], agent: "Governance Review", stage: "release_governance", summary: "Applies release gates", status: "ABSTAINED", started_at: "", completed_at: "", duration_ms: 0, input_evidence_ids: [], output_artifact_ids: [], policy_refs: [], gap_codes: [], error_class: null },
            ]).map((activity, index) => (
              <div className="agent" key={activity.activity_id}>
                <div className={`agentIcon ${run ? "active" : ""}`}>{index + 1}</div>
                <div><strong>{activity.agent}</strong><p>{activity.summary}</p>{run && <small>{activity.stage.replaceAll("_", " ")} · {activity.duration_ms} ms</small>}</div>
                <span className="agentStatus">{run ? activity.status : "READY"}</span>
              </div>
            ))}
          </div>
        </article>

        <article className="panel governance">
          <div className="panelTitle"><span>03</span><div><h2>Governance signals</h2><p>Measured before narrative</p></div></div>
          {(run?.governance?.metrics ?? []).map((metric) => {
            const value = ratio(metric.numerator, metric.denominator);
            const display = metric.status === "NOT_APPLICABLE" ? "N/A" : metric.status === "INSUFFICIENT_SAMPLE" ? "INSUFFICIENT SAMPLE" : value === null ? "NO POPULATION" : `${value}%`;
            return <div className="gauge" key={metric.metric}><div><span>{metric.metric.replaceAll("_", " ")}</span><strong>{display}</strong></div><div className="track"><i style={{ width: `${value ?? 0}%` }} /></div><small>{metric.numerator}/{metric.denominator} · Target {metric.comparator === "AT_LEAST" ? "≥" : "≤"} {metric.target * 100}% · minimum {metric.minimum_sample_size}</small></div>;
          })}
          {!run && <div className="emptyState"><span>◎</span><p>Coverage, grounding, and policy gates appear after analysis.</p></div>}
        </article>
      </section>

      <section className="shell panel findings">
        <div className="panelTitle"><span>04</span><div><h2>Impact & evidence</h2><p>Every material finding links back to source</p></div></div>
        {run?.impacts.length ? <div className="tableWrap"><table><thead><tr><th>Entity</th><th>Type</th><th>Relationship</th><th>Risk</th><th>Evidence basis</th></tr></thead><tbody>{run.impacts.slice(0, 12).map((impact) => <tr key={impact.entity_id}><td><strong>{impact.label}</strong><small>{impact.entity_id}</small></td><td>{impact.kind}</td><td>{impact.relation}</td><td><span className={`risk ${impact.severity.toLowerCase()}`}>{impact.severity}</span></td><td>{impact.strength_basis.replaceAll("_", " ")}</td></tr>)}</tbody></table></div> : <div className="emptyState horizontal"><span>⌁</span><p>No inferred impact is shown until the evidence graph supports it.</p></div>}
        {run && <footer className="trace">Trace <code>{run.trace_id}</code><span>{new Date(run.created_at).toLocaleString()}</span></footer>}
      </section>
    </main>
  );
}
