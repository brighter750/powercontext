/** Dashboard page: learning overview at a glance. */

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
  renderLoading(container, 4);

  let data;
  try {
    data = await api.dashboard(userId);
  } catch (error) {
    renderError(container, error);
    return;
  }

  const profile = data.profile || {};
  const weaknesses = data.weaknesses || [];
  const review = data.today_review || [];
  const progress = data.recent_progress || [];

  container.innerHTML = `
    <div class="stack">
      <div class="card">
        <div class="card__head">
          <div>
            <div class="card__title">你好，${esc(profile.nickname || userId)}</div>
            <div class="card__hint">当前课程：${esc(profile.course || "尚未设置")}　·　目标：${esc(
    profile.goal || "尚未设置"
  )}</div>
          </div>
          <div class="spacer"></div>
          ${
            data.ai_configured
              ? '<span class="badge badge--ok badge--dot">AI 模型已就绪</span>'
              : '<span class="badge badge--medium badge--dot">AI 模型未配置</span>'
          }
        </div>
        ${
          profile.focus_points && profile.focus_points.length
            ? `<div class="pill-row">${profile.focus_points
                .map((point) => `<span class="tag">${esc(point)}</span>`)
                .join("")}</div>`
            : ""
        }
      </div>

      <div class="grid grid--3">
        ${statTile("已记录学习活动", String(data.activity_count ?? 0), "条学习记录")}
        ${statTile("覆盖知识点", String(data.knowledge_point_count ?? 0), "个知识点")}
        ${statTile("最近记忆更新", data.last_memory_update ? fmtDate(data.last_memory_update) : "—", "来自 PowerContext")}
      </div>

      <div class="grid grid--2">
        <section class="card">
          <div class="card__head">
            <div class="card__title">当前薄弱知识点</div>
            <div class="spacer"></div>
            <a href="#/weaknesses" class="small">查看全部</a>
          </div>
          ${
            weaknesses.length
              ? `<div class="stack" style="gap:12px">${weaknesses
                  .map(
                    (item) => `
                <div>
                  <div class="row" style="margin-bottom:6px">
                    <strong style="font-size:14px">${esc(item.topic)}</strong>
                    <span class="badge badge--${severityClass(item.severity)}">${severityLabel(
                      item.severity
                    )}</span>
                    <div class="spacer"></div>
                    <span class="small muted">${esc(item.evidence || "")}</span>
                  </div>
                  ${meterCell(item.mastery, item.severity)}
                  <div class="small muted" style="margin-top:6px">${esc(item.reason || "")}</div>
                </div>`
                  )
                  .join("")}</div>`
              : emptyState(
                  "🎯",
                  "暂时没有明显的薄弱知识点",
                  "记录几次练习后，系统会根据真实练习结果找出需要加强的知识点。"
                )
          }
        </section>

        <section class="card">
          <div class="card__head">
            <div class="card__title">今日复习建议</div>
            <div class="spacer"></div>
            <a href="#/review" class="small">完整计划</a>
          </div>
          ${
            review.length
              ? `<div class="stack" style="gap:12px">
                  ${review
                    .map(
                      (item) => `<div>
                    <div class="row" style="margin-bottom:4px">
                      <span class="badge badge--brand">第 ${item.priority} 优先</span>
                      <strong style="font-size:14px">${esc(item.topic)}</strong>
                    </div>
                    <div class="small" style="color:var(--text-muted)">${esc(item.action || "")}</div>
                    <div class="small muted" style="margin-top:4px">${esc(item.reason || "")}</div>
                  </div>`
                    )
                    .join("")}
                </div>`
              : emptyState("📅", "还没有复习建议", data.review_summary || "")
          }
        </section>
      </div>

      <section class="card">
        <div class="card__head">
          <div class="card__title">最近学习进展</div>
          <div class="spacer"></div>
          <a href="#/records" class="small">学习记录</a>
        </div>
        ${
          progress.length
            ? `<div class="table-wrap"><table>
                <thead><tr><th>时间</th><th>知识点</th><th>结果</th><th>备注</th></tr></thead>
                <tbody>${progress
                  .map(
                    (row) => `<tr>
                      <td class="muted" style="white-space:nowrap">${esc(fmtDate(row.practiced_at))}</td>
                      <td>${esc(row.topic)}</td>
                      <td>${
                        row.is_correct === true
                          ? '<span class="badge badge--ok">正确</span>'
                          : row.is_correct === false
                          ? '<span class="badge badge--high">错误</span>'
                          : '<span class="badge">未标记</span>'
                      }</td>
                      <td class="muted">${esc(row.notes || "")}</td>
                    </tr>`
                  )
                  .join("")}</tbody>
              </table></div>`
            : emptyState("📝", "还没有学习记录", "在「学习记录」页面添加一次练习，进展就会出现在这里。")
        }
      </section>
    </div>
  `;
}

function statTile(label, value, foot) {
  return `<div class="stat">
    <div class="stat__label">${esc(label)}</div>
    <div class="stat__value stat__value--sm">${esc(value)}</div>
    <div class="stat__foot">${esc(foot)}</div>
  </div>`;
}

function severityLabel(severity) {
  if (severity === "high") return "急需加强";
  if (severity === "medium") return "需要关注";
  return "轻度薄弱";
}
