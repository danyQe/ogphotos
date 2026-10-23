// Model setup: one card per model; Download only after the license is accepted.

(() => {
  const root = document.getElementById("setup-models");
  if (!root) return;
  const list = document.getElementById("model-cards");
  const tpl = document.getElementById("model-card");
  const continueBtn = document.getElementById("models-continue");
  const status = document.getElementById("models-status");
  const inSetup = root.dataset.setup === "true";
  const cards = new Map(); // id -> card element
  let timer = null;

  async function call(url, body) {
    const opts = body === undefined ? {} : {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    };
    const res = await fetch(url, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw Object.assign(new Error(data.detail || `HTTP ${res.status}`), { status: res.status });
    return data;
  }

  function bytes(n) {
    if (n >= 1e9) return `${(n / 1e9).toFixed(1)} GB`;
    if (n >= 1e6) return `${(n / 1e6).toFixed(0)} MB`;
    return `${Math.max(1, Math.round(n / 1e3))} KB`;
  }

  function field(card, name) {
    return card.querySelector(`[data-field="${name}"]`);
  }

  function create(m) {
    const card = tpl.content.firstElementChild.cloneNode(true);
    card.dataset.id = m.id;
    field(card, "name").textContent = m.name;
    field(card, "badge").textContent = m.required ? "Required" : "Optional";
    field(card, "purpose").textContent = m.purpose;
    field(card, "size").textContent = bytes(m.size);
    const link = field(card, "license");
    link.textContent = m.license_name;
    link.href = m.license_url;
    field(card, "license_summary").textContent = m.license_summary;
    field(card, "license_name").textContent = m.license_name;
    const accept = card.querySelector('[data-action="accept"]');
    const download = card.querySelector('[data-action="download"]');
    const form = card.querySelector('[data-action="import"]');
    const showImport = card.querySelector('[data-action="show-import"]');

    accept.addEventListener("change", () => update(card, card.model));
    download.addEventListener("click", () => act(card, `/api/setup/models/${m.id}/install`, { accept_license: accept.checked }));
    card.querySelector('[data-action="cancel"]').addEventListener("click", () => act(card, `/api/setup/models/${m.id}/cancel`, {}));
    showImport.addEventListener("click", () => {
      form.hidden = !form.hidden;
      if (!form.hidden) form.elements.zip_path.focus();
    });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const button = form.querySelector('[type="submit"]');
      button.disabled = true;
      field(card, "progress").textContent = "Checking the zip…";
      await act(card, `/api/setup/models/${m.id}/import`, { zip_path: form.elements.zip_path.value });
      button.disabled = false;
    });
    list.append(card);
    cards.set(m.id, card);
    return card;
  }

  async function act(card, url, body) {
    field(card, "error").textContent = "";
    try {
      await call(url, body);
    } catch (err) {
      field(card, "error").textContent = err.message;
    }
    refresh();
  }

  function update(card, m) {
    card.model = m;
    const accepted = card.querySelector('[data-action="accept"]').checked;
    const installed = m.status === "installed";
    const downloading = m.status === "downloading";
    const download = card.querySelector('[data-action="download"]');
    download.hidden = installed || downloading;
    download.disabled = !accepted || inSetup;
    download.textContent = m.done > 0 && !installed ? "Resume" : "Download";
    card.querySelector('[data-action="cancel"]').hidden = !downloading;
    card.querySelector('[data-action="show-import"]').hidden = installed || downloading;
    card.querySelector('[data-action="show-import"]').disabled = !accepted || inSetup;
    if (installed || downloading || !accepted) card.querySelector('[data-action="import"]').hidden = true;
    card.querySelector("label.license").hidden = installed;
    const bar = card.querySelector("progress");
    bar.hidden = !downloading && !(m.done > 0 && !installed);
    bar.value = m.total ? m.done / m.total : 0;
    let text = "";
    if (installed) text = "Installed.";
    else if (downloading) {
      const pct = m.total ? Math.floor((100 * m.done) / m.total) : 0;
      const speed = m.bytes_per_s ? ` · ${bytes(m.bytes_per_s)}/s` : "";
      text = `Downloading… ${bytes(m.done)} of ${bytes(m.total)} (${pct}%)${speed}`;
    } else if (m.done > 0) text = `Paused at ${bytes(m.done)} of ${bytes(m.total)}.`;
    field(card, "progress").textContent = text;
    if (m.status === "error") field(card, "error").textContent = m.error;
  }

  async function refresh() {
    clearTimeout(timer);
    let models;
    try {
      models = await call("/api/setup/models");
    } catch (err) {
      status.textContent = err.message;
      timer = setTimeout(refresh, 3000);
      return;
    }
    status.textContent = inSetup ? "Choose a library folder before downloading models." : "";
    for (const m of models) update(cards.get(m.id) || create(m), m);
    continueBtn.hidden = inSetup || !models.filter((m) => m.required).every((m) => m.status === "installed");
    if (models.some((m) => m.status === "downloading")) timer = setTimeout(refresh, 1000);
  }

  refresh();
})();
