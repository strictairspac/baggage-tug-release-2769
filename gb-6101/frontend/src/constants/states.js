export const States = Object.freeze({
  queued: "等待执行", recomputing: "补登重算中", done: "已完成", failed: "执行失败",
});

export const Actions = Object.freeze({
  create_case: "登记资料", enqueue: "提交执行", done: "执行完成", failed: "执行失败",
  review: "复核签署", backfill: "补登回执", recompute: "触发重算", recomputed: "重算完成",
  retry: "失败续跑",
});
