/** Settings page: learner profile plus service configuration status. */

import { api } from "../api.js";
import { getUserId, invalidateStatus, setUserId } from "../store.js";
import { esc, formValues, renderError, renderLoading, toast } from "../ui.js";

export async function render(container) {
  const userId = getUserId();
  renderLoading(container, 3);

  let profile;
  let status;
  try {
    [profile, status] = await Promise.all([api.profile(userId), api.status()]);
  } catch (error) {
    renderError(container, error);
    return;
  }

  const info = status.powercontext_server_info || {};

  container.innerHTML = `
    <div class="stack">
      <section class="card">
        <div class="card__head">
          <div>
            <div class="card__title">学习者设置</div>
            <div class="card__hint">档案会作为长期记忆写入 PowerContext，下次打开仍可读取。</div>
          </div>
        </div>

        <form id="settings-form">
          <div class="field">
            <label class="field__label" for="set-user">学习者 ID</label>
            <input type="text" id="set-user" value="${esc(userId)}" />
            <span class="field__hint">换一个 ID 即可体验「不同用户记忆完全隔离」。每个 ID 在 PowerContext 中对应一个独立的 Scope。</span>
          </div>

          <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:0 16px">
            <div class="field">
              <label class="field__label" for="set-nickname">昵称</label>
              <input type="text" id="set-nickname" name="nickname" value="${esc(profile.nickname || "")}" placeholder="你的称呼" />
            </div>
            <div class="field">
              <label class="field__label" for="set-course">正在学习的课程</label>
              <input type="text" id="set-course" name="course" value="${esc(profile.course || "")}" placeholder="例如：数据库系统" />
            </div>
          </div>

          <div class="field">
            <label class="field__label" for="set-goal">学习目标</label>
            <input type="text" id="set-goal" name="goal" value="${esc(profile.goal || "")}" placeholder="例如：通过期末规范化部分" />
          </div>

          <div class="field">
            <label class="field__label" for="set-style">偏好的讲解方式</label>
            <input type="text" id="set-style" name="preferred_style" value="${esc(
    profile.preferred_style || ""
  )}" placeholder="例如：先给例子再讲定义" />
          </div>

          <div class="field">
            <label class="field__label" for="set-focus">需要重点关注的知识点</label>
            <input type="text" id="set-focus" name="focus_points" value="${esc(
    (profile.focus_points || []).join(", ")
  )}" placeholder="多个知识点用英文逗号分隔" />
          </div>

          <div class="row">
            <button class="btn btn--primary" type="submit" id="settings-save">保存档案</button>
            <span class="small muted">保存将调用 PowerContext 写入接口</span>
          </div>
        </form>
      </section>

      <section class="card">
        <div class="card__head">
          <div class="card__title">服务配置</div>
          <div class="spacer"></div>
          <button class="btn btn--sm" id="settings-refresh">重新检测</button>
        </div>

        <div class="grid grid--2">
          <div>
            <div class="section-title">PowerContext</div>
            <div class="kv">
              <span class="kv__k">连接状态</span><span>${
                status.powercontext_ok
                  ? '<span class="badge badge--ok badge--dot">可用</span>'
                  : '<span class="badge badge--high badge--dot">不可用</span>'
              }</span>
              <span class="kv__k">服务地址</span><span class="mono small">${esc(
    status.powercontext_base_url
  )}</span>
              <span class="kv__k">访问凭据</span><span>${
    status.powercontext_token_present
                  ? '<span class="badge badge--ok">已配置（后端环境变量）</span>'
                  : '<span class="badge badge--medium">未配置</span>'
              }</span>
              <span class="kv__k">Server 版本</span><span class="mono small">${esc(
    info.package_version || "—"
  )}</span>
              <span class="kv__k">API 契约</span><span class="mono small">${esc(
    info.api_contract_version
      ? `${info.api_contract_version.major}.${info.api_contract_version.minor}`
      : "—"
  )}</span>
              <span class="kv__k">Server ID</span><span class="mono small">${esc(
    info.server_id || "—"
  )}</span>
            </div>
            ${status.powercontext_detail ? `<div class="callout" style="margin-top:12px">${esc(
    status.powercontext_detail
  )}</div>` : ""}
          </div>

          <div>
            <div class="section-title">AI 模型服务</div>
            <div class="kv">
              <span class="kv__k">配置状态</span><span>${
                status.ai_configured
                  ? '<span class="badge badge--ok badge--dot">已配置</span>'
                  : '<span class="badge badge--medium badge--dot">未配置</span>'
              }</span>
              <span class="kv__k">接口地址</span><span class="mono small">${esc(
    status.ai_api_base || "未设置"
  )}</span>
              <span class="kv__k">模型</span><span class="mono small">${esc(
    status.ai_model || "未设置"
  )}</span>
              <span class="kv__k">协议</span><span class="mono small">${esc(
    status.ai_api_style
  )}</span>
              <span class="kv__k">API Key</span><span>${
    status.ai_api_key_present
                  ? '<span class="badge badge--ok">已配置（后端环境变量）</span>'
                  : '<span class="badge badge--medium">未配置</span>'
              }</span>
            </div>
            ${
              status.ai_configured
                ? ""
                : `<div class="callout callout--warn" style="margin-top:12px">
                    AI 模型服务未配置时，学习记录、薄弱知识点分析和复习计划仍然可用（这些由真实练习记录驱动），
                    但 AI 助手的回答与练习题生成不会返回伪造内容。
                    配置方式见 <code>qoder/docs/DEPLOYMENT.md</code>。
                  </div>`
            }
          </div>
        </div>
      </section>
    </div>
  `;

  const form = container.querySelector("#settings-form");
  const saveButton = container.querySelector("#settings-save");

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const values = formValues(form);
    const nextUserId = (values["set-user"] || "").trim() || userId;
    const changed = nextUserId !== userId;
    if (changed) setUserId(nextUserId);

    saveButton.disabled = true;
    saveButton.innerHTML = '<span class="spinner"></span> 保存中…';
    try {
      await api.updateProfile(nextUserId, {
        nickname: values.nickname || "",
        course: values.course || "",
        goal: values.goal || "",
        preferred_style: values.preferred_style || "",
        focus_points: (values.focus_points || "")
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
      });
      toast(
        changed ? `已切换学习者并保存档案：${nextUserId}` : "学习档案已写入 PowerContext",
        "ok",
        "保存成功"
      );
      await render(container);
    } catch (error) {
      toast(error.message, "error", "保存失败");
    } finally {
      saveButton.disabled = false;
      saveButton.textContent = "保存档案";
    }
  });

  container.querySelector("#settings-refresh").addEventListener("click", () => {
    invalidateStatus();
    render(container);
  });
}
