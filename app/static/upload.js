// Batch uploader: sends media (+ matching Takeout .json sidecars) to /api/upload.

// Pure helpers below are top-level so tests/test_upload_js.py can load them under Node.

// Could `jsonName` be the Takeout sidecar of `mediaName`? Mirrors the name forms
// app/services/sidecar.py accepts; the server makes the final choice.
function sidecarMatches(mediaName, jsonName) {
  if (!/\.json$/i.test(jsonName)) return false;
  if (jsonName.startsWith(`${mediaName}.`)) return true;
  const m = mediaName.match(/^(.+)\((\d+)\)(\.[^.()]+)$/);
  if (m && jsonName.startsWith(`${m[1]}${m[3]}`) && jsonName.endsWith(`(${m[2]}).json`)) return true;
  // Takeout writes no sidecar for X-edited.jpg; it shares X.jpg's
  const e = mediaName.match(/^(.+)-edited(\.[^.]+)$/);
  return !!e && sidecarMatches(`${e[1]}${e[2]}`, jsonName);
}

// Split files into batches of at most maxCount files and maxBytes total;
// a file larger than maxBytes travels alone.
function makeBatches(files, maxCount, maxBytes) {
  const batches = [];
  let current = [];
  let bytes = 0;
  for (const f of files) {
    if (current.length && (current.length >= maxCount || bytes + f.size > maxBytes)) {
      batches.push(current);
      current = [];
      bytes = 0;
    }
    current.push(f);
    bytes += f.size;
  }
  if (current.length) batches.push(current);
  return batches;
}

(() => {
  const root = document.getElementById("upload");
  if (!root) return;

  const BATCH = Number(root.dataset.batchSize) || 20;
  const BATCH_BYTES = Number(root.dataset.batchMaxBytes) || 200 * 1024 * 1024;
  const MEDIA = /\.(jpe?g|png|heic|heif|webp|gif|mp4|mov)$/i;
  const LABEL = {
    new: "✅ New",
    exact_duplicate: "🟰 Exact duplicate",
    similar_exists: "≈ Similar exists",
    error: "⚠️ Error",
  };
  const counts = { uploaded: 0, new: 0, exact_duplicate: 0, similar_exists: 0, error: 0, ignored: 0 };
  const $ = (id) => document.getElementById(id);
  const drop = $("drop-zone");
  const results = $("upload-results");
  const progress = $("upload-progress");
  const summary = $("upload-summary");
  const retryButton = $("retry-failed");
  const queue = [];
  const failed = []; // { file, jsonByDir, row } for every errored file, for "Retry failed"
  let total = 0;
  let running = false;

  const relPath = (f) => f.relPath || f.webkitRelativePath || f.name;
  const dirOf = (p) => (p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "");

  function enqueue(files) {
    const jsonByDir = new Map();
    const media = [];
    for (const f of files) {
      const p = relPath(f);
      if (/\.json$/i.test(p)) {
        if (!jsonByDir.has(dirOf(p))) jsonByDir.set(dirOf(p), []);
        jsonByDir.get(dirOf(p)).push(f);
      } else if (MEDIA.test(p)) {
        media.push(f);
      } else {
        counts.ignored++;
      }
    }
    queueMedia(media, jsonByDir);
  }

  function queueMedia(media, jsonByDir) {
    for (const batch of makeBatches(media, BATCH, BATCH_BYTES)) {
      queue.push({ batch, jsonByDir });
    }
    total += media.length;
    renderSummary();
    if (!running) run();
  }

  // Re-send every errored file; its old row and counts are replaced by the new result.
  function retryFailed() {
    const items = failed.splice(0);
    const groups = new Map();
    for (const { file, jsonByDir, row } of items) {
      row.remove();
      if (!groups.has(jsonByDir)) groups.set(jsonByDir, []);
      groups.get(jsonByDir).push(file);
    }
    counts.uploaded -= items.length;
    counts.error -= items.length;
    total -= items.length;
    for (const [jsonByDir, media] of groups) queueMedia(media, jsonByDir);
  }

  function sidecarsFor(file, jsonByDir) {
    const p = relPath(file);
    const name = p.slice(p.lastIndexOf("/") + 1);
    return (jsonByDir.get(dirOf(p)) || []).filter((j) => sidecarMatches(name, j.name));
  }

  async function send({ batch, jsonByDir }) {
    const fd = new FormData();
    const add = (f) => {
      fd.append("files", f, f.name);
      fd.append("paths", relPath(f));
      fd.append("mtimes", String(f.lastModified || 0));
    };
    const sidecars = new Set();
    for (const f of batch) {
      add(f);
      sidecarsFor(f, jsonByDir).forEach((j) => sidecars.add(j));
    }
    sidecars.forEach(add);
    try {
      const res = await fetch("/api/upload", { method: "POST", body: fd });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return (await res.json()).results;
    } catch (e) {
      return batch.map((f) => ({ filename: relPath(f), status: "error", message: `Upload failed: ${e.message}` }));
    }
  }

  async function run() {
    running = true;
    while (queue.length) {
      progress.textContent = `Uploading ${counts.uploaded} / ${total}…`;
      const job = queue.shift();
      // Server returns one result per media file, in request order.
      (await send(job)).forEach((r, i) => {
        counts.uploaded++;
        counts[r.status] = (counts[r.status] || 0) + 1;
        const row = renderRow(r);
        if (r.status === "error") failed.push({ file: job.batch[i], jsonByDir: job.jsonByDir, row });
      });
      renderSummary();
    }
    progress.textContent = total ? `Done — ${counts.uploaded} / ${total}` : "";
    running = false;
    renderSummary();
  }

  function renderRow(r) {
    const li = document.createElement("li");
    li.className = `result ${r.status}`;
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = LABEL[r.status] || r.status;
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = r.filename;
    const msg = document.createElement("span");
    msg.className = "muted";
    msg.textContent = r.message || "";
    li.append(badge, name, msg);
    const thumbs = document.createElement("span");
    thumbs.className = "thumbs";
    if (r.existing_file_id != null) thumbs.append(thumb(r.existing_file_id, "existing copy"));
    for (const id of r.similar_file_ids || []) thumbs.append(thumb(id, "similar photo"));
    if (thumbs.childElementCount) li.append(thumbs);
    results.append(li);
    return li;
  }

  function thumb(fileId, alt) {
    const img = document.createElement("img");
    img.src = `/api/files/${fileId}/thumb`;
    img.alt = alt;
    img.title = `${alt} (#${fileId})`;
    img.loading = "lazy";
    img.onerror = () => img.remove();
    return img;
  }

  function renderSummary() {
    const parts = [
      `${counts.uploaded.toLocaleString()} uploaded`,
      `${counts.new.toLocaleString()} new`,
      `${counts.exact_duplicate.toLocaleString()} exact dupes skipped`,
      `${counts.similar_exists.toLocaleString()} similar`,
      `${counts.error.toLocaleString()} errors`,
    ];
    if (counts.ignored) parts.push(`${counts.ignored.toLocaleString()} ignored`);
    summary.textContent = parts.join(" · ");
    retryButton.hidden = running || failed.length === 0;
    retryButton.textContent = `Retry ${failed.length.toLocaleString()} failed`;
  }

  // Drag-and-drop with folder traversal. Collect entries synchronously: DataTransfer
  // items are invalid after the first await.
  async function filesFromDrop(dt) {
    const entries = [...dt.items].map((i) => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
    if (!entries.length) return [...dt.files];
    const out = [];
    async function walk(entry, prefix) {
      if (entry.isFile) {
        const f = await new Promise((res, rej) => entry.file(res, rej));
        f.relPath = prefix + f.name;
        out.push(f);
      } else if (entry.isDirectory) {
        const reader = entry.createReader();
        let batch;
        do {
          batch = await new Promise((res, rej) => reader.readEntries(res, rej));
          for (const e of batch) await walk(e, `${prefix}${entry.name}/`);
        } while (batch.length);
      }
    }
    for (const e of entries) await walk(e, "");
    return out;
  }

  for (const id of ["file-input", "folder-input"]) {
    $(id).addEventListener("change", (e) => {
      enqueue([...e.target.files]);
      e.target.value = "";
    });
  }
  retryButton.addEventListener("click", retryFailed);

  // Background analysis progress (embeddings + sharpness), polled every 2s.
  const analysis = $("analysis");
  async function pollStatus() {
    try {
      const s = await (await fetch("/api/status")).json();
      const total = s.pending + s.done + s.failed;
      analysis.hidden = total === 0;
      $("analysis-bar").max = total || 1;
      $("analysis-bar").value = s.done + s.failed;
      const failed = s.failed ? ` · ${s.failed.toLocaleString()} failed` : "";
      const state = s.pending ? (s.model.loaded === "true" ? "Analyzing" : "Loading model…") : "Analysis complete";
      $("analysis-text").textContent = s.error
        ? `Analysis paused: ${s.error}`
        : `${state} — ${(s.done + s.failed).toLocaleString()} / ${total.toLocaleString()}${failed}`;
    } catch {
      // server restarting; try again next tick
    }
    setTimeout(pollStatus, 2000);
  }
  pollStatus();
  drop.addEventListener("dragover", (e) => {
    e.preventDefault();
    drop.classList.add("over");
  });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", async (e) => {
    e.preventDefault();
    drop.classList.remove("over");
    enqueue(await filesFromDrop(e.dataTransfer));
  });
})();
