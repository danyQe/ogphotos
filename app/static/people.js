// People pages: rename, hide, merge, and fixing which faces belong to whom.

(() => {
  const picker = document.getElementById("person-picker");
  if (!picker) return;
  const status = document.querySelector(".status");

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

  function report(err) {
    if (status) status.textContent = `Failed: ${err.message}`;
  }

  // Resolves with { id, label } or { id: "new" }, or null when cancelled.
  async function pickPerson({ exclude = null, allowNew = false } = {}) {
    const { people } = await call("GET", "/api/people?hidden=true");
    const list = picker.querySelector(".picker-list");
    const labels = {};
    list.replaceChildren();
    for (const p of people) {
      if (p.person_id === exclude) continue;
      labels[p.person_id] = p.label;
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.value = String(p.person_id);
      btn.className = "picker-item";
      if (p.cover_face_id) {
        const img = document.createElement("img");
        img.src = `/api/faces/${p.cover_face_id}/crop`;
        img.alt = "";
        btn.append(img);
      }
      btn.append(document.createTextNode(p.label));
      li.append(btn);
      list.append(li);
    }
    picker.querySelector("[data-new]").hidden = !allowNew;
    picker.returnValue = "";
    picker.showModal();
    return new Promise((resolve) => {
      picker.addEventListener(
        "close",
        () => {
          const v = picker.returnValue;
          if (!v || v === "cancel") resolve(null);
          else if (v === "new") resolve({ id: "new" });
          else resolve({ id: Number(v), label: labels[v] });
        },
        { once: true },
      );
    });
  }

  const selected = () =>
    [...document.querySelectorAll(".face-pick input:checked")].map((i) => Number(i.value));

  // Rename (People grid and person page)
  for (const input of document.querySelectorAll(".person-name")) {
    input.addEventListener("change", async () => {
      const id = Number(input.dataset.personId);
      try {
        const r = await call("PATCH", `/api/people/${id}`, { name: input.value });
        if (r.same_name.length && window.confirm(`Someone is already called “${r.name}”. Are they the same person?`)) {
          await call("POST", `/api/people/${id}/merge`, { into_person_id: r.same_name[0] });
          window.location.href = `/people/${r.same_name[0]}`;
        }
      } catch (err) {
        report(err);
      }
    });
  }

  const person = document.getElementById("person");
  const personId = person ? Number(person.dataset.personId) : null;

  document.addEventListener("click", async (e) => {
    const el = e.target.closest("[data-action]");
    if (!el) return;
    try {
      switch (el.dataset.action) {
        case "hide":
          await call("PATCH", `/api/people/${personId}`, { hidden: el.dataset.hidden !== "true" });
          break;
        case "merge": {
          const target = await pickPerson({ exclude: personId });
          if (!target) return;
          if (!window.confirm(`Are ${person.dataset.label} and ${target.label} the same person?`)) return;
          await call("POST", `/api/people/${personId}/merge`, { into_person_id: target.id });
          window.location.href = `/people/${target.id}`;
          return;
        }
        case "not-me":
          for (const id of selected()) await call("POST", `/api/faces/${id}/remove`);
          break;
        case "move":
        case "assign": {
          const ids = selected();
          if (!ids.length) return;
          const target = await pickPerson({ exclude: personId, allowNew: true });
          if (!target) return;
          const body = target.id === "new" ? { face_ids: ids, new: true } : { face_ids: ids, person_id: target.id };
          await call("POST", "/api/faces/assign", body);
          break;
        }
        case "cover": {
          const ids = selected();
          if (ids.length !== 1) {
            if (status) status.textContent = "Select exactly one face.";
            return;
          }
          await call("PATCH", `/api/people/${personId}`, { cover_face_id: ids[0] });
          break;
        }
        default:
          return;
      }
      window.location.reload();
    } catch (err) {
      report(err);
    }
  });
})();
