// First-run setup: choose (or browse for) the library folder, then start the app.

(() => {
  const root = document.getElementById("setup");
  if (!root) return;
  const form = document.getElementById("setup-form");
  const input = form.elements.path;
  const check = document.getElementById("setup-check");
  const status = form.querySelector(".status");
  const browseBtn = form.querySelector('[data-action="browse"]');
  const submitBtn = form.querySelector('[type="submit"]');
  const legacy = root.dataset.legacy;
  let timer = null;

  async function call(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw Object.assign(new Error(data.detail || `HTTP ${res.status}`), { status: res.status });
    return data;
  }

  async function requiredModelMissing() {
    try {
      const res = await fetch("/api/setup/models");
      const models = await res.json();
      return models.some((m) => m.required && m.status !== "installed");
    } catch {
      return false;
    }
  }

  function existingChoice() {
    const picked = form.querySelector('input[name="existing"]:checked');
    return picked ? picked.value : null;
  }

  async function runCheck() {
    const keep = existingChoice() === "keep";
    input.disabled = keep;
    browseBtn.disabled = keep;
    if (keep) {
      check.textContent = `OGphotos will keep using ${legacy}.`;
      return;
    }
    try {
      const r = await call("/api/setup/check", { path: input.value });
      if (r.photos !== null) check.textContent = `Existing OGphotos library with ${r.photos} photos at ${r.path}; it will be used.`;
      else check.textContent = r.exists ? `Photos will be kept in ${r.path}.` : `${r.path} will be created.`;
      check.classList.remove("error-text");
    } catch (err) {
      check.textContent = err.message;
      check.classList.add("error-text");
    }
  }

  function scheduleCheck() {
    clearTimeout(timer);
    timer = setTimeout(runCheck, 250);
  }

  input.addEventListener("input", scheduleCheck);
  form.addEventListener("change", (e) => {
    if (e.target.name === "existing") runCheck();
  });

  browseBtn.addEventListener("click", async () => {
    browseBtn.disabled = true;
    status.textContent = "A folder dialog is open (it may be behind this window)…";
    try {
      const r = await call("/api/browse-folder", { path: input.value, title: "Choose where OGphotos keeps your photos" });
      if (r.path) {
        input.value = r.path;
        runCheck();
      }
      status.textContent = "";
    } catch (err) {
      status.textContent = err.status === 501 ? "No folder dialog on this system; type the path instead." : err.message;
    } finally {
      browseBtn.disabled = false;
    }
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const choice = existingChoice();
    const body =
      choice === "keep" ? { path: legacy } : { path: input.value, move_existing: choice === "move" };
    submitBtn.disabled = true;
    status.textContent = choice === "move" ? "Moving your library…" : "Setting up…";
    try {
      await call("/api/setup", body);
      window.location.href = (await requiredModelMissing()) ? "/setup/models" : "/";
    } catch (err) {
      status.textContent = err.message;
      submitBtn.disabled = false;
    }
  });

  runCheck();
})();
