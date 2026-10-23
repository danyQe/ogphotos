// Group review page: toggle keep/trash, keep best, apply, "not similar", keyboard shortcuts.

// Pure helper (top-level so tests/test_review_js.py can run it under Node):
// map a keydown to an action name, or null to let the browser handle it.
function keyAction(key, { tag = "", ctrl = false, meta = false, alt = false } = {}) {
  if (ctrl || meta || alt) return null;
  if (["INPUT", "TEXTAREA", "SELECT"].includes(tag)) return null;
  if (key === "k" || key === "K") return "keep-best";
  // Enter on a focused button/link activates that control instead
  if (key === "Enter") return tag === "BUTTON" || tag === "A" ? null : "apply";
  if (key === "ArrowRight") return "next";
  if (key === "ArrowLeft") return "prev";
  if (/^[1-9]$/.test(key)) return `toggle-${key}`;
  return null;
}

(() => {
  const root = typeof document !== "undefined" && document.getElementById("review");
  if (!root) return;

  const gid = root.dataset.groupId;
  const status = root.querySelector(".status");
  const applyBtn = root.querySelector('[data-action="apply"]');
  const members = [...root.querySelectorAll(".member")];
  let busy = false;

  const goTo = (id) => {
    window.location.href = id ? `/groups/${id}` : "/groups";
  };

  function marked() {
    return members.filter((li) => li.classList.contains("trash"));
  }

  function refresh() {
    const n = marked().length;
    applyBtn.firstChild.textContent =
      n === 0 ? "Mark reviewed " : n === members.length ? `Trash all ${n} ` : `Trash ${n}, keep ${members.length - n} `;
  }

  function toggle(li) {
    if (!li) return;
    const on = li.classList.toggle("trash");
    li.querySelector(".photo").setAttribute("aria-pressed", String(on));
    refresh();
  }

  async function post(url, body) {
    if (busy) return null;
    busy = true;
    status.textContent = "Working…";
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: body ? { "Content-Type": "application/json" } : {},
        body: body ? JSON.stringify(body) : undefined,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      return data;
    } catch (e) {
      status.textContent = `Failed: ${e.message}`;
      busy = false;
      return null;
    }
  }

  const ids = (lis) => lis.map((li) => Number(li.dataset.fileId));

  async function keepBest() {
    const r = await post(`/api/groups/${gid}/keep-best`);
    if (r) goTo(r.next_id);
  }

  async function apply() {
    const trash = marked();
    const keep = members.filter((li) => !trash.includes(li));
    const r = await post(`/api/groups/${gid}/apply`, { keep: ids(keep), trash: ids(trash) });
    if (r) goTo(r.next_id);
  }

  async function split(li) {
    const r = await post(`/api/groups/${gid}/remove/${li.dataset.fileId}`);
    if (!r) return;
    if (r.group_exists) window.location.reload();
    else goTo(r.next_id);
  }

  root.addEventListener("click", (e) => {
    const el = e.target.closest("[data-action], .photo");
    if (!el) return;
    if (el.classList.contains("photo")) toggle(el.closest(".member"));
    else if (el.dataset.action === "keep-best") keepBest();
    else if (el.dataset.action === "apply") apply();
    else if (el.dataset.action === "split") split(el.closest(".member"));
  });

  document.addEventListener("keydown", (e) => {
    // A photo keeps focus after a click; Enter there should still apply, not re-toggle.
    const onPhoto = e.target.classList && e.target.classList.contains("photo");
    const action = keyAction(e.key, {
      tag: onPhoto ? "" : e.target.tagName,
      ctrl: e.ctrlKey,
      meta: e.metaKey,
      alt: e.altKey,
    });
    if (!action) return;
    e.preventDefault();
    if (action === "keep-best") keepBest();
    else if (action === "apply") apply();
    else if (action === "next" && root.dataset.nextId) goTo(root.dataset.nextId);
    else if (action === "prev" && root.dataset.prevId) goTo(root.dataset.prevId);
    else if (action.startsWith("toggle-")) toggle(members[Number(action.slice(7)) - 1]);
  });

  refresh();
})();
