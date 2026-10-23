// File page: move to Trash / restore.

(() => {
  const root = document.getElementById("file");
  if (!root) return;
  const status = root.querySelector(".status");

  root.addEventListener("click", async (e) => {
    const el = e.target.closest("[data-action]");
    if (!el) return;
    status.textContent = "Working…";
    const res = await fetch(`/api/files/${root.dataset.fileId}/${el.dataset.action}`, {
      method: "POST",
    });
    if (res.ok) window.location.reload();
    else status.textContent = `Failed: HTTP ${res.status}`;
  });

  const box = root.querySelector(".facebox");
  if (box) {
    for (const chip of root.querySelectorAll(".chip[data-box]")) {
      chip.addEventListener("mouseenter", () => {
        const [x, y, w, h] = chip.dataset.box.split(",").map(Number);
        Object.assign(box.style, { left: `${x * 100}%`, top: `${y * 100}%`, width: `${w * 100}%`, height: `${h * 100}%` });
        box.hidden = false;
      });
      chip.addEventListener("mouseleave", () => { box.hidden = true; });
    }
  }
})();
