/** Learning Records page: submit practice activity and browse the stored log. */

import { api } from "../api.js";
import { getUserId } from "../store.js";
import { emptyState, esc, fmtDate, formValues, renderError, renderLoading, toast } from "../ui.js";

export async function render(container) {
  const userId = getUserId();
  renderLoading(container, 3);

  let records;
  try {
    records = await api.records(userId);
  } catch (error) {
    renderError(container, error);
    return;
  }

  container.innerHTML = `
    <div class="stack">
      <section class="card">
        <div class="card__head">
          <div>
            <div class="card__title">新增学习记录</div>
            <div class="card__hint">保存后会写入 PowerContext，并重新计算该知识点的掌握度。</div>
          </div>
        </div>
        <form id="record-form">
          <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:0 16px">
            <div class="field">
              <label class="field__label" for="rec-course">课程</label>
              <input type="text" id="rec-course" name="course" placeholder="例如：数据库系统" />
            </div>
            <div class="field">
              <label class="field__label" for="rec-topic">知识点 <span style="color:var(--danger)">*</span></label>
              <input type="text" id="rec-topic" name="topic" required placeholder="例如：BCNF 分解" />
            </div>
          </div>

          <div class="field">
            <label class="field__label" for="rec-question">题目</label>
            <input type="text" id="rec-question" name="question" placeholder="练习题或知识点内容" />
          </div>

          <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:0 16px">
            <div class="field">
              <label class="field__label" for="rec-user-answer">我的答案</label>
              <input type="text" id="rec-user-answer" name="user_answer" placeholder="你的答案" />
            </div>
            <div class="field">
              <label class="field__label" for="rec-correct-answer">正确答案</label>
              <input type="text" id="rec-correct-answer" name="correct_answer" placeholder="正确答案（可留空）" />
            </div>
          </div>

          <div class="field">
            <span class="field__label">本次练习结果</span>
            <div class="radio-row">
              <label class="radio-pill"><input type="radio" name="is_correct" value="" checked /><span>未标记</span></label>
              <label class="radio-pill"><input type="radio" name="is_correct" value="true" /><span>正确</span></label>
              <label class="radio-pill"><input type="radio" name="is_correct" value="false" /><span>错误</span></label>
            </div>
          </div>

          <div class="field">
            <label class="field__label" for="rec-rating">自评掌握程度</label>
            <select id="rec-rating" name="self_rating">
              <option value="">未评分</option>
              <option value="1">1 — 完全没掌握</option>
              <option value="2">2 — 不太熟</option>
              <option value="3">3 — 一般</option>
              <option value="4">4 — 比较熟</option>
              <option value="5">5 — 很熟</option>
            </select>
          </div>

          <div class="field">
            <label class="field__label" for="rec-notes">学习笔记</label>
            <textarea id="rec-notes" name="notes" placeholder="这次学到了什么？哪里卡住了？"></textarea>
          </div>

          <div class="field">
            <label class="field__label" for="rec-problems">遇到的问题</label>
            <input type="text" id="rec-problems" name="problems" placeholder="例如：分解步骤总是漏掉求闭包" />
          </div>

          <div class="row">
            <button class="btn btn--primary" type="submit" id="rec-submit">保存学习记录</button>
            <span class="small muted">保存时会真实写入 PowerContext Memory。</span>
          </div>
        </form>
      </section>

      <section class="card card--flush">
        <div class="card__head" style="padding:20px 20px 0;margin-bottom:12px">
          <div>
            <div class="card__title">历史学习记录</div>
            <div class="card__hint">共 ${records.length} 条，全部来自 PowerContext</div>
          </div>
        </div>
        ${
          records.length
            ? `<div class="table-wrap"><table>
                <thead><tr>
                  <th>时间</th><th>知识点</th><th>结果</th><th>自评</th><th>备注</th><th>PowerContext</th>
                </tr></thead>
                <tbody>${records.map(rowTemplate).join("")}</tbody>
              </table></div>`
            : emptyState(
                "📚",
                "还没有学习记录",
                "填写上面的表单记录一次练习，StudyFlow 会把它写入 PowerContext 并开始追踪掌握度。"
              )
        }
      </section>
    </div>
  `;

  const form = container.querySelector("#record-form");
  const submit = container.querySelector("#rec-submit");

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const values = formValues(form);

    const payload = {
      course: values.course || "",
      topic: (values.topic || "").trim(),
      notes: values.notes || "",
      question: values.question || "",
      user_answer: values.user_answer || "",
      correct_answer: values.correct_answer || "",
      is_correct:
        values.is_correct === "" || values.is_correct === undefined
          ? null
          : values.is_correct === "true",
      self_rating: values.self_rating ? Number(values.self_rating) : null,
      problems: values.problems || "",
      practiced_at: "",
    };

    if (!payload.topic) {
      toast("请填写知识点", "warn");
      return;
    }

    submit.disabled = true;
    submit.innerHTML = '<span class="spinner"></span> 保存中…';
    try {
      const result = await api.createRecord(userId, payload);
      const state = result.knowledge_state;
      toast(
        state
          ? `已写入 PowerContext，「${state.topic}」当前掌握度 ${Math.round(
              state.mastery * 100
            )}%（${state.attempts} 次练习）`
          : "学习记录已保存到 PowerContext",
        "ok",
        "保存成功"
      );
      form.reset();
      await render(container);
    } catch (error) {
      toast(error.message, "error", "保存失败");
    } finally {
      submit.disabled = false;
      submit.textContent = "保存学习记录";
    }
  });
}

function rowTemplate(record) {
  const result =
    record.is_correct === true
      ? '<span class="badge badge--ok">正确</span>'
      : record.is_correct === false
      ? '<span class="badge badge--high">错误</span>'
      : '<span class="badge">未标记</span>';
  return `<tr>
    <td class="muted" style="white-space:nowrap">${esc(fmtDate(record.practiced_at))}</td>
    <td><strong>${esc(record.topic)}</strong><div class="small muted">${esc(record.course || "")}</div></td>
    <td>${result}</td>
    <td class="muted">${record.self_rating ? `${record.self_rating}/5` : "—"}</td>
    <td class="muted">${esc(record.notes || record.problems || "")}</td>
    <td class="small muted mono">${esc(record.artifact_id || "—")}<div>r${esc(record.revision || 0)}</div></td>
  </tr>`;
}
