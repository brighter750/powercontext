/**
 * StudyFlow single-page app shell and hash router.
 */

import { getUserId, setUserId } from "./store.js";
import { toast } from "./ui.js";

import { render as renderDashboard } from "./pages/dashboard.js";
import { render as renderTutor } from "./pages/tutor.js";
import { render as renderRecords } from "./pages/records.js";
import { render as renderWeaknesses } from "./pages/weaknesses.js";
import { render as renderReview } from "./pages/review.js";
import { render as renderMemory } from "./pages/memory.js";
import { render as renderSettings } from "./pages/settings.js";

const PAGES = [
  { id: "dashboard", path: "/dashboard", icon: "◈", label: "学习概览", title: "学习概览", sub: "课程、目标、薄弱点与今日复习", render: renderDashboard },
  { id: "tutor", path: "/tutor", icon: "✦", label: "AI 助手", title: "AI 学习助手", sub: "结合长期记忆的个性化辅导", render: renderTutor },
  { id: "records", path: "/records", icon: "✎", label: "学习记录", title: "学习记录", sub: "记录并追踪每一次练习", render: renderRecords },
  { id: "weaknesses", path: "/weaknesses", icon: "◐", label: "薄弱分析", title: "薄弱知识点分析", sub: "基于真实练习记录的掌握度", render: renderWeaknesses },
  { id: "review", path: "/review", icon: "☰", label: "复习计划", title: "个性化复习计划", sub: "今天该复习什么，以及为什么", render: renderReview },
  { id: "memory", path: "/memory", icon: "◫", label: "记忆中心", title: "长期记忆中心", sub: "查看 PowerContext 中保存的全部记忆", render: renderMemory },
  { id: "settings", path: "/settings", icon: "⚙", label: "设置", title: "设置", sub: "学习档案与服务状态", render: renderSettings },
];

const DEFAULT_PAGE = PAGES[0];

function currentPage() {
  const hash = window.location.hash.replace(/^#/, "") || DEFAULT_PAGE.path;
  return PAGES.find((page) => page.path === hash) || DEFAULT_PAGE;
}

function buildNav() {
  const nav = document.querySelector("#nav");
  nav.innerHTML = `
    <div class="nav-label">学习</div>
    ${PAGES.slice(0, 5)
      .map(
        (page) => `<a class="nav-item" href="#${page.path}" data-page="${page.id}">
          <span class="nav-item__icon">${page.icon}</span><span>${page.label}</span>
        </a>`
      )
      .join("")}
    <div class="nav-label">系统</div>
    ${PAGES.slice(5)
      .map(
        (page) => `<a class="nav-item" href="#${page.path}" data-page="${page.id}">
          <span class="nav-item__icon">${page.icon}</span><span>${page.label}</span>
        </a>`
      )
      .join("")}
  `;
}

function markActive(pageId) {
  document.querySelectorAll(".nav-item").forEach((item) => {
    item.classList.toggle("is-active", item.dataset.page === pageId);
  });
}

async function route() {
  const page = currentPage();
  const content = document.querySelector("#content");
  const title = document.querySelector("#page-title");
  const sub = document.querySelector("#page-sub");

  title.textContent = page.title;
  sub.textContent = page.sub;
  markActive(page.id);
  document.querySelector(".app").classList.remove("is-nav-open");

  try {
    await page.render(content);
  } catch (error) {
    content.innerHTML = `<div class="callout callout--warn">页面渲染失败：${error.message}</div>`;
  }
}

function boot() {
  buildNav();

  const userBadge = document.querySelector("#user-badge");
  const syncUserBadge = () => {
    userBadge.textContent = getUserId();
  };
  syncUserBadge();

  document.querySelector("#menu-toggle").addEventListener("click", () => {
    document.querySelector(".app").classList.toggle("is-nav-open");
  });
  document.querySelector(".app").addEventListener("click", (event) => {
    if (event.target.classList.contains("app")) {
      event.currentTarget.classList.remove("is-nav-open");
    }
  });

  userBadge.addEventListener("click", () => {
    const next = window.prompt("切换学习者 ID（每个 ID 在 PowerContext 中对应独立记忆空间）：", getUserId());
    if (next === null) return;
    const trimmed = next.trim();
    if (!trimmed) {
      toast("学习者 ID 不能为空", "warn");
      return;
    }
    setUserId(trimmed);
    syncUserBadge();
    toast(`已切换到学习者 ${trimmed}`, "ok");
    route();
  });

  window.addEventListener("hashchange", route);
  route();
}

document.addEventListener("DOMContentLoaded", boot);
