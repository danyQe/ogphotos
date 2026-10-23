// Folder import page: start a server-side import and poll its progress and results.

(() => {
  const root = document.getElementById("import");
  if (!root) return;
  const form = document.getElementById("import-form");
  const status = form.querySelector(".status");
  const startBtn = form.querySelector('[type="submit"]');
  const cancelBtn = form.querySelector('[data-action="cancel"]');
  const progress = document.getElementById("import-progress");
  const bar = progress.querySelector("progress");
  const progressText = progress.querySelector("span");
  const summary = document.getElementById("import-summary");
  const list = document.getElementById("import-results");

  const LABEL = { new: "✅ New", exact_duplicate: "🟰 Exact duplicate", similar_exists: "≈ Similar exists", error: "⚠️ Error" };
  const SUMMARY = [["new", "new"], ["exact_duplicate", "exact dupes skipped"], ["similar_exists", "similar"], ["error", "errors"], ["date_fixed", "dates fixed from sidecars"]];
  let jobId = root.dataset.jobId;
  let offset = 0;

  async function call(method, url, body) {
    const res = await fetch(url, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
  }

  function renderRow(r) {
    const li = document.createElement("li");
    li.className = `result ${r.status}`;
    const badge = document.createElement("span");
    badge.textContent = LABEL[r.status] || r.status;
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = r.filename;
    const msg = document.createElement("span");
    msg.className = "muted";
    msg.textContent = r.message || "";
    const link = document.createElement("span");
    const id = r.file_id || r.existing_file_id;
    if (id) {
      const a = document.createElement("a");
      a.href = `/files/${id}`;
      a.textContent = "View";
      link.append(a);
    }
    li.append(badge, name, msg, link);
    return li;
  }

  function running(on) {
    startBtn.disabled = on;
    cancelBtn.hidden = !on;
    progress.hidden = false;
  }

  async function poll() {
    let job;
    try {
      job = await call("GET", `/api/import-folder/${jobId}?offset=${offset}`);
    } catch (err) {
      status.textContent = `Status unavailable: ${err.message}`;
      setTimeout(poll, 2000);
      return;
    }
    offset += job.results.length;
    // Newest on top; errors are the rows worth reading
    for (const r of job.results) list.prepend(renderRow(r));
    bar.max = Math.max(job.total, 1);
    bar.value = job.processed;
    progressText.textContent = `${job.processed} / ${job.total} files · ${job.path}`;
    summary.textContent = SUMMARY.filter(([k]) => job.counts[k])
      .map(([k, label]) => `${job.counts[k]} ${label}`)
      .join(" · ");
    if (job.status === "running") {
      setTimeout(poll, 1000);
      return;
    }
    running(false);
    status.textContent =
      job.status === "done" ? "Import finished. Analysis continues in the background."
      : job.status === "cancelled" ? "Import cancelled."
      : `Import failed: ${job.error}`;
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    status.textContent = "Starting…";
    try {
      const job = await call("POST", "/api/import-folder", { path: form.elements.path.value });
      jobId = job.id;
      offset = 0;
      list.replaceChildren();
      summary.textContent = "";
      status.textContent = "Importing…";
      running(true);
      poll();
    } catch (err) {
      status.textContent = err.message;
    }
  });

  form.querySelector('[data-action="browse"]').addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    status.textContent = "A folder dialog is open (it may be behind this window)…";
    try {
      const r = await call("POST", "/api/browse-folder", {
        path: form.elements.path.value,
        title: "Choose a folder to import",
      });
      if (r.path) form.elements.path.value = r.path;
      status.textContent = "";
    } catch (err) {
      status.textContent = err.message;
    } finally {
      btn.disabled = false;
    }
  });

  cancelBtn.addEventListener("click", () => {
    call("POST", `/api/import-folder/${jobId}/cancel`).catch(() => {});
    status.textContent = "Cancelling…";
  });

  if (jobId) {
    running(true);
    status.textContent = "Importing…";
    poll();
  }
})();
