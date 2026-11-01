/**
 * Memory Center page.
 *
 * This page exists to make the PowerContext integration auditable: it shows the
 * learner's Scope, every raw Atomic Memory entry that was written, the read/write
 * status of the service, and when memory was last updated.
 */

import { api } from "../api.js";
import { getUserId } from "../store.js";
import {
  emptyState,
  esc,
  fmtDate,
  fmtPct,
  meterCell,
  renderError,
  renderLoading,
  severityClass,
} from "../ui.js";

export async function render(container) {
  const userId = getUserId();
  renderLoading(container, 4);

  let data;
  let status = null;
  try {
    [data, status] = await Promise.all([api.memory(userId), api.status().catch(() => null)]);
  } catch (error) {
    renderError(container, error);
    return;
  }

  const profile = data.profile || {};
  const entries = data.entries || [];

  container.innerHTML = `
    <div class="stack">
      <section class="card">
        <div class="card__head">
          <div>
            <div class="card__title">长期记忆中心</div>
            <div class="card__hint">下面的每一条内容都来自 PowerContext Server 的 Memory 存储，可逐条核对。</div>
          </div>
          <div class="spacer"></div>
          <button class="btn btn--sm" id="memory-refresh">刷新</button>
        </div>

        <div class="grid grid--3">
          <div class="stat">
            <div class="stat__label">PowerContext 读取</div>
            <div class="stat__value stat__value--sm" style="color:var(--ok)">${data.read_ok ? "正常" : "失败"}</div>
            <div class="stat__foot">${esc(data.powercontext?.base_url || "")}</div>
          </div>
          <div class="stat">
            <div class="stat__label">PowerContext 写入</div>
            <div class="stat__value stat__value--sm" style="color:var(--ok)">${data.write_ok ? "正常" : "失败"}</div>
            <div class="stat__foot">凭据：${data.powercontext?.token_present ? "已配置" : "未配置"}</div>
          </div>
          <div class="stat">
            <div class="stat__label">最近记忆更新</div>
            <div class="stat__value stat__value--sm">${esc(
    data.last_memory_update ? fmtDate(data.last_memory_update) : "—"
  )}</div>
            <div class="stat__foot">共 ${esc(data.entry_count)} 条记忆</div>
          </div>
        </div>

        <div class="divider"></div>

        <div class="kv">
          <span class="kv__k">学习者 ID</span><span class="mono">${esc(data.user_id)}</span>
          <span class="kv__k">PowerContext Scope</span><span class="mono">${esc(data.scope_id || "—")}</span>
          <span class="kv__k">Scope 标题</span><span>${esc(data.scope_title || "—")}</span>
          <span class="kv__k">用户隔离键</span><span class="mono">${esc(
    data.powercontext?.scope_kind || "—"
  )}</span>
          <span class="kv__k">服务状态</span><span>${
            status
              ? status.powercontext_ok
                ? '<span class="badge badge--ok badge--dot">PowerContext 可用</span>'
                : '<span class="badge badge--high badge--dot">PowerContext 不可用</span>'
              : '<span class="badge badge--medium">状态未知</span>'
          }</span>
        </div>
      </section>

      <section class="card">
        <div class="card__head"><div class="card__title">已保存的学习档案</div></div>
        ${
          profile.source === "powercontext"
            ? `<div class="kv">
                <span class="kv__k">昵称</span><span>${esc(profile.nickname || "—")}</span>
                <span class="kv__k">当前课程</span><span>${esc(profile.course || "—")}</span>
                <span class="kv__k">学习目标</span><span>${esc(profile.goal || "—")}</span>
                <span class="kv__k">偏好的讲解方式</span><span>${esc(profile.preferred_style || "—")}</span>
                <span class="kv__k">重点关注</span><span>${
                  profile.focus_points && profile.focus_points.length
                    ? profile.focus_points.map((point) => `<span class="tag">${esc(point)}</span>`).join(" ")
                    : "—"
                }</span>
                <span class="kv__k">创建时间</span><span class="muted">${esc(
    profile.created_at ? fmtDate(profile.created_at) : "—"
  )}</span>
                <span class="kv__k">更新时间</span><span class="muted">${esc(
    profile.updated_at ? fmtDate(profile.updated_at) : "—"
  )}</span>
              </div>`
            : emptyState("👤", "还没有保存学习档案", "在「设置」页面填写档案后，会作为长期记忆写入 PowerContext。")
        }
      </section>

      <div class="grid grid--2">
        <section class="card">
          <div class="card__head"><div class="card__title">已保存的知识点</div></div>
          ${
            data.knowledge_states && data.knowledge_states.length
              ? `<div class="stack" style="gap:12px">${data.knowledge_states
                  .map(
                    (state) => `<div>
                      <div class="row" style="margin-bottom:5px">
                        <strong style="font-size:14px">${esc(state.topic)}</strong>
                        <div class="spacer"></div>
                        <span class="small muted">${esc(state.evidence || "")}</span>
                      </div>
                      ${meterCell(state.mastery)}
                    </div>`
                  )
                  .join("")}</div>`
              : emptyState("🧠", "还没有知识点记忆", "记录练习或与 AI 助手对话后，这里会出现知识点掌握情况。")
          }
        </section>

        <section class="card">
          <div class="card__head"><div class="card__title">历史薄弱点</div></div>
          ${
            data.weaknesses && data.weaknesses.length
              ? `<div class="stack" style="gap:10px">${data.weaknesses
                  .map(
                    (item) => `<div>
                      <div class="row" style="margin-bottom:4px">
                        <strong style="font-size:14px">${esc(item.topic)}</strong>
                        <span class="badge badge--${severityClass(item.severity)}">${esc(
                      fmtPct(item.mastery)
                    )}</span>
                      </div>
                      <div class="small muted">${esc(item.reason || "")}</div>
                    </div>`
                  )
                  .join("")}</div>`
              : emptyState("✅", "没有记录到薄弱点", "这说明当前没有反复出错的知识点。")
          }
        </section>
      </div>

      <section class="card card--flush">
        <div class="card__head" style="padding:20px 20px 0;margin-bottom:12px">
          <div>
            <div class="card__title">PowerContext 记忆条目</div>
            <div class="card__hint">共 ${entries.length} 条 Atomic Memory 记录，显示真实 artifact ID 与修订号</div>
          </div>
        </div>
        ${
          entries.length
            ? `<div class="table-wrap table-wrap--wide"><table>
                <thead><tr><th>类型</th><th>Key</th><th>内容</th><th>artifact_id</th><th>修订</th><th>状态</th></tr></thead>
                <tbody>${entries
                  .map(
                    (entry) => `<tr>
                      <td><span class="badge badge--brand">${esc(entry.kind)}</span></td>
                      <td class="mono small">${esc(entry.key)}</td>
                      <td class="small cell-text">${esc(entry.narrative || entry.text)}</td>
                      <td class="mono small">${esc(entry.artifact_id)}</td>
                      <td class="small">r${esc(entry.revision)}</td>
                      <td><span class="badge badge--ok">${esc(entry.state)}</span></td>
                    </tr>`
                  )
                  .join("")}</tbody>
              </table></div>`
            : emptyState("🗄️", "PowerContext 中还没有记忆", "添加学习记录或与 AI 助手对话后，这里会出现真实条目。")
        }
      </section>
    </div>
  `;

  container.querySelector("#memory-refresh").addEventListener("click", () => render(container));
}
