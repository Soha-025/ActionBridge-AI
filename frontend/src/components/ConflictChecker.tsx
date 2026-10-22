/**
 * ConflictChecker — container for the conflicts panel (Member 5).
 *
 * `GET /api/conflicts` is contextual: it requires `commitment_id` + `new_due_date`
 * and answers "what pressure would this move create?". The backend has no endpoint
 * that lists conflicts for the plan as it stands, so this container stays silent
 * until both values are real — and nothing here invents a global conflicts list
 * just to make a pre-fetch look honest.
 *
 * Member 1's contract in `types.ts` (`ConflictsPanelProps { conflicts }`) says the
 * panel must not fetch. So this file owns everything the panel must not own —
 * picker state, the one request, and its abort lifecycle — and hands the results
 * down as plain props. `ConflictsPanel` only renders them.
 *
 * Detection never resolves anything: a conflict stays unresolved until the
 * Assistant's proposal is confirmed through the `/api/changes` flow.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  Commitment,
  Conflict,
  ConflictsQuery,
  ConflictsResponse,
  PlanItem,
} from "../types";
import { addDaysIso, isIsoDate } from "../format";
import ConflictsPanel from "./ConflictsPanel";

export interface ConflictCheckerProps {
  /** Active commitments — the picker's options and the "moved" wording. */
  commitments: Commitment[];
  /** Full plan, forwarded so rows can be matched to their plan steps. */
  plan: PlanItem[];
  /** Reference date: seeds the suggested target date. */
  today: string;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  /** Injected so the same view works against the real API or the demo source. */
  getConflicts: (params: ConflictsQuery, signal?: AbortSignal) => Promise<ConflictsResponse>;
}

type CheckResult =
  | { status: "idle" }
  | { status: "ok"; conflicts: Conflict[]; checked: ConflictsQuery }
  | { status: "error"; message: string };

export default function ConflictChecker({
  commitments,
  plan,
  today,
  loading,
  error,
  onRetry,
  getConflicts,
}: ConflictCheckerProps) {
  const [selectedId, setSelectedId] = useState("");
  const [targetDate, setTargetDate] = useState("");
  const [checking, setChecking] = useState(false);
  const [result, setResult] = useState<CheckResult>({ status: "idle" });
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => () => controllerRef.current?.abort(), []);

  const selected = useMemo(
    () => commitments.find((item) => String(item.id) === selectedId) ?? null,
    [commitments, selectedId],
  );

  /** Keep the target date blank until the user picks one; suggest +3 days. */
  const suggestedDate = useMemo(() => addDaysIso(today, 3), [today]);

  const canRun = selectedId !== "" && isIsoDate(targetDate);

  const runCheck = useCallback(async () => {
    const commitmentId = Number(selectedId);
    if (!commitmentId || !isIsoDate(targetDate)) {
      setResult({
        status: "error",
        message: "Pick a commitment and enter a valid target date (YYYY-MM-DD) before checking.",
      });
      return;
    }
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setChecking(true);
    try {
      const response = await getConflicts(
        { commitment_id: commitmentId, new_due_date: targetDate },
        controller.signal,
      );
      if (controller.signal.aborted) return;
      setResult({
        status: "ok",
        conflicts: response.conflicts ?? [],
        checked: { commitment_id: commitmentId, new_due_date: targetDate },
      });
    } catch (err) {
      if (controller.signal.aborted) return;
      setResult({
        status: "error",
        message: err instanceof Error ? err.message : "The conflict check failed.",
      });
    } finally {
      setChecking(false);
    }
  }, [selectedId, targetDate, getConflicts]);

  return (
    <ConflictsPanel
      conflicts={result.status === "ok" ? result.conflicts : []}
      checked={result.status === "ok" ? result.checked : null}
      checkError={result.status === "error" ? result.message : null}
      commitments={commitments}
      plan={plan}
      loading={loading}
      error={error}
      onRetry={onRetry}
      selectedId={selectedId}
      targetDate={targetDate}
      selected={selected}
      suggestedDate={suggestedDate}
      canRun={canRun}
      checking={checking}
      onSelectChange={setSelectedId}
      onTargetDateChange={setTargetDate}
      onRunCheck={() => {
        void runCheck();
      }}
    />
  );
}
