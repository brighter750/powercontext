/**
 * Thin API client for the StudyFlow backend.
 *
 * Every call goes to the backend's JSON API; the browser never holds a PowerContext
 * token or an AI model key.
 */

const BASE = "";

async function request(path, options = {}) {
  const { method = "GET", body, query } = options;
  let url = BASE + path;
  if (query) {
    const params = new URLSearchParams();
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== "") {
        params.append(key, value);
      }
    });
    const qs = params.toString();
    if (qs) url += (url.includes("?") ? "&" : "?") + qs;
  }

  let response;
  try {
    response = await fetch(url, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (error) {
    throw new ApiError("无法连接 StudyFlow 后端服务，请确认后端已启动。", 0, error.message);
  }

  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch (error) {
      payload = { message: text };
    }
  }

  if (!response.ok) {
    const message =
      (payload && (payload.message || payload.detail)) ||
      `请求失败（HTTP ${response.status}）`;
    throw new ApiError(message, response.status, payload);
  }
  return payload;
}

export class ApiError extends Error {
  constructor(message, status, payload) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}

export const api = {
  status: () => request("/api/system/status"),
  dashboard: (userId) => request("/api/dashboard", { query: { user_id: userId } }),
  profile: (userId) => request("/api/profile", { query: { user_id: userId } }),
  updateProfile: (userId, payload) =>
    request("/api/profile", { method: "PUT", query: { user_id: userId }, body: payload }),
  records: (userId) => request("/api/records", { query: { user_id: userId } }),
  createRecord: (userId, payload) =>
    request("/api/records", { method: "POST", query: { user_id: userId }, body: payload }),
  weaknesses: (userId) => request("/api/weaknesses", { query: { user_id: userId } }),
  reviewPlan: (userId) => request("/api/review-plan", { query: { user_id: userId } }),
  tutor: (payload) => request("/api/tutor", { method: "POST", body: payload }),
  memory: (userId) => request("/api/memory", { query: { user_id: userId } }),
};
