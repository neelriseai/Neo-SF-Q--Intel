"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { captureCandidateFoundation, FoundationClientError } from "@/lib/foundation-client";
import type { CandidateFoundationEvidence } from "@/lib/foundation-contract";
import { buildFoundationViewModel, foundationWords } from "@/lib/foundation-view-model";

type PanelState =
  | { kind: "IDLE" }
  | { kind: "LOADING" }
  | { kind: "READY"; evidence: CandidateFoundationEvidence }
  | { kind: "STALE" }
  | { kind: "ERROR"; code: "UNAVAILABLE" | "INVALID" };

const MAX_TIMER_DELAY = 2_147_483_647;

function shortDigest(value: string): string {
  return `${value.slice(0, 12)}…${value.slice(-8)}`;
}

export function FoundationEvidencePanel({ apiBase }: { apiBase: string }) {
  const [hydrated, setHydrated] = useState(false);
  const [state, setState] = useState<PanelState>({ kind: "IDLE" });
  const requestNumber = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const view = useMemo(
    () => state.kind === "READY" ? buildFoundationViewModel(state.evidence) : null,
    [state],
  );

  useEffect(() => {
    setHydrated(true);
    return () => controller.current?.abort();
  }, []);

  useEffect(() => {
    if (!view?.earliestExpiry) return;
    const expireIfNeeded = () => {
      if (Date.now() >= view.earliestExpiry!) setState({ kind: "STALE" });
    };
    expireIfNeeded();
    const delay = Math.min(MAX_TIMER_DELAY, Math.max(0, view.earliestExpiry - Date.now()));
    const timer = window.setTimeout(expireIfNeeded, delay);
    const onVisibility = () => {
      if (document.visibilityState === "visible") expireIfNeeded();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [view?.earliestExpiry]);

  async function capture() {
    requestNumber.current += 1;
    const ownRequest = requestNumber.current;
    controller.current?.abort();
    const nextController = new AbortController();
    controller.current = nextController;
    setState({ kind: "LOADING" });
    try {
      const evidence = await captureCandidateFoundation(apiBase, nextController.signal);
      if (requestNumber.current === ownRequest && !nextController.signal.aborted) {
        setState({ kind: "READY", evidence });
      }
    } catch (error) {
      if (nextController.signal.aborted || requestNumber.current !== ownRequest) return;
      setState({
        kind: "ERROR",
        code: error instanceof FoundationClientError && error.code === "FOUNDATION_RESPONSE_INVALID"
          ? "INVALID"
          : "UNAVAILABLE",
      });
    }
  }

  return (
    <section className="shell foundationPanel panel" aria-labelledby="foundation-heading">
      <div className="foundationHeader">
        <div className="sectionHeading compact">
          <div><span className="sectionNumber">03</span><h2 id="foundation-heading" tabIndex={-1}>Local candidate foundation</h2></div>
          <p>Fresh, host-scoped evidence from the configured local Git candidate.</p>
        </div>
        <button className="foundationButton" type="button" onClick={capture} disabled={!hydrated || state.kind === "LOADING"}>
          {!hydrated ? "Preparing…" : state.kind === "LOADING" ? "Capturing…" : state.kind === "READY" ? "Capture again" : "Capture candidate"}
        </button>
      </div>

      <div className="foundationInterlock" data-testid="foundation-interlock">
        <span>Analysis only</span><span>Evidence incomplete</span><span>Release ineligible</span>
      </div>

      <div className="foundationStatus" role="status" aria-live="polite">
        {state.kind === "IDLE" && <p>No local candidate evidence has been captured in this browser session.</p>}
        {state.kind === "LOADING" && <p>Capturing and validating a fresh local candidate projection…</p>}
        {state.kind === "READY" && <p>{state.evidence.outcome === "EXECUTED" ? "Three local foundation stages executed at their recorded capture times." : "The fresh capture abstained; failed and skipped stages remain visible below."} Analysis remains incomplete and release ineligible.</p>}
        {state.kind === "STALE" && <p>Captured evidence has expired. Capture again before using it for analysis.</p>}
        {state.kind === "ERROR" && <p className="error">{state.code === "INVALID" ? "Foundation response failed runtime validation." : "Foundation capture is unavailable."} No prior receipt remains displayed.</p>}
      </div>

      {view && <div data-testid="foundation-evidence">
        <div className="foundationSummary">
          <div><span>Candidate project</span><strong>{view.projectId}</strong></div>
          <div><span>Local execution outcome</span><strong>{view.outcome}</strong></div>
          <div><span>Foundation stages</span><strong>{view.stages.filter(({ stage }) => stage.state === "EXECUTED").length} / {view.stages.length} executed</strong></div>
          <div><span>Blocking gaps</span><strong>{view.blockingGaps.length}</strong></div>
        </div>
        <p className="foundationBoundary">This is capture-time evidence for a local Git candidate. It does not evidence a live Salesforce org, deployment, build, test execution, approval, or release readiness.</p>

        <div className="foundationStages">
          {view.stages.map(({ stage, label, evaluatedLabel, validityLabel, inputReceipts }) => <article className={`foundationStage ${stage.state.toLowerCase()}`} key={stage.stage}>
            <header>
              <div><span>Stage {stage.sequence}</span><strong>{label}</strong></div>
              <span className={`statusTag ${stage.state === "FAILED" ? "failed" : stage.state === "NOT_RUN" ? "idle" : ""}`}>{stage.state.replaceAll("_", " ")}</span>
            </header>
            <code className="capabilityId">{stage.capability_id}</code>
            <div className="foundationTiming"><span>{evaluatedLabel}</span><span>{validityLabel}</span></div>
            <div className="foundationMeasurements">
              <span>Measurements</span>
              {stage.measurements.length
                ? <dl>{stage.measurements.map((item) => <div key={item.name}><dt>{foundationWords(item.name)}</dt><dd>{item.value}</dd></div>)}</dl>
                : <p>Not reported</p>}
            </div>
            <div className="foundationReceipts">
              <span>Receipt identifiers · not proof</span>
              {inputReceipts.length ? inputReceipts.map((item) => <div key={item.role}><small>Input · {item.role}</small><code>{shortDigest(item.sha256)}</code></div>) : <p>No input receipt reported</p>}
              {stage.output_receipt
                ? <div><small>Output · {stage.output_receipt.role}</small><code>{shortDigest(stage.output_receipt.sha256)}</code></div>
                : <p>No output receipt reported</p>}
            </div>
            <details className="foundationGaps">
              <summary>{stage.gap_codes.length} blocking gap{stage.gap_codes.length === 1 ? "" : "s"}</summary>
              <ul>{stage.gap_codes.map((gap) => <li key={gap}><code>{gap}</code></li>)}</ul>
            </details>
          </article>)}
        </div>

        <div className="foundationGapRegister">
          <div><h3>Release-evidence gaps</h3><p>Every gap reported by the validated stage chain remains blocking.</p></div>
          <ul>{view.blockingGaps.map((gap) => <li key={gap}><code>{gap}</code></li>)}</ul>
        </div>
        <footer className="foundationChain">Chain identifier · not cryptographic verification <code>{shortDigest(view.chainSha256)}</code></footer>
      </div>}
    </section>
  );
}
