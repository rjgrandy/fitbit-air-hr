// Pins and free-form tags. Stored only in the app's own SQLite database, never sent to Google.

async function put(path, body) {
  const r = await fetch(path, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);
  return j;
}

export const savePinned = (id, pinned) => put(`/api/workouts/${encodeURIComponent(id)}/pin`, { pinned });
export const saveTags = (id, tags) => put(`/api/workouts/${encodeURIComponent(id)}/tags`, { tags });

let suggestions = [];
/** Load existing tags into a shared <datalist id="tagSuggestions"> used for autocomplete. */
export async function refreshTagSuggestions() {
  try { suggestions = await (await fetch("/api/tags")).json(); } catch { suggestions = []; }
  let dl = document.getElementById("tagSuggestions");
  if (!dl) { dl = document.createElement("datalist"); dl.id = "tagSuggestions"; document.body.appendChild(dl); }
  dl.innerHTML = suggestions.map((t) => `<option value="${esc(t.tag)}"></option>`).join("");
  return suggestions;
}
export const tagSuggestions = () => suggestions;

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/** A pin star toggle button. */
export function pinButton(btn, { id, pinned, onChange }) {
  const paint = (p) => {
    btn.textContent = p ? "★" : "☆";
    btn.classList.toggle("on", p);
    btn.title = p ? "Unpin" : "Pin";
    btn.setAttribute("aria-pressed", String(p));
  };
  paint(pinned);
  btn.onclick = async (e) => {
    e.preventDefault(); e.stopPropagation();
    const next = !btn.classList.contains("on");
    paint(next);
    try { const r = await savePinned(id, next); paint(r.pinned); onChange?.(r.pinned); }
    catch { paint(!next); }
  };
}

/** Inline tag editor: chips with remove buttons plus an "+ tag" input with autocomplete. */
export function tagEditor(el, { id, tags, onChange, compact = false }) {
  let current = [...tags];
  el.classList.add("tags");
  const render = () => {
    el.innerHTML = current.map((t) => `<span class="tag">${esc(t)}<button type="button" data-rm="${esc(t)}" aria-label="Remove tag ${esc(t)}">×</button></span>`).join("") +
      `<button type="button" class="tag-add" title="Add tag">+ tag</button>`;
    el.querySelectorAll("[data-rm]").forEach((b) => b.onclick = (e) => {
      e.preventDefault(); e.stopPropagation();
      commit(current.filter((t) => t !== b.dataset.rm));
    });
    el.querySelector(".tag-add").onclick = (e) => { e.preventDefault(); e.stopPropagation(); openInput(); };
    el.classList.toggle("empty", !current.length);
  };
  const openInput = () => {
    const input = document.createElement("input");
    input.type = "text"; input.className = "tag-input"; input.setAttribute("list", "tagSuggestions");
    input.placeholder = "tag"; input.maxLength = 40; input.enterKeyHint = "done";
    el.querySelector(".tag-add").replaceWith(input);
    input.focus();
    let done = false;
    const finish = (save) => {
      if (done) return; done = true;
      const parts = input.value.split(",").map((s) => s.trim()).filter(Boolean);
      if (save && parts.length) commit([...current, ...parts]); else render();
    };
    input.onkeydown = (e) => {
      e.stopPropagation();
      if (e.key === "Enter") { e.preventDefault(); finish(true); }
      else if (e.key === "Escape") { e.preventDefault(); finish(false); }
    };
    input.onclick = (e) => e.stopPropagation();
    input.onblur = () => finish(true);
    // Picking from the autocomplete list commits immediately.
    input.oninput = (e) => { if (e.inputType === "insertReplacementText" || e.inputType == null) finish(true); };
  };
  // Saves run one at a time, in order. Edits made while a save is in flight are queued, and only
  // the newest one is sent next, so an older response can never overwrite a newer edit.
  let confirmed = [...tags], saving = false, queued = null;
  const commit = (next) => {
    current = next; render();
    queued = next;
    if (!saving) flush();
  };
  const flush = async () => {
    saving = true;
    while (queued) {
      const want = queued; queued = null;
      try {
        const r = await saveTags(id, want);
        confirmed = r.tags;
        if (!queued) { current = r.tags; render(); onChange?.(current); refreshTagSuggestions(); }
      } catch {
        if (!queued) { current = [...confirmed]; render(); }
      }
    }
    saving = false;
  };
  el.onclick = (e) => e.stopPropagation();
  render();
  return { set(t) { current = [...t]; confirmed = [...t]; render(); } };
}
