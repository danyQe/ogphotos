// Settings page: save thresholds, run "Regroup all" and poll until it finishes.

(() => {
  const root = document.getElementById("settings");
  if (!root) return;
  const regroupBtn = root.querySelector('[data-action="regroup"]');
  const regroupStatus = document.getElementById("regroup-status");

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

  for (const form of document.querySelectorAll("form.tunables")) {
    const formStatus = form.querySelector(".status");
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const values = {};
      for (const input of form.querySelectorAll("input[name]")) values[input.name] = Number(input.value);
      formStatus.textContent = "Saving…";
      try {
        await call("PUT", "/api/settings", values);
        formStatus.textContent = "Saved.";
      } catch (err) {
        formStatus.textContent = `Not saved: ${err.message}`;
      }
    });
  }

  async function poll() {
    try {
      const { regroup } = await call("GET", "/api/settings");
      if (regroup.status === "running") {
        regroupStatus.textContent = "Regrouping…";
        setTimeout(poll, 1000);
        return;
      }
      regroupBtn.disabled = false;
      regroupStatus.textContent =
        regroup.status === "done" ? `Done: ${regroup.groups} groups.` : `Failed: ${regroup.error}`;
    } catch (err) {
      regroupStatus.textContent = `Status unavailable: ${err.message}`;
      setTimeout(poll, 2000);
    }
  }

  regroupBtn.addEventListener("click", async () => {
    const ok = window.confirm(
      "Regroup all photos with the current thresholds?\n\nAll reviewed marks will be cleared. “Not similar” choices are kept.",
    );
    if (!ok) return;
    regroupBtn.disabled = true;
    try {
      await call("POST", "/api/regroup?confirm=true");
      poll();
    } catch (err) {
      regroupBtn.disabled = false;
      regroupStatus.textContent = `Failed: ${err.message}`;
    }
  });

  if (regroupStatus.dataset.status === "running") poll();

  const faceForm = document.getElementById("face-settings");
  const reclusterStatus = document.getElementById("recluster-status");
  const reclusterBtn = faceForm.querySelector('[data-action="recluster"]');

  faceForm.querySelector('[data-action="scanning"]').addEventListener("change", async (e) => {
    try {
      await call("PUT", "/api/faces/scanning", { enabled: e.target.checked });
    } catch (err) {
      reclusterStatus.textContent = `Not saved: ${err.message}`;
    }
  });

  faceForm.querySelector('[data-action="retry-faces"]').addEventListener("click", async () => {
    const { queued } = await call("POST", "/api/faces/retry-failed");
    reclusterStatus.textContent = `${queued} photo(s) queued again.`;
  });

  async function pollRecluster() {
    const { faces } = await call("GET", "/api/settings");
    const st = faces.recluster;
    if (st.status === "running") {
      reclusterStatus.textContent = "Reclustering…";
      setTimeout(pollRecluster, 1000);
      return;
    }
    reclusterBtn.disabled = false;
    reclusterStatus.textContent = st.status === "done" ? `Done: ${st.people} people.` : `Failed: ${st.error}`;
  }

  reclusterBtn.addEventListener("click", async () => {
    reclusterBtn.disabled = true;
    try {
      await call("POST", "/api/faces/recluster");
      pollRecluster();
    } catch (err) {
      reclusterBtn.disabled = false;
      reclusterStatus.textContent = `Failed: ${err.message}`;
    }
  });

  if (reclusterStatus.dataset.status === "running") pollRecluster();
})();
