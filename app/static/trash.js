// Trash page: restore one, restore all, empty trash (with a confirmation showing the count).

(() => {
  const root = document.getElementById("trash");
  if (!root) return;
  const status = root.querySelector(".status");

  async function post(url) {
    status.textContent = "Working…";
    const res = await fetch(url, { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      status.textContent = `Failed: ${data.detail || `HTTP ${res.status}`}`;
      return null;
    }
    return data;
  }

  root.addEventListener("click", async (e) => {
    const el = e.target.closest("[data-action]");
    if (!el) return;
    const action = el.dataset.action;
    if (action === "restore") {
      const li = el.closest("li");
      if (await post(`/api/files/${li.dataset.fileId}/restore`)) window.location.reload();
    } else if (action === "restore-all") {
      if (await post("/api/trash/restore-all")) window.location.reload();
    } else if (action === "empty") {
      const n = root.dataset.count;
      const ok = window.confirm(
        `Permanently delete ${n} photo${n === "1" ? "" : "s"}? This cannot be undone.`,
      );
      if (ok && (await post("/api/trash/empty?confirm=true"))) window.location.reload();
    }
  });
})();
