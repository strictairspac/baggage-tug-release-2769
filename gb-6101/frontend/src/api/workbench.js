import { request } from "./http.js";

export const api = {
  config: () => request("/workbench"),
  login: data => request("/auth/login", {method: "POST", data}),
  cases: () => request("/cases"),
  createCase: data => request("/cases", {method: "POST", data}),
  runs: () => request("/runs"),
  run: (id, signal) => request(`/runs/${id}`, {signal}),
  enqueue: (id, data) => request(`/cases/${id}/runs`, {method: "POST", data}),
  review: id => request(`/runs/${id}/review`),
  submitReview: (id, data) => request(`/runs/${id}/review`, {method: "POST", data}),
  retry: id => request(`/runs/${id}/retry`, {method: "POST"}),
  export: id => request(`/runs/${id}/export`, {raw: true}),
  backfills: caseId => request(`/cases/${caseId}/backfills`),
  submitBackfill: (caseId, data) => request(`/cases/${caseId}/backfills`, {method: "POST", data}),
  audit: () => request("/audit", {signal: AbortSignal.timeout(5000)}),
};
