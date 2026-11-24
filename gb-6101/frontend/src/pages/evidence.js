import { api } from "../api/workbench.js";
import { errorText, field } from "../components/common/fields.js";
import { empty, pageHeading, section } from "../components/common/panel.js";
import { resultTable, table } from "../components/common/table.js";
import { status } from "../components/common/status.js";
import { poll } from "../hooks/poll.js";
import { submit } from "../hooks/submit.js";
import { el } from "../utils/dom.js";
import { cell, download, time } from "../utils/format.js";

const BLOCKED = new Set(["queued", "recomputing", "failed"]);
const PENDING = new Set(["queued", "recomputing"]);

function minuteLabel(value) {
  return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
}

function reviewForm(id, saved) {
  const form = el("form", {id: "review-form"},
    field("复核意见", el("textarea", {name: "note", required: true, minlength: 3, maxlength: 500, rows: 3})),
    errorText(), el("button", {type: "submit"}, "签署复核"));
  submit(form, async values => saved(await api.submitReview(id, {note: values.get("note")})));
  return form;
}

function signature(review) {
  return el("div", {"data-testid": "reviewed", class: "signature"},
    el("strong", {}, `已签署 · ${review.actor}`), el("p", {}, review.note),
    el("time", {}, time(review.created_at)),
    el("small", {}, `签署落版 V${review.result_version} · 摘要 ${review.result_digest.slice(0, 12)}`));
}

function backfillForm(caseId, issue, onDone) {
  const blank = JSON.stringify({
    receipts: [{receipt_key: "RC-", asset: "", check: "", checked: 460, expires: 560, status: "pass"}],
  }, null, 2);
  const form = el("form", {id: "backfill-form"},
    field("漏登回执（JSON，receipt_key 为同一张回执的幂等键）",
      el("textarea", {name: "receipts", spellcheck: "false", required: true, rows: 9}, blank),
      "同一张回执重试补登不会重复入账；晚到的较早检测不会顶掉已生效的较新回执。"),
    errorText(), el("button", {type: "submit"}, "补登并重算"));
  submit(form, async values => {
    let payload;
    try {
      payload = JSON.parse(values.get("receipts"));
    } catch {
      throw new Error("回执 JSON 无法解析，请检查括号、引号和逗号");
    }
    const receipts = Array.isArray(payload) ? payload : payload.receipts;
    if (!Array.isArray(receipts) || !receipts.length) throw new Error("至少填写一张漏登回执");
    const outcome = await api.submitBackfill(caseId, {receipts});
    onDone(outcome);
  });
  return form;
}

function backfillHistory(rows) {
  if (!rows.length) return empty("本资料暂无补登回执。");
  return table(["回执键", "车辆", "项目", "检测", "失效", "结论", "序号", "补登人"],
    rows.map(item => [item.receipt_key, item.asset, item.check, minuteLabel(item.checked),
      minuteLabel(item.expires), item.status, item.seq, item.actor]), "补登回执台账");
}

function blockedNotice(run) {
  const text = {
    queued: run.revision > 0
      ? "该批次受补登影响，已排队等待重算，期间暂停签核和下载。"
      : "批次正在排队处理，期间暂停签核和下载。",
    recomputing: "补登回执正在重算，算完并恢复交接后才能签核和下载。",
    failed: "重算失败，批次保持暂停；操作员续跑成功后才恢复交接。",
  }[run.status];
  return el("div", {class: "feature hold-notice", "data-testid": "hold"},
    el("div", {}, status(run.status), el("p", {}, text)));
}

function exportButton(run, review, issue) {
  const button = el("button", {type: "button", onclick: async () => {
    button.disabled = true;
    issue.textContent = "";
    try {
      await download(await api.export(run.id), `evidence-${run.id}.json`);
    } catch (error) {
      issue.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  }}, "导出证据");
  if (!review && BLOCKED.has(run.status)) button.disabled = true;
  return button;
}

function retryButton(runId, issue, onRetry) {
  const button = el("button", {type: "button", "data-testid": "retry", onclick: async () => {
    button.disabled = true;
    issue.textContent = "";
    try {
      await api.retry(runId);
      onRetry();
    } catch (error) {
      issue.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  }}, "续跑重算");
  return el("div", {}, button);
}

export async function evidencePage(config, user, route, signal) {
  // 交接单页只收已完成、首次失败，以及被补登拖入重算（revision>0）的批次。
  const visible = item => item.status === "done" || item.status === "failed" ||
    (item.status === "recomputing" || (item.status === "queued" && item.revision > 0));
  const runs = (await api.runs()).filter(visible);
  if (!runs.length && !route.id) return el("div", {class: "page"},
    pageHeading(config.modules[3], null), empty("成功执行一个批次后可以查看、签核和下载证据。"),
    el("a", {href: "#runs"}, config.modules[2]));
  const runId = route.id ?? runs[0].id;

  const host = el("div", {class: "page evidence"});
  const issue = errorText();
  const reload = () => { location.hash = `evidence/${runId}`; };

  async function render() {
    const run = await api.run(runId, signal);
    const [review, history, allRuns] = await Promise.all([
      api.review(run.id), api.backfills(run.case_id), api.runs(),
    ]);
    const held = !review && BLOCKED.has(run.status);
    const signing = el("div", {});
    signing.append(
      review ? signature(review)
        : held ? empty("批次暂停交接，重算完成后复核员才能签署。")
        : user.role === "reviewer" ? reviewForm(run.id, reload)
        : empty("待复核员签署。"));

    const options = el("select", {"aria-label": "选择批次", onchange: event => {
      location.hash = `evidence/${event.target.value}`;
    }}, allRuns.filter(visible).map(item => el("option", {value: item.id, selected: item.id === run.id},
      `#${item.id} ${item.case_title}（V${item.version}）`)));

    const backfillSection = user.role === "operator" ? section("漏登回执补登",
      el("p", {class: "empty"}, "补登后本资料下未签核批次自动重算；已签核交接单保持原样。"),
      backfillForm(run.case_id, issue, outcome => {
        const dup = outcome.duplicate.length ? `，${outcome.duplicate.length} 张重复回执未重复入账` : "";
        issue.textContent = `已补登 ${outcome.accepted.length} 张回执，${outcome.affected_run_ids.length} 个批次转入重算${dup}`;
        reload();
      }),
      el("details", {open: history.length > 0}, el("summary", "补登台账"), backfillHistory(history))) : null;

    host.replaceChildren(
      pageHeading(config.modules[3], `${run.case_title} · 版本 V${run.version}`, exportButton(run, review, issue)),
      issue, options,
      held ? blockedNotice(run) : null,
      held && run.status === "failed" && user.role === "operator" ? retryButton(run.id, issue, reload) : null,
      run.result ? el("section", {class: "feature evidence-summary"},
        el("div", {}, el("h2", {}, "批次判定"), el("p", {}, run.result.summary)),
        el("details", {}, el("summary", {}, "证据摘要"), el("code", {}, run.digest || "—"))) :
        el("section", {class: "panel"}, el("h2", {}, "批次判定"), empty(run.failure || "等待重算结果。")),
      run.result ? el("dl", {class: "metrics"}, Object.entries(run.result.metrics).map(([label, value]) =>
        el("div", {}, el("dt", {}, label), el("dd", {}, cell(value))))) : null,
      run.result ? section("业务结果明细",
        run.result.rows.length ? resultTable(run.result.rows) : empty("此批次无明细。")) : null,
      section("复核签署", signing),
      backfillSection);
    return run;
  }

  const first = await render();
  if (PENDING.has(first.status)) {
    poll(async () => {
      if (signal.aborted) return false;
      const run = await render();
      return PENDING.has(run.status);
    }, signal);
  }
  return host;
}
