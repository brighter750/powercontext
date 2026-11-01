/** Review Plan page: today's personalised plan and generated practice. */

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

export async function render(container) {
  const userId = getUserId();
  renderLoading(container, 3);

  let plan;
  try {
    plan = await api.reviewPlan(userId);
  } catch (error) {
    renderError(container, error);
    return;
  }

  const items = plan.items || [];
  const questions = plan.practice_questions || [];

  container.innerHTML = `
    <div class="stack">
      <section class="card">
        <div class="card__head">
          <div>
            <div class="card__title">今日复习计划</div>
            <div class="card__hint">生成时间 ${esc(fmtDate(plan.generated_at))}　·　依据：PowerContext 中的学习记录</div>
          </div>
          <div class="spacer"></div>
          <button class="btn btn--sm" id="review-refresh">重新生成</button>
        </div>
        <p style="margin-bottom:0;color:var(--text-muted)">${esc(plan.summary || "")}</p>
      </section>

      <section class="card card--flush">
        <div class="card__head" style="padding:20px 20px 0;margin-bottom:12px">
          <div class="card__title">复习建议</div>
        </div>
        ${
          items.length
            ? `<div style="padding:0 20px 20px" class="stack">${items.map(itemTemplate).join("")}</div>`
            : emptyState(
                "🗓️",
                "今天没有需要特别复习的知识点",
                "保持当前节奏即可。记录更多练习后，系统会给出更有针对性的安排。"
              )
        }
      </section>

      <section class="card card--flush">
        <div class="card__head" style="padding:20px 20px 0;margin-bottom:12px">
          <div>
            <div class="card__title">针对性练习</div>
            <div class="card__hint">${
              questions.length
                ? "由 AI 模型基于你的记忆生成"
                : "配置 AI 模型后，这里会针对最需要加强的知识点生成练习题"
            }</div>
          </div>
        </div>
        ${
          questions.length
            ? `<div style="padding:0 20px 20px" class="stack">${questions.map(questionTemplate).join("")}</div>`
            : emptyState(
                "✍️",
                "还没有生成练习题",
                "练习题由 AI 模型根据你的长期记忆生成，未配置模型时不会用假内容填充。"
              )
        }
      </section>
    </div>
  `;

  container.querySelector("#review-refresh").addEventListener("click", () => render(container));

  container.querySelectorAll("[data-toggle-answer]").forEach((button) => {
    button.addEventListener("click", () => {
      const answer = button.parentElement.querySelector("[data-answer]");
      const hidden = answer.style.display === "none";
      answer.style.display = hidden ? "block" : "none";
      button.textContent = hidden ? "隐藏答案" : "显示答案";
    });
  });
}

function itemTemplate(item) {
  return `<div style="padding:14px 0;border-bottom:1px solid var(--border)">
    <div class="row" style="margin-bottom:8px">
      <span class="badge badge--brand">第 ${esc(item.priority)} 优先</span>
      <strong style="font-size:15px">${esc(item.topic)}</strong>
      <span class="badge badge--${severityClass(item.severity)}">${esc(
    severityLabel(item.severity)
  )}</span>
      <div class="spacer"></div>
      ${meterCell(item.mastery, item.severity)}
    </div>
    <div style="color:var(--text-muted);font-size:14px">${esc(item.action || "")}</div>
    <div class="callout" style="margin-top:10px">
      <strong>推荐原因</strong><br />${esc(item.reason || "")}
      <div class="small muted" style="margin-top:6px">证据：${esc(item.evidence || "")}</div>
    </div>
  </div>`;
}

function questionTemplate(question, index) {
  return `<div style="padding:12px 0;border-bottom:1px solid var(--border)">
    <div class="row" style="align-items:flex-start;gap:10px">
      <span class="badge badge--brand">Q${index + 1}</span>
      <div style="flex:1;min-width:0">
        <div>${esc(question.question || "")}</div>
        ${question.hint ? `<div class="small muted" style="margin-top:4px">提示：${esc(question.hint)}</div>` : ""}
        <div data-answer style="display:none;margin-top:8px">
          <span class="badge badge--ok">参考答案</span>
          <div style="margin-top:4px">${esc(question.answer || "")}</div>
        </div>
      </div>
      <button class="btn btn--sm" data-toggle-answer>显示答案</button>
    </div>
  </div>`;
}

function severityLabel(severity) {
  if (severity === "high") return "急需加强";
  if (severity === "medium") return "需要关注";
  return "轻度薄弱";
}
