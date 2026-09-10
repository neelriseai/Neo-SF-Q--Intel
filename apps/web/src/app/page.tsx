"use client";

import { FormEvent, KeyboardEvent, useMemo, useState } from "react";
import { FoundationEvidencePanel } from "@/components/foundation-evidence-panel";
import { buildRunViewModel, metricValue, type EvidenceLane } from "@/lib/run-view-model";
import type { AssuranceRun } from "@/lib/types";

const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

type ChangeIntent = "INFORMATIONAL" | "PLANNED_CHANGE" | "OBSERVED_CHANGE";
type ResultTab = "evidence" | "tests" | "healing" | "governance";

const evidenceLaneCopy: Record<EvidenceLane, { label: string; detail: string }> = {
  CONFIRMED: { label: "Confirmed", detail: "Source-confirmed evidence recorded by the run" },
  HUMAN_RECORDED: { label: "Human-recorded", detail: "Kept distinct from source-confirmed evidence" },
  NEEDS_VERIFICATION: { label: "Needs verification", detail: "Unverified, stale, or rejected evidence cannot close a gate" },
  ADVISORY: { label: "Inferred", detail: "Candidate evidence only; it cannot authorize a decision" },
  CONFLICT: { label: "Conflict", detail: "Explicitly contradictory evidence requires resolution" },
};

function words(value: string) {
  return value.replaceAll("_", " ").toLowerCase();
}

function formatPercent(value: number | null) {
  return value === null ? "No sample" : `${Math.round(value * 100)}%`;
}

function EvidenceIdList({ ids }: { ids: string[] }) {
  if (!ids.length) return <span className="mutedText">No evidence IDs cited</span>;
  return <div className="idList" aria-label="Cited evidence IDs">{ids.map((id, index) => <code key={`${id}-${index}`}>{id}</code>)}</div>;
}

export default function Dashboard() {
  const [requirement, setRequirement] = useState("");
  const [paths, setPaths] = useState("");
  const [changeIntent, setChangeIntent] = useState<ChangeIntent>("INFORMATIONAL");
  const [run, setRun] = useState<AssuranceRun | null>(null);
  const [activeTab, setActiveTab] = useState<ResultTab>("evidence");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const view = useMemo(() => buildRunViewModel(run), [run]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setRun(null);
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`${apiBase}/api/v1/assurance-runs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          requirement,
          change_intent: changeIntent,
          changed_paths: paths.split("\n").map((path) => path.trim()).filter(Boolean),
        }),
      });
      if (!response.ok) throw new Error(`Analysis failed (${response.status})`);
      setRun((await response.json()) as AssuranceRun);
      setActiveTab("evidence");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Analysis failed");
    } finally {
      setBusy(false);
    }
  }

  const totalEvidence = run?.evidence.length ?? 0;
  const blockingGapCount = view.gaps.filter((gap) => gap.blocking).length;
  const tabs: ResultTab[] = ["evidence", "tests", "healing", "governance"];

  function moveTab(event: KeyboardEvent<HTMLButtonElement>, current: ResultTab) {
    const currentIndex = tabs.indexOf(current);
    let nextIndex: number | null = null;
    if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % tabs.length;
    if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = tabs.length - 1;
    if (nextIndex === null) return;
    event.preventDefault();
    const nextTab = tabs[nextIndex];
    setActiveTab(nextTab);
    document.getElementById(`tab-${nextTab}`)?.focus();
  }

  return (
    <main>
      <header className="topbar">
        <div className="shell nav">
          <div className="brand"><span className="brandMark" aria-hidden="true">N</span><span>Neo SF Q-Intel</span></div>
          <div className="navMeta"><span className="pulse" aria-hidden="true" />Evidence-governed assurance</div>
        </div>
      </header>

      <section className="command shell" aria-labelledby="command-heading">
        <div className="commandIntro">
          <p className="eyebrow">Salesforce change assurance</p>
          <h1 id="command-heading">Trace impact. Expose gaps.<br /><span>Keep authority explicit.</span></h1>
          <p className="lede">Analyze a requirement or source change against the current evidence graph. Every result remains tied to the run that produced it.</p>
        </div>
        <aside className="authorityCard" aria-label="Release posture">
          <div className="authorityHeader"><span>Release authority</span><strong>{view.releaseAuthority.replaceAll("_", " ")}</strong></div>
          <p className="decisionValue">{view.decisionCode.replaceAll("_", " ")}</p>
          <p>{view.decisionReason}</p>
          <div className="authorityFoot"><span aria-hidden="true">◈</span> Release-authority receipt is not exposed by this run contract</div>
        </aside>
      </section>

      <section className="shell workspace" aria-label="Assurance workspace">
        <form className="composer panel" onSubmit={submit}>
          <div className="sectionHeading"><div><span className="sectionNumber">01</span><h2>Start an analysis</h2></div><p>Inputs are sent to the existing assurance-run API.</p></div>
          <label htmlFor="requirement">Requirement or observed change</label>
          <textarea id="requirement" value={requirement} onChange={(event) => { setRequirement(event.target.value); setRun(null); }} rows={4} placeholder="Describe what changed, what is planned, or what needs to be understood." />
          <div className="formRow">
            <div>
              <label htmlFor="change-intent">Intent</label>
              <select id="change-intent" value={changeIntent} onChange={(event) => { setChangeIntent(event.target.value as ChangeIntent); setRun(null); }}>
                <option value="INFORMATIONAL">Explore context only</option>
                <option value="PLANNED_CHANGE">Assess a planned change</option>
                <option value="OBSERVED_CHANGE">Assess an observed change</option>
              </select>
            </div>
            <div>
              <label htmlFor="changed-paths">Changed paths <em>optional</em></label>
              <textarea id="changed-paths" className="paths" value={paths} onChange={(event) => { setPaths(event.target.value); setRun(null); }} rows={2} placeholder="relative/path/to/source" />
            </div>
          </div>
          <button className="primaryButton" disabled={busy || requirement.trim().length < 3}><span>{busy ? "Workflow is analyzing…" : "Analyze change"}</span><span aria-hidden="true">→</span></button>
          <div className="formMessage" aria-live="polite">{error && <p className="error">{error}. Confirm the local API is available.</p>}</div>
        </form>

        <aside className="truthPanel panel" aria-labelledby="truth-heading">
          <div className="sectionHeading compact"><div><span className="sectionNumber">02</span><h2 id="truth-heading">Run truth</h2></div></div>
          <div className="truthRow">
            <div><span>Evidence linkage</span><strong>CITATIONS ONLY</strong></div>
            <p>The API exposes evidence-ID linkage, not ordered edge or path receipts.</p>
          </div>
          <div className="truthRow" data-testid="semantic-state">
            <div><span>Semantic retrieval</span><strong>{view.semanticParticipation.replaceAll("_", " ")}</strong></div>
            <p>{view.semanticDetail}</p>
          </div>
          <div className="truthRow" data-testid="advisory-state">
            <div><span>Advisory plane</span><strong>{view.advisoryCount ? "PROPOSALS PRESENT" : "NOT CONNECTED"}</strong></div>
            <p>{view.advisoryDetail}</p>
          </div>
          <div className="truthRow" data-testid="specialist-state">
            <div><span>Graph-grounded specialist</span><strong>{view.specialistParticipation.replaceAll("_", " ")}</strong></div>
            <p>{view.specialistDetail}</p>
          </div>
        </aside>
      </section>

      <FoundationEvidencePanel apiBase={apiBase} />

      <section className="shell metrics" aria-label="Run summary">
        <article className="metric panel"><span>Impact findings</span><strong>{run ? run.impacts.length : "—"}</strong><small>{run ? `${view.highRiskCount} marked high risk` : "Not evaluated"}</small></article>
        <article className="metric panel"><span>Selected validations</span><strong>{run ? run.selected_tests.length : "—"}</strong><small>{run ? `${run.test_results.length} execution receipt${run.test_results.length === 1 ? "" : "s"} reported` : "Not evaluated"}</small></article>
        <article className="metric panel"><span>Evidence items</span><strong>{run ? totalEvidence : "—"}</strong><small>{run ? `${view.confirmedCitationCount} confirmed · ${view.unverifiedCitationCount} need review` : "Not collected"}</small></article>
        <article className={`metric panel ${view.hasConflict || blockingGapCount ? "attention" : ""}`}><span>Blocking analysis gaps</span><strong>{run ? blockingGapCount : "—"}</strong><small>{run ? (view.hasConflict ? "Explicit contradiction recorded" : `${view.gaps.length} visible signals`) : "Not evaluated"}</small></article>
      </section>

      <section className="shell workflow panel" aria-labelledby="workflow-heading">
        <div className="sectionHeading"><div><span className="sectionNumber">04</span><h2 id="workflow-heading">Specialist activity</h2></div><p>Only stages recorded on this run are shown.</p></div>
        {run?.activities.length ? <ol className="activityList">{run.activities.map((activity) => <li key={activity.activity_id}><span className={`activityDot ${activity.status.toLowerCase()}`} aria-hidden="true" /><div><strong>{activity.agent}</strong><p>{activity.summary}</p><small>{words(activity.stage)} · {activity.duration_ms} ms · {activity.capability_ids.join(", ")}</small></div><span className={`statusTag ${activity.status.toLowerCase()}`}>{activity.status}</span></li>)}</ol> : <div className="emptyState"><span aria-hidden="true">○</span><p>No specialist activity is attached to this run.</p></div>}
      </section>

      <section className="shell resultPanel panel" aria-labelledby="result-heading">
        <div className="resultHeader">
          <div className="sectionHeading compact"><div><span className="sectionNumber">05</span><h2 id="result-heading">Assurance evidence</h2></div></div>
          <div className="tabs" role="tablist" aria-label="Assurance result views">
            {tabs.map((tab) => <button key={tab} type="button" role="tab" aria-selected={activeTab === tab} aria-controls={`panel-${tab}`} id={`tab-${tab}`} tabIndex={activeTab === tab ? 0 : -1} className={activeTab === tab ? "selected" : ""} onClick={() => setActiveTab(tab)} onKeyDown={(event) => moveTab(event, tab)}>{tab}</button>)}
          </div>
        </div>

        <>
          <div role="tabpanel" id="panel-evidence" aria-labelledby="tab-evidence" tabIndex={0} hidden={activeTab !== "evidence"}><div className="evidenceView">
            <div className="evidenceLanes">{(Object.keys(evidenceLaneCopy) as EvidenceLane[]).map((lane) => <article className={`evidenceLane ${lane.toLowerCase()}`} key={lane}><header><div><strong>{evidenceLaneCopy[lane].label}</strong><span>{view.citations[lane].length}</span></div><p>{evidenceLaneCopy[lane].detail}</p></header>{view.citations[lane].length ? <ul>{view.citations[lane].map((item, index) => <li key={`${item.evidenceId}-${index}`}><strong>{item.label}</strong><span>{item.kind} · {item.state}</span><code>{item.evidenceId}</code></li>)}</ul> : <p className="laneEmpty">No evidence in this lane</p>}</article>)}</div>
            <div className="impactSection"><h3>Impacts and cited evidence</h3>{view.impacts.length ? <div className="impactGrid">{view.impacts.map(({ finding, citations, missingEvidenceIds, ambiguousEvidenceIds }, index) => <article className="impactCard" key={`${finding.entity_id}-${index}`}><div className="impactTop"><span className={`risk ${finding.severity.toLowerCase()}`}>{finding.severity}</span><small>{finding.kind}</small></div><h4>{finding.label}</h4><p>{finding.relation} · {words(finding.strength_basis)}</p><div className="citationLine"><span>Citations</span><EvidenceIdList ids={citations.map((item) => item.evidenceId)} /></div>{missingEvidenceIds.length > 0 && <p className="missingCitation">Missing from run evidence: {missingEvidenceIds.join(", ")}</p>}{ambiguousEvidenceIds.length > 0 && <p className="missingCitation">Ambiguous duplicate evidence IDs withheld: {ambiguousEvidenceIds.join(", ")}</p>}</article>)}</div> : <div className="emptyState"><span aria-hidden="true">⌁</span><p>No impact finding is attached to this run.</p></div>}</div>
          </div></div>

          <div role="tabpanel" id="panel-tests" aria-labelledby="tab-tests" tabIndex={0} hidden={activeTab !== "tests"}><div className="cardsList">{run?.selected_tests.length ? run.selected_tests.map((item, index) => {
            const receipts = run.test_results.filter((result) => result.test_id === item.test_id);
            return <article className="resultCard" key={`${item.test_id}-${index}`}><div className="resultCardTitle"><div><span className="category">{item.classification}</span><h3>{item.label}</h3></div><span className={`statusTag ${receipts.length === 1 ? "reported" : receipts.length > 1 ? "failed" : "idle"}`}>{receipts.length === 1 ? "RECEIPT REPORTED" : receipts.length > 1 ? "AMBIGUOUS RECEIPTS" : "NO EXECUTION RECEIPT"}</span></div><p>{item.reason}</p><EvidenceIdList ids={item.evidence_ids} />{receipts.length === 1 && <p className="receiptNote">Reported outcome: <strong>{receipts[0].outcome}</strong>. Governance, not this view, determines whether the receipt can satisfy a gate.</p>}</article>;
          }) : <div className="emptyState"><span aria-hidden="true">□</span><p>No validation has been selected by this run.</p></div>}</div></div>

          <div role="tabpanel" id="panel-healing" aria-labelledby="tab-healing" tabIndex={0} hidden={activeTab !== "healing"}><div className="cardsList">{run?.healing_proposals.length ? run.healing_proposals.map((item, index) => <article className="resultCard" key={`${item.target_id}-${index}`}><div className="resultCardTitle"><div><span className="category">Strategy proposal</span><h3>{item.target_id}</h3></div><span className="statusTag abstained">NOT APPLIED</span></div><p>{item.strategy}</p><p className="secondaryCopy">{item.rationale}</p><EvidenceIdList ids={item.evidence_ids} /><p className="receiptNote">{item.requires_human_approval ? "Human approval required" : "No human-approval requirement is recorded"} · browser execution is not recorded by this run.</p></article>) : <div className="emptyState"><span aria-hidden="true">◇</span><p>No healing proposal is attached. Browser execution is not implied.</p></div>}</div></div>

          <div role="tabpanel" id="panel-governance" aria-labelledby="tab-governance" tabIndex={0} hidden={activeTab !== "governance"}><div className="governanceGrid">
            <div><h3>Measured controls</h3>{run?.governance?.metrics.length ? run.governance.metrics.map((metric) => { const value = metricValue(metric); return <article className="gauge" key={metric.metric}><div><span>{words(metric.metric)}</span><strong>{metric.status === "NOT_APPLICABLE" ? "N/A" : metric.status === "INSUFFICIENT_SAMPLE" ? "Insufficient sample" : formatPercent(value)}</strong></div><div className="track" aria-hidden="true"><i style={{ width: `${Math.max(0, Math.min(100, (value ?? 0) * 100))}%` }} /></div><small>{metric.numerator}/{metric.denominator} · target {metric.comparator === "AT_LEAST" ? "≥" : "≤"} {Math.round(metric.target * 100)}% · {metric.status}</small></article>; }) : <div className="emptyState"><span aria-hidden="true">◎</span><p>No governance measurement is attached.</p></div>}</div>
            <div><h3>Conflicts and gaps</h3>{view.gaps.length ? <ul className="gapList">{view.gaps.map((gap, index) => <li key={`${gap.source}-${gap.code}-${index}`} className={gap.blocking === true ? "blocking" : "observed"}><div><strong>{gap.code}</strong><span>{gap.blocking === true ? "BLOCKING" : gap.blocking === false ? "OBSERVED" : "SEVERITY NOT EXPOSED"}</span></div><p>{gap.message}</p><small>{gap.source.toLowerCase()} signal</small></li>)}</ul> : <div className="emptyState"><span aria-hidden="true">○</span><p>No analysis or activity gap is attached.</p></div>}
              {run?.governance?.guardrails.length ? <><h3>Guardrails</h3><ul className="gapList">{run.governance.guardrails.map((guardrail, index) => <li key={`${guardrail.control_id}-${index}`} className={guardrail.blocking ? "blocking" : "observed"}><div><strong>{guardrail.control_id}</strong><span>{guardrail.outcome}</span></div><p>{guardrail.reason_code}</p><small>{guardrail.stage} · {guardrail.blocking ? "blocking" : "nonblocking"}</small></li>)}</ul></> : null}
              {run?.governance?.violations.length ? <><h3>Violations</h3><ul className="gapList">{run.governance.violations.map((violation, index) => <li className="blocking" key={`${violation}-${index}`}><div><strong>Governance violation</strong><span>RECORDED</span></div><p>{violation}</p></li>)}</ul></> : null}
              {run?.decision && <aside className="currentDecision"><span>Current API decision</span><strong>{run.decision.code.replaceAll("_", " ")}</strong><ul>{run.decision.reasons.map((reason, index) => <li key={`${reason}-${index}`}>{reason}</li>)}</ul><EvidenceIdList ids={run.decision.evidence_ids} /></aside>}
              {run?.recorded_decision && <aside className="auditDecision"><span>Recorded decision · audit only</span><strong>{run.recorded_decision.code.replaceAll("_", " ")}</strong><p>Historical posture is not the current effective decision.</p></aside>}
            </div>
          </div></div>
        </>
        {run && <footer className="trace"><span>Run <code>{run.run_id}</code></span><span>Trace <code>{run.trace_id}</code></span><time dateTime={run.created_at}>{new Date(run.created_at).toLocaleString()}</time></footer>}
      </section>
    </main>
  );
}
