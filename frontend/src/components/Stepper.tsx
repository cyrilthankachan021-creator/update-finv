import { useStepper } from "../lib/navigation";

/** Back and Next for a step flow. Back is disabled on the first step; on the last step Next becomes Finish. Never wraps. */
export function StepNav({ stepper, onFinish, finishLabel = "Finish", nextLabel = "Next", canAdvance = true }: {
  stepper: ReturnType<typeof useStepper>; onFinish: () => void; finishLabel?: string; nextLabel?: string; canAdvance?: boolean;
}) {
  return (
    <div className="row" role="group" aria-label="Steps">
      <button type="button" className="btn" disabled={stepper.isFirst} onClick={stepper.back}>Back</button>
      <span className="muted" aria-live="polite">Step {stepper.index + 1} of {stepper.total}</span>
      <button type="button" className="btn primary" disabled={!canAdvance} onClick={stepper.isLast ? onFinish : stepper.next}>
        {stepper.isLast ? finishLabel : nextLabel}
      </button>
    </div>
  );
}
