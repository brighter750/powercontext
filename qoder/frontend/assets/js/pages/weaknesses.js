/** Weakness Analysis page: the knowledge points that need attention, with evidence. */

import { api } from "../api.js";
import { getUserId } from "../store.js";
import {
  emptyState,
  esc,
  fmtDate,
  meterCell,
  renderError,
  renderLoading,
  severityClass,
} from "../ui.js";

const SEVERITY_LABEL = { high: "急需加强", medium: "需要关注", low: "轻度薄弱" };

export async function render(container) {
  const userId = getUserId();
  renderLoading(container, 3);

  let items;
  let states;
  try {
    [items, states] = await Promise.all([api.weaknesses(userId), api.memory(userId)]);
  } catch (error) {
    renderError(container, error);
    return;
  }

  const byState = new Map((states.knowledge_states || []).map((item) => [item.topic, item]));

  container.innerHTML = `
    <div class="stack">
      <div class="callout">
        掌握度由 PowerContext 中保存的真实练习记录计算得出：练习正确率与自评加权，并按时间衰减让近期练习权重更高。
        单次答错只会标为「需要关注」，需要多次证据才会升到「急需加强」。
      </div>

      <section class="card card--flush">
        <div class="card__head" style="padding:20px 20px 0;margin-bottom:12px">
          <div>
            <div class="card__title">薄弱知识点</div>
            <div class="card__hint">共 ${items.length} 项，均有来自学习记录或学习者自述的证据</div>
          </div>
        </div>
        ${
          items.length
            ? `<div class="table-wrap table-wrap--wide"><table>
                <thead><tr>
                  <th>知识点</th><th>严重程度</th><th>掌握度</th><th>练习</th><th>错误</th>
                  <th>证据</th><th>最近练习</th><th>建议</th><th>判定原因</th>
                </tr></thead>
                <tbody>${items.map((item) => rowTemplate(item, byState.get(item.topic))).join("")}</tbody>
              </table></div>`
            : emptyState(
                "🌿",
                "目前没有薄弱知识点",
                "这通常意味着掌握度不错，或者还没有足够的练习记录。继续记录练习，系统会持续跟踪。"
              )
        }
      </section>

      ${
        states.knowledge_states && states.knowledge_states.length
          ? `<section class="card">
              <div class="card__head"><div class="card__title">全部知识点掌握度</div></div>
              <div class="table-wrap"><table>
                <thead><tr><th>知识点</th><th>掌握度</th><th>练习次数</th><th>答对 / 答错</th><th>自评</th><th>证据说明</th></tr></thead>
                <tbody>${states.knowledge_states
                  .map(
                    (state) => `<tr>
                      <td><strong>${esc(state.topic)}</strong></td>
                      <td>${meterCell(state.mastery)}</td>
                      <td>${esc(state.attempts)}</td>
                      <td>${esc(state.correct)} / ${esc(state.wrong)}</td>
                      <td class="muted">${state.self_rating ? `${Number(state.self_rating).toFixed(1)}/5` : "—"}</td>
                      <td class="muted small">${esc(state.evidence || "")}</td>
                    </tr>`
                  )
                  .join("")}</tbody>
              </table></div>
            </section>`
          : ""
      }
    </div>
  `;
}

function rowTemplate(item, state) {
  return `<tr>
    <td>
      <strong>${esc(item.topic)}</strong>
      ${item.course ? `<div class="small muted">${esc(item.course)}</div>` : ""}
    </td>
    <td><span class="badge badge--${severityClass(item.severity)}">${esc(
    SEVERITY_LABEL[item.severity] || item.severity
  )}</span></td>
    <td>${meterCell(item.mastery, item.severity)}</td>
    <td>${esc(item.attempts)}</td>
    <td>${esc(item.wrong)}</td>
    <td class="small muted">${esc(item.evidence || "")}</td>
    <td class="muted small" style="white-space:nowrap">${esc(
    item.last_practiced_at ? fmtDate(item.last_practiced_at) : "—"
  )}</td>
    <td class="small" style="min-width:180px">${esc(item.recommendation || "")}</td>
    <td class="small muted cell-text">${esc(item.reason || "")}</td>
  </tr>`;
}
