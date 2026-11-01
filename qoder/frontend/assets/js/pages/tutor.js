/**
 * AI Tutor page.
 *
 * Every turn is grounded in the learner's long-term memory recalled from
 * PowerContext, and the page shows exactly which memories were used so the grounding
 * is visible rather than claimed.
 */

import { api } from "../api.js";
import { getUserId } from "../store.js";
import { esc, escAttr, toast } from "../ui.js";

const SUGGESTIONS = [
  "我今天应该复习什么？",
  "帮我解释一下数据库 BCNF。",
  "我之前在哪些知识点上容易出错？",
  "根据我的学习情况出五道练习题。",
  "总结我最近的学习进步。",
];

export async function render(container) {
  const userId = getUserId();

  container.innerHTML = `
    <div class="stack">
      <section class="card">
        <div class="card__head">
          <div>
            <div class="card__title">AI 学习助手</div>
            <div class="card__hint">回答会结合 PowerContext 中保存的长期学习记忆；没有相关记忆时会明确说明。</div>
          </div>
          <div class="spacer"></div>
          <button class="btn btn--sm" id="tutor-clear">清空对话</button>
        </div>

        <div class="chat" id="tutor-chat">
          ${introBlock()}
        </div>

        <div class="pill-row" style="margin-top:14px" id="tutor-suggestions">
          ${SUGGESTIONS.map(
            (text) =>
              `<button class="btn btn--sm" data-suggest="${escAttr(text)}">${esc(text)}</button>`
          ).join("")}
        </div>

        <form class="chat-input" id="tutor-form">
          <textarea id="tutor-input" rows="1" placeholder="描述你的学习情况，或直接提问…（Enter 发送，Shift+Enter 换行）"></textarea>
          <button class="btn btn--primary" type="submit" id="tutor-send">发送</button>
        </form>
      </section>
    </div>
  `;

  const chat = container.querySelector("#tutor-chat");
  const form = container.querySelector("#tutor-form");
  const input = container.querySelector("#tutor-input");
  const sendButton = container.querySelector("#tutor-send");

  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
  });

  container.querySelector("#tutor-clear").addEventListener("click", () => {
    chat.innerHTML = introBlock();
  });

  container.querySelectorAll("[data-suggest]").forEach((button) => {
    button.addEventListener("click", () => {
      input.value = button.dataset.suggest;
      input.focus();
    });
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = input.value.trim();
    if (!message) return;
    input.value = "";
    input.style.height = "auto";

    chat.appendChild(userMessage(message));
    const pending = pendingMessage();
    chat.appendChild(pending.node);
    sendButton.disabled = true;
    input.disabled = true;

    try {
      const data = await api.tutor({ user_id: userId, message, save_to_memory: true });
      pending.node.remove();
      chat.appendChild(assistantMessage(data));
      chat.scrollIntoView({ block: "end", behavior: "smooth" });
      if (data.saved) {
        toast(
          `本轮学习信息已写入 PowerContext（${data.saved_actions.length} 条记忆）`,
          "ok",
          "已保存到长期记忆"
        );
      }
    } catch (error) {
      pending.node.remove();
      chat.appendChild(errorMessage(error));
      toast(error.message, "error", "请求失败");
    } finally {
      sendButton.disabled = false;
      input.disabled = false;
      input.focus();
    }
  });
}

function introBlock() {
  return `<div class="callout callout--info">
    直接用自然语言告诉我你的学习情况，例如「我正在学习数据库规范化，函数依赖基本理解，但 BCNF 分解经常出错」，
    或者问我「我今天应该复习什么？」。StudyFlow 会先从 PowerContext 读取你的长期记忆，再基于这些记忆回答，
    并把本轮的新信息写回记忆。
  </div>`;
}

function userMessage(text) {
  const node = document.createElement("div");
  node.className = "msg msg--me";
  node.innerHTML = `<div class="msg__avatar">我</div><div class="msg__bubble">${esc(text)}</div>`;
  return node;
}

function pendingMessage() {
  const node = document.createElement("div");
  node.className = "msg";
  node.innerHTML = `<div class="msg__avatar">AI</div><div class="msg__bubble">
    <span class="spinner"></span> 正在读取长期记忆并思考…
  </div>`;
  return { node };
}

function errorMessage(error) {
  const node = document.createElement("div");
  node.className = "msg";
  node.innerHTML = `<div class="msg__avatar">!</div><div class="msg__bubble">
    请求失败：${esc(error && error.message ? error.message : String(error))}
  </div>`;
  return node;
}

function assistantMessage(data) {
  const node = document.createElement("div");
  node.className = "msg";

  const hits = (data.memory_hits || [])
    .slice(0, 6)
    .map(
      (hit) =>
        `<span class="badge badge--brand" title="${escAttr(hit.narrative || hit.key)}">${esc(
          hit.kind
        )}：${esc((hit.key || "").split(":").slice(1).join(":") || hit.key)}</span>`
    )
    .join("");

  const meta = [];
  if (data.used_memory) {
    meta.push(`<span class="badge badge--ok badge--dot">已使用 ${(data.memory_hits || []).length} 条长期记忆</span>`);
  } else {
    meta.push('<span class="badge badge--medium badge--dot">未检索到相关长期记忆</span>');
  }
  if (data.saved) {
    meta.push(`<span class="badge badge--ok">已写入 ${(data.saved_actions || []).length} 条记忆</span>`);
  }
  if (data.ai_configured === false) {
    meta.push('<span class="badge badge--high">AI 未配置</span>');
  }

  node.innerHTML = `
    <div class="msg__avatar">AI</div>
    <div>
      <div class="msg__bubble">${esc(data.answer || "")}</div>
      <div class="msg__meta">
        ${meta.join("")}
        ${hits ? `<div class="pill-row" style="margin-top:6px">${hits}</div>` : ""}
      </div>
    </div>
  `;
  return node;
}
