/** Shared UI helpers: escaping, formatting, toasts, and small DOM utilities. */

/** Escape a value for safe interpolation into HTML. */
export function esc(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** Escape a value for use inside a single-quoted JS string in an inline handler. */
export function escAttr(value) {
  return esc(value).replace(/\n/g, " ");
}

/** Format an ISO timestamp as a local readable string. */
export function fmtDate(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Format an ISO timestamp as a relative description. */
export function fmtRelative(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const delta = Date.now() - date.getTime();
  const minutes = Math.floor(delta / 60000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} 天前`;
  return fmtDate(iso);
}

/** Format a 0..1 mastery value as a percentage string. */
export function fmtPct(value) {
  if (value === null || value === undefined) return "—";
  return `${Math.round(Number(value) * 100)}%`;
}

/** Return the CSS modifier for a severity label. */
export function severityClass(severity) {
  if (severity === "high") return "high";
  if (severity === "medium") return "medium";
  return "low";
}

/** Build a mastery meter cell. */
export function meterCell(mastery, severity) {
  const pct = Math.max(0, Math.min(1, Number(mastery) || 0));
  const modifier = severity ? severityClass(severity) : "";
  return `<div class="meter-cell">
    <div class="meter"><div class="meter__fill meter__fill--${modifier}" style="width:${pct * 100}%"></div></div>
    <span class="meter-cell__num">${fmtPct(pct)}</span>
  </div>`;
}

/** Build an empty-state block. */
export function emptyState(icon, title, text) {
  return `<div class="empty">
    <div class="empty__icon">${esc(icon)}</div>
    <div class="empty__title">${esc(title)}</div>
    <div class="empty__text">${esc(text)}</div>
  </div>`;
}

/** Build a skeleton placeholder. */
export function skeleton(rows = 3) {
  return `<div class="stack">${Array.from(
    { length: rows },
    () => `<div class="skeleton skeleton--block"></div>`
  ).join("")}</div>`;
}

/** Show a toast notification. */
export function toast(message, kind = "info", title = "") {
  let host = document.querySelector(".toasts");
  if (!host) {
    host = document.createElement("div");
    host.className = "toasts";
    document.body.appendChild(host);
  }
  const node = document.createElement("div");
  node.className = `toast toast--${kind}`;
  node.innerHTML = `${title ? `<div class="toast__title">${esc(title)}</div>` : ""}<div>${esc(
    message
  )}</div>`;
  host.appendChild(node);
  setTimeout(() => {
    node.style.opacity = "0";
    node.style.transform = "translateY(8px)";
    node.style.transition = "opacity .2s ease, transform .2s ease";
    setTimeout(() => node.remove(), 220);
  }, kind === "error" ? 6500 : 3600);
}

/** Render an error banner inside a container. */
export function renderError(container, error) {
  container.innerHTML = `<div class="callout callout--warn">
    <strong>加载失败</strong><br />${esc(error && error.message ? error.message : String(error))}
  </div>`;
}

/** Replace a container's content with a loading placeholder. */
export function renderLoading(container, rows = 3) {
  container.innerHTML = skeleton(rows);
}

/** Read the form values of a container as a plain object. */
export function formValues(form) {
  const values = {};
  new FormData(form).forEach((value, key) => {
    if (key.endsWith("[]")) {
      const name = key.slice(0, -2);
      values[name] = values[name] || [];
      values[name].push(value);
    } else {
      values[key] = value;
    }
  });
  form.querySelectorAll("input[type=checkbox]").forEach((box) => {
    values[box.name] = box.checked;
  });
  return values;
}

/** Scroll the main content area back to the top. */
export function scrollTop() {
  window.scrollTo({ top: 0, behavior: "smooth" });
}
