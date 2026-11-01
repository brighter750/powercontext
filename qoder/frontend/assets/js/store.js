/**
 * Session state for the browser.
 *
 * The selected learner id is a UI convenience only. Every piece of long-term learning
 * memory lives in PowerContext — never in localStorage.
 */

import { api } from "./api.js";

const USER_KEY = "studyflow.user_id";
const DEFAULT_USER = "student_demo_001";

let statusPromise = null;

export function getUserId() {
  return localStorage.getItem(USER_KEY) || DEFAULT_USER;
}

export function setUserId(userId) {
  const trimmed = (userId || "").trim();
  if (!trimmed) return getUserId();
  localStorage.setItem(USER_KEY, trimmed);
  return trimmed;
}

/** Fetch the service status once and reuse the promise afterwards. */
export function serviceStatus() {
  if (!statusPromise) {
    statusPromise = api.status().catch((error) => {
      statusPromise = null;
      throw error;
    });
  }
  return statusPromise;
}

/** Force the next serviceStatus() call to hit the backend again. */
export function invalidateStatus() {
  statusPromise = null;
}
