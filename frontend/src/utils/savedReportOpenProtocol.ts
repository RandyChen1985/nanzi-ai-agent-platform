export interface SavedReportOpenTarget {
  report_id: string;
  run_id: string;
}

export interface SavedReportOpenRequest extends SavedReportOpenTarget {
  request_id: string;
}

interface SavedReportOpenTargetInput {
  report_id: string | number;
  run_id?: string | number | null;
  request_id?: string | number | null;
}

type SavedReportOpenDispatch = (event: CustomEvent<SavedReportOpenRequest>) => unknown;

export const SAVED_REPORT_OPEN_EVENT = "nanzi:open-saved-report";
let requestSequence = 0;

export const createSavedReportOpenRequest = (
  target: SavedReportOpenTargetInput,
): SavedReportOpenRequest => ({
  report_id: String(target.report_id),
  run_id: String(target.run_id ?? ""),
  request_id: String(
    target.request_id ?? `saved-report-${Date.now().toString(36)}-${++requestSequence}`,
  ),
});

export const createSavedReportOpenMessage = (target: SavedReportOpenTargetInput) => ({
  type: "OPEN_SAVED_REPORT" as const,
  open_saved_report: createSavedReportOpenRequest(target),
});

export const publishSavedReportOpenRequest = (
  target: SavedReportOpenTargetInput,
  dispatch: SavedReportOpenDispatch = event => window.dispatchEvent(event),
) => dispatch(new CustomEvent(SAVED_REPORT_OPEN_EVENT, {
  detail: createSavedReportOpenRequest(target),
}));
