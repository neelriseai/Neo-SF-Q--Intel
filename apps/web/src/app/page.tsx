"use client";

import { FormEvent, KeyboardEvent, useMemo, useState } from "react";
import { FoundationEvidencePanel } from "@/components/foundation-evidence-panel";
import { analyzeCurrentCandidate, getCandidateRun } from "@/lib/candidate-client";
import { getLiveCampaignStatus } from "@/lib/live-campaign-client";
import { buildRunViewModel, metricValue, type EvidenceLane } from "@/lib/run-view-model";
import type {
  AssuranceRun,
  CandidateAssuranceView,
  CandidateSideAssuranceView,
} from "@/lib/types";
import type { LiveCampaignStatus } from "@/lib/live-campaign-client";

const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

type ChangeIntent = "INFORMATIONAL" | "PLANNED_CHANGE" | "OBSERVED_CHANGE";
type ResultTab = "evidence" | "tests" | "healing" | "governance";
type WorkspaceView = {
  title: string;
  role: string;
  description: string;
  target: string;
  tab?: ResultTab;
  symbol: string;
};

const evidenceLaneCopy: Record<EvidenceLane, { label: string; detail: string }> = {
  CONFIRMED: { label: "Confirmed", detail: "Source-confirmed evidence recorded by the run" },
  HUMAN_RECORDED: { label: "Human-recorded", detail: "Kept distinct from source-confirmed evidence" },
  NEEDS_VERIFICATION: { label: "Needs verification", detail: "Unverified, stale, or rejected evidence cannot close a gate" },
  ADVISORY: { label: "Inferred", detail: "Candidate evidence only; it cannot authorize a decision" },
  CONFLICT: { label: "Conflict", detail: "Explicitly contradictory evidence requires resolution" },
};

const workspaceViews: WorkspaceView[] = [
  { title: "Source Evidence", role: "Source analyst", description: "Shows bounded local candidate capture and preserved source provenance.", target: "foundation-heading", symbol: "S" },
  { title: "Knowledge Context", role: "Graph specialist", description: "Opens typed relationship and cited-evidence context for analysis.", target: "foundation-heading", symbol: "K" },
  { title: "Impact Analysis", role: "Impact analyst", description: "Shows affected entities without presenting inferred evidence as authority.", target: "result-heading", tab: "evidence", symbol: "I" },
  { title: "Validation Selection", role: "Test specialist", description: "Shows selected validations and their independently reported receipts.", target: "result-heading", tab: "tests", symbol: "T" },
  { title: "Locator Healing", role: "Healing specialist", description: "Shows governed repair proposals without implying they were applied.", target: "result-heading", tab: "healing", symbol: "H" },
  { title: "Governance Review", role: "Governance reviewer", description: "Shows measurements, guardrails, gaps, and authority boundaries together.", target: "result-heading", tab: "governance", symbol: "G" },
];

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
  const [candidateView, setCandidateView] = useState<CandidateAssuranceView | null>(null);
  const [activeTab, setActiveTab] = useState<ResultTab>("evidence");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [campaignId, setCampaignId] = useState("");
  const [liveStatus, setLiveStatus] = useState<LiveCampaignStatus | null>(null);
  const [liveStatusBusy, setLiveStatusBusy] = useState(false);
  const [liveStatusError, setLiveStatusError] = useState("");
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

  async function analyzeCandidate() {
    setBusy(true);
    setError("");
    try {
      const result = await analyzeCurrentCandidate(apiBase);
      setCandidateView(result.view);
      setRun(result.run);
      setActiveTab("evidence");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Candidate analysis failed");
    } finally {
      setBusy(false);
    }
  }

  async function selectCandidateSide(side: CandidateSideAssuranceView) {
    if (!candidateView || busy) return;
    setBusy(true);
    setError("");
    try {
      setRun(await getCandidateRun(apiBase, candidateView, side));
      setActiveTab("evidence");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Candidate run retrieval failed");
    } finally {
      setBusy(false);
    }
  }

  async function replayLiveCampaign(event: FormEvent) {
    event.preventDefault();
    setLiveStatus(null);
    setLiveStatusError("");
    setLiveStatusBusy(true);
    try {
      setLiveStatus(await getLiveCampaignStatus(apiBase, campaignId.trim()));
    } catch (caught) {
      setLiveStatusError(caught instanceof Error ? caught.message : "LIVE_STATUS_UNAVAILABLE");
    } finally {
      setLiveStatusBusy(false);
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

  function openCapability(target: string, tab?: ResultTab) {
    if (tab) setActiveTab(tab);
    window.requestAnimationFrame(() => {
      const destination = document.getElementById(target);
      const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      destination?.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
      destination?.focus({ preventScroll: true });
    });
  }

  return (
    <div className="appShell">
      <aside className="sideRail" aria-label="Workspace navigation">
        <div className="brand"><span className="brandMark" aria-hidden="true">N</span><span><strong>Neo</strong><small>SF Q-Intel</small></span></div>
        <nav className="sideNav" aria-label="Assurance sections">
          <a className="active" href="#command-heading"><span aria-hidden="true">⌂</span>Workspace</a>
          <a href="#capability-catalog"><span aria-hidden="true">⌘</span>Specialist views</a>
          <a href="#foundation-heading"><span aria-hidden="true">◇</span>Candidate evidence</a>
          <a href="#result-heading"><span aria-hidden="true">▤</span>Assurance results</a>
        </nav>
        <div className="railGroup">
          <p>Workspace areas</p>
          <a href="#capability-catalog">Source &amp; metadata</a>
          <a href="#capability-catalog">Graph reasoning</a>
          <a href="#capability-catalog">Testing &amp; healing</a>
          <a href="#capability-catalog">Governance</a>
        </div>
        <div className="railFoot"><span className="pulse" aria-hidden="true" /><div><strong>Local workspace</strong><small>Evidence-governed</small></div></div>
      </aside>

      <main className="contentCanvas">
        <header className="topbar">
          <div>
            <p className="breadcrumb">Assurance workspace <span>/</span> Change intelligence</p>
            <h1 id="command-heading" tabIndex={-1}>Change assurance</h1>
          </div>
          <aside className="authorityPill" aria-label="Release posture">
            <span>Release posture</span>
            <strong>{view.decisionCode.replaceAll("_", " ")}</strong>
            <small>{view.releaseAuthority.replaceAll("_", " ")}</small>
            <p>{view.decisionReason}</p>
          </aside>
        </header>

        <section className="stageBand" aria-labelledby="stage-heading">
          <div className="stageBandHeader"><div><span className="eyebrow">Recorded workflow</span><h2 id="stage-heading">Cooperating agent activity</h2></div><span>{run?.activities.length ?? 0} recorded</span></div>
          {run?.activities.length ? (
            <ol className="stageRail">
              {run.activities.map((activity, index) => <li key={activity.activity_id}>
                <span className="stageIndex">{index + 1}</span>
                <div><strong>{activity.agent}</strong><small>{words(activity.stage)}</small></div>
                <span className={`stageState ${activity.status.toLowerCase()}`}>{activity.status}</span>
              </li>)}
            </ol>
          ) : <div className="stageRailEmpty"><span aria-hidden="true">◎</span><p>Agent stages appear here only when they are recorded by an assurance run.</p></div>}
        </section>

        <section className="workspace" aria-label="Assurance workspace">
          <aside className="conversationPanel panel" aria-label="Orchestrator conversation">
            <div className="conversationHeading"><span aria-hidden="true">✦</span><div><strong>Assurance orchestrator</strong><small>{run ? "Run evidence received" : "Ready for input"}</small></div></div>
            <div className="assistantMessage">Describe a requirement or observed source change. The orchestrator will coordinate only the capabilities recorded by the run.</div>
            <div className="conversationEmpty"><span aria-hidden="true">▣</span><strong>{run ? "Run recorded" : "Input required"}</strong><p>{run ? "Review the agent activity and evidence panels below." : "Complete the workspace form to begin."}</p></div>
          </aside>

          <form className="composer panel" onSubmit={submit}>
            <div className="panelTitle"><div className="panelGlyph" aria-hidden="true">A</div><div><h2>Analyze a change</h2><p>Provide a requirement, feature description, or observed source change.</p></div><span className="readyTag">{busy ? "Analyzing" : "Ready"}</span></div>
            <label htmlFor="requirement">Requirement or observed change</label>
            <textarea id="requirement" value={requirement} onChange={(event) => { setRequirement(event.target.value); setRun(null); }} rows={5} placeholder="Describe what changed, what is planned, or what needs to be understood." />
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
            <div className="candidateAction">
              <button type="button" onClick={analyzeCandidate} disabled={busy}>Analyze current Git candidate</button>
              <p>Host-owned capture; no repository, path, branch, or seed scope is accepted from this screen.</p>
            </div>
            {candidateView && <div className="candidateSides" aria-label="Verified candidate graph sides">
              {candidateView.analyses.map((analysis) => <button
                type="button"
                key={analysis.side}
                className={run?.run_id === analysis.run_id ? "selected" : ""}
                onClick={() => void selectCandidateSide(analysis)}
              >{analysis.side} · {analysis.operation_scope.join(" + ")}</button>)}
              <span>Analysis only · {candidateView.blocking_gap_codes.length} release gaps retained</span>
            </div>}
            <div className="formMessage" aria-live="polite">{error && <p className="error">{error}. Confirm the local API is available.</p>}</div>
          </form>
        </section>

        <section className="capabilitySection" id="capability-catalog" aria-labelledby="capability-heading">
          <div className="catalogHeader"><div><span className="eyebrow">Workspace views</span><h2 id="capability-heading">Specialist views</h2></div><p>These cards navigate existing interface views; agent participation appears only in recorded run activity.</p></div>
          <div className="capabilityGrid">
            {workspaceViews.map((viewCard) => <article className="capabilityCard" key={viewCard.title}>
              <div className="capabilityTop"><span className="capabilityIcon" aria-hidden="true">{viewCard.symbol}</span><span className="roleTag">{viewCard.role}</span></div>
              <h3>{viewCard.title}</h3>
              <p>{viewCard.description}</p>
              <button type="button" onClick={() => openCapability(viewCard.target, viewCard.tab)}>Open {viewCard.title} view <span aria-hidden="true">→</span></button>
            </article>)}
          </div>
        </section>

        <section className="truthPanel panel" aria-labelledby="truth-heading">
          <div className="sectionHeading compact"><div><span className="sectionNumber">01</span><h2 id="truth-heading">Run truth</h2></div><p>Authority and participation stay evidence-bound.</p></div>
          <div className="truthGrid">
            <div className="truthRow"><div><span>Evidence linkage</span><strong>CITATIONS ONLY</strong></div><p>The API exposes evidence-ID linkage, not ordered edge or path receipts.</p></div>
            <div className="truthRow" data-testid="semantic-state"><div><span>Semantic retrieval</span><strong>{view.semanticParticipation.replaceAll("_", " ")}</strong></div><p>{view.semanticDetail}</p></div>
            <div className="truthRow" data-testid="advisory-state"><div><span>Advisory plane</span><strong>{view.advisoryCount ? "PROPOSALS PRESENT" : "NOT CONNECTED"}</strong></div><p>{view.advisoryDetail}</p></div>
            <div className="truthRow" data-testid="specialist-state"><div><span>Graph-grounded specialist</span><strong>{view.specialistParticipation.replaceAll("_", " ")}</strong></div><p>{view.specialistDetail}</p></div>
          </div>
        </section>

        <section className="liveCampaignPanel panel" aria-labelledby="live-campaign-heading">
          <div className="sectionHeading compact">
            <div><span className="sectionNumber">LIVE</span><h2 id="live-campaign-heading">Salesforce campaign evidence</h2></div>
            <p>Replays durable receipts only. This view cannot run Salesforce or grant authority.</p>
          </div>
          <form className="liveCampaignLookup" onSubmit={replayLiveCampaign}>
            <label htmlFor="campaign-id">Campaign ID</label>
            <div><input id="campaign-id" value={campaignId} onChange={(event) => setCampaignId(event.target.value)} placeholder="Enter an issued campaign ID" /><button type="submit" disabled={liveStatusBusy || campaignId.trim().length < 1}>{liveStatusBusy ? "Replaying…" : "Replay evidence"}</button></div>
          </form>
          <div className="formMessage" aria-live="polite">{liveStatusError && <p className="error">Live evidence is unavailable ({liveStatusError}).</p>}</div>
          {liveStatus ? <div className="liveCampaignSummary">
            <article><span>Accepted gates</span><strong>{liveStatus.accepted_completion_numerator}/{liveStatus.completion_denominator}</strong><small>Acceptance remains independent of locally valid receipts.</small></article>
            <article><span>Replay state</span><strong>{words(liveStatus.replay_state)}</strong><small>{liveStatus.receipt_count} durable receipt{liveStatus.receipt_count === 1 ? "" : "s"} · {liveStatus.ledger_mode}</small></article>
            <article><span>Release eligibility</span><strong>NOT ELIGIBLE</strong><small>{liveStatus.requirements_satisfied ? "Requirements reported complete" : "Required live evidence is incomplete"}</small></article>
            <article className={liveStatus.quarantined ? "attention" : ""}><span>Campaign safety</span><strong>{liveStatus.quarantined ? "QUARANTINED" : "NOT QUARANTINED"}</strong><small>{liveStatus.gap_codes.length} current gap code{liveStatus.gap_codes.length === 1 ? "" : "s"}</small></article>
          </div> : <div className="liveCampaignEmpty"><span aria-hidden="true">◎</span><p>No campaign has been replayed in this view.</p></div>}
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
          <div className="sectionHeading compact"><div><span className="sectionNumber">05</span><h2 id="result-heading" tabIndex={-1}>Assurance evidence</h2></div></div>
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
    </div>
  );
}
