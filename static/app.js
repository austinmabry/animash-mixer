(() => {
  const $ = (id) => document.getElementById(id);
  const el = {
    snap: $("snap"), status: $("status"), preview: $("preview"), working: $("working"),
    workingText: $("working-text"), error: $("error"), shelf: $("shelf"), chips: $("chips"),
    shelfCount: $("shelf-count"), addInput: $("add-input"), addBtn: $("add-btn"),
    list: $("animal-list"), results: $("results"), mixes: $("mixes"), note: $("results-note"),
    more: $("more"), reset: $("reset"), dataNote: $("data-note"), manual: $("manual"),
    cam: $("cam"), pick: $("pick"),
  };

  let known = [];
  let shelf = [];
  let topN = 5;

  // ---------------------------------------------------------------- boot
  fetch("/api/animals").then((r) => r.json()).then((names) => {
    known = names;
    el.list.innerHTML = names.map((n) => `<option value="${esc(n)}">`).join("");
  });
  fetch("/api/health").then((r) => r.json()).then((h) => {
    el.dataNote.textContent = h.sample_data
      ? "Running on sample data (Dragon only). Run the scraper to load every animal."
      : `${h.fusions.toLocaleString()} fusions across ${h.animals} animals · updated ${(h.scraped_at || "").slice(0, 10)}`;
  });

  // ---------------------------------------------------------------- input
  el.cam.addEventListener("change", onFile);
  el.pick.addEventListener("change", onFile);
  el.manual.addEventListener("click", () => { showShelf(); el.addInput.focus(); });
  el.reset.addEventListener("click", resetAll);
  el.addBtn.addEventListener("click", addFromInput);
  el.addInput.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addFromInput(); } });
  el.addInput.addEventListener("change", () => { if (known.includes(el.addInput.value)) addFromInput(); });
  el.more.addEventListener("click", () => { topN += 5; refreshMixes(); });

  async function onFile(e) {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    e.target.value = "";
    showStatus(file);
    setWorking(true, "Finding animals…");
    const body = new FormData();
    body.append("image", file);
    try {
      const r = await fetch("/api/analyze", { method: "POST", body });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || `Server said ${r.status}`);
      shelf = data.available;
      topN = 5;
      showShelf();
      renderMixes(data);
      if (!shelf.length) showError("No animals recognised in that picture. Try a clearer shot of the shelf, or add them by name below.");
    } catch (err) {
      showError(err.message);
      showShelf();
    } finally {
      setWorking(false);
    }
  }

  function addFromInput() {
    const v = el.addInput.value.trim();
    if (!v) return;
    const match = known.find((n) => n.toLowerCase() === v.toLowerCase())
      || known.find((n) => n.toLowerCase().startsWith(v.toLowerCase()));
    if (!match) { showError(`"${v}" isn't an animal in the catalog.`); return; }
    hideError();
    if (!shelf.includes(match)) shelf.push(match);
    el.addInput.value = "";
    showShelf();
    refreshMixes();
  }

  function removeAnimal(name) {
    shelf = shelf.filter((n) => n !== name);
    showShelf();
    refreshMixes();
  }

  async function refreshMixes() {
    if (shelf.length < 2) { renderMixes({ mixes: [], total_known: 0, missing: [], available: shelf }); return; }
    setWorking(true, "Ranking mixes…");
    try {
      const r = await fetch("/api/mixes", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ animals: shelf, top_n: topN }),
      });
      renderMixes(await r.json());
    } finally { setWorking(false); }
  }

  // --------------------------------------------------------------- render
  function showStatus(file) {
    el.status.hidden = false;
    hideError();
    const url = URL.createObjectURL(file);
    el.preview.src = url;
    el.preview.hidden = false;
    el.preview.onload = () => URL.revokeObjectURL(url);
  }

  function setWorking(on, text) {
    el.status.hidden = false;
    el.working.hidden = !on;
    if (text) el.workingText.textContent = text;
  }

  function showError(msg) { el.status.hidden = false; el.error.textContent = msg; el.error.hidden = false; }
  function hideError() { el.error.hidden = true; }

  function showShelf() {
    el.snap.hidden = true;
    el.reset.hidden = false;
    el.shelf.hidden = false;
    el.shelfCount.textContent = shelf.length ? `${shelf.length} animal${shelf.length === 1 ? "" : "s"}` : "";
    el.chips.innerHTML = shelf.map((n) =>
      `<li class="chip">${esc(n)}<button type="button" aria-label="Remove ${esc(n)}" data-name="${esc(n)}">×</button></li>`
    ).join("");
    el.chips.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => removeAnimal(b.dataset.name)));
  }

  function renderMixes(data) {
    el.results.hidden = false;
    const mixes = data.mixes || [];
    if (!mixes.length) {
      el.mixes.innerHTML = `<li class="empty">${shelf.length < 2 ? "Add at least two animals to see mixes." : "None of these pairs are in the catalog yet."}</li>`;
      el.note.textContent = "";
      el.more.hidden = true;
      return;
    }
    el.mixes.innerHTML = mixes.map((m, i) => `
      <li class="mix${i === 0 ? " best" : ""}">
        <div class="stars">${m.stars}<small>stars</small></div>
        <div class="mix-body">
          <div class="mix-name">${esc(m.name || "Unnamed fusion")}</div>
          <div class="mix-parents">${esc(m.parents[0])} + ${esc(m.parents[1])}</div>
          ${m.tier ? `<div class="mix-tier">${esc(m.tier)}</div>` : ""}
        </div>
        ${m.icon ? `<img src="${esc(m.icon)}" alt="" loading="lazy">` : ""}
      </li>`).join("");
    const missing = (data.missing || []).length;
    el.note.textContent = `${data.total_known} known mixes on this shelf` + (missing ? ` · ${missing} pair${missing === 1 ? "" : "s"} not on the wiki yet` : "");
    el.more.hidden = mixes.length >= data.total_known;
  }

  function resetAll() {
    shelf = []; topN = 5;
    el.snap.hidden = false; el.status.hidden = true; el.shelf.hidden = true; el.results.hidden = true;
    el.preview.hidden = true; el.reset.hidden = true; hideError();
    window.scrollTo({ top: 0 });
  }

  function esc(s) { return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
})();
