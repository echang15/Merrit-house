/* Merrit House tap list front-end. Vanilla JS, no build step. */
(function () {
  "use strict";

  const $ = (sel, root) => (root || document).querySelector(sel);
  const board = $("#board");
  const sheet = $("#sheet");
  const sheetContent = $("#sheet-content");
  const sheetPanel = $(".sheet-panel");
  const oskEl = $("#osk");
  const toastEl = $("#toast");

  const POLL_MS = 4000;
  const IDLE_MS = 90000;

  let state = { version: 0, taps: [], house: "", tap_count: 3 };
  let idleTimer = null;
  let searchSeq = 0;

  // ---------- utils ----------

  const esc = (s) =>
    String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const html = (strings, ...vals) => strings.reduce((out, str, i) => out + str + (i < vals.length ? vals[i] : ""), "");

  function fmtAbv(v) { return v == null || v === "" ? "" : `${Number(v).toFixed(1).replace(/\.0$/, "")}%`; }
  function fmtIbu(v) { return v == null || v === "" ? "" : `${Math.round(Number(v))}`; }

  function ago(ts) {
    if (!ts) return "";
    const days = Math.floor((Date.now() / 1000 - ts) / 86400);
    if (days <= 0) return "tapped today";
    if (days === 1) return "tapped yesterday";
    if (days < 30) return `on tap ${days} days`;
    return `tapped ${new Date(ts * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;
  }
  function fmtDate(ts) {
    return ts ? new Date(ts * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "";
  }

  async function api(url, opts = {}) {
    const init = { method: opts.method || "GET", headers: {} };
    if (opts.json !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.json);
    } else if (opts.form) {
      init.body = opts.form;
    }
    const resp = await fetch(url, init);
    let data = null;
    try { data = await resp.json(); } catch (_) { /* no body */ }
    if (!resp.ok) throw new Error((data && data.error) || `${resp.status} ${resp.statusText}`);
    return data;
  }

  function toast(msg, isError) {
    toastEl.textContent = msg;
    toastEl.classList.toggle("error", !!isError);
    toastEl.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { toastEl.hidden = true; }, isError ? 4500 : 2200);
  }

  // Beer colour by style, used for the generated label placeholder.
  const STYLE_COLORS = [
    [/imperial stout|stout|porter|black/i, ["#5a3320", "#120806", "rgba(255,236,210,0.75)"]],
    [/brown|dunkel|bock|schwarz|scotch|old ale|barley/i, ["#a25b23", "#3b1d0a", "rgba(255,236,210,0.8)"]],
    [/amber|red|märzen|marzen|oktoberfest|vienna|esb|bitter/i, ["#e07a2c", "#6f2f0c", "rgba(255,236,210,0.85)"]],
    [/sour|gose|lambic|kriek|fruit|berliner|framboise|rosé|rose/i, ["#ff8aa1", "#9a2a49", "rgba(255,236,210,0.85)"]],
    [/wheat|weizen|wit|hefe|blanche|white/i, ["#ffd77a", "#c78a1c"]],
    [/pils|lager|helles|kölsch|kolsch|blonde|golden|light|cream/i, ["#ffe27a", "#d19b1c"]],
    [/ipa|pale|hazy|neipa|session/i, ["#ffbe4a", "#b8641a"]],
    [/cider|mead|perry/i, ["#d9f28b", "#6f8f1f"]],
    [/saison|farmhouse|tripel|belgian|abbey|trappist|dubbel|quad/i, ["#f6b64d", "#8c4a12"]],
  ];
  function styleColors(style) {
    for (const [re, colors] of STYLE_COLORS) if (re.test(style || "")) return colors;
    return ["#f7c96b", "#a35a12"];
  }
  function initials(name) {
    const words = String(name || "").replace(/[^\w\s]/g, "").split(/\s+/).filter(Boolean);
    if (!words.length) return "?";
    return words.slice(0, 2).map((w) => w[0].toUpperCase()).join("");
  }

  function artHTML(beer, opts = {}) {
    if (beer && beer.label) {
      const src = esc(beer.label);
      const blur = opts.blur ? html`<div class="tap-art-blur" style="background-image:url('${src}')"></div>` : "";
      return html`${blur}<img class="${opts.cls || ""}" src="${src}" alt="" loading="lazy" onerror="this.closest('[data-art]').innerHTML = window.__genLabel(this.dataset.b)" data-b="${esc(JSON.stringify({ name: beer.name, style: beer.style }))}">`;
    }
    return genLabel(beer);
  }
  function genLabel(beer) {
    const b = typeof beer === "string" ? JSON.parse(beer) : beer || {};
    const [hi, lo, ink] = styleColors(b.style);
    return html`<div class="label-gen" style="--beer-hi:${hi};--beer-lo:${lo};${ink ? `--beer-ink:${ink}` : ""}">
      <div class="label-gen-initials">${esc(initials(b.name))}</div>
      <div class="label-gen-style">${esc(b.style || "")}</div>
    </div>`;
  }
  window.__genLabel = genLabel;

  // ---------- board ----------

  function renderBoard() {
    document.documentElement.style.setProperty("--taps", state.tap_count || state.taps.length || 3);
    $("#house-name").textContent = state.house || "On Tap";
    document.title = `${state.house || "On Tap"} · On Tap`;
    board.innerHTML = state.taps.map((tap) => {
      const b = tap.beer;
      if (!b) {
        return html`<button class="tap empty" type="button" data-tap="${tap.number}">
          <span class="tap-badge">TAP <small></small>${tap.number}</span>
          <span class="tap-empty-plus"><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg></span>
          <span class="tap-empty-text">Nothing pouring</span>
        </button>`;
      }
      return html`<button class="tap" type="button" data-tap="${tap.number}">
        <div class="tap-art" data-art>${artHTML(b, { blur: true, cls: "tap-art-img" })}</div>
        <span class="tap-badge">TAP ${tap.number}</span>
        <div class="tap-body">
          <h2 class="tap-name">${esc(b.name)}</h2>
          <div class="tap-brewery">${esc(b.brewery)}</div>
          <div class="tap-meta">
            ${b.style ? html`<span class="pill pill-style">${esc(b.style)}</span>` : ""}
            ${b.abv != null ? html`<span class="pill"><b>${fmtAbv(b.abv)}</b> ABV</span>` : ""}
            ${b.ibu != null ? html`<span class="pill"><b>${fmtIbu(b.ibu)}</b> IBU</span>` : ""}
          </div>
          ${b.description ? html`<p class="tap-desc">${esc(b.description.split("\n")[0])}</p>` : ""}
          <div class="tap-since">${esc(ago(tap.tapped_at))}</div>
        </div>
      </button>`;
    }).join("");
    fitDescriptions();
  }

  // Show as many whole lines of the tasting note as the card has room for.
  function fitDescriptions() {
    board.querySelectorAll(".tap-body").forEach((body) => {
      const desc = body.querySelector(".tap-desc");
      if (!desc) return;
      const bodyStyle = getComputedStyle(body);
      const descStyle = getComputedStyle(desc);
      const lineHeight = parseFloat(descStyle.lineHeight) || 19;
      const gap = parseFloat(bodyStyle.rowGap) || 0;
      let used = parseFloat(bodyStyle.paddingTop) + parseFloat(bodyStyle.paddingBottom) + parseFloat(descStyle.marginTop);
      for (const child of body.children) {
        if (child !== desc) used += child.offsetHeight;
        used += gap;
      }
      used -= gap;
      const lines = Math.floor((body.clientHeight - used) / lineHeight);
      desc.style.webkitLineClamp = String(Math.max(1, lines));
    });
  }
  let resizeTimer = null;
  window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(fitDescriptions, 150); });

  async function loadState() {
    try {
      const data = await api("/api/state");
      const changed = data.version !== state.version;
      state = data;
      if (changed) renderBoard();
    } catch (err) {
      console.warn("state", err);
    }
  }

  async function poll() {
    try {
      const { version } = await api("/api/version");
      if (version !== state.version) await loadState();
    } catch (_) { /* server may be restarting */ }
  }

  function applyState(data) {
    state = data;
    renderBoard();
  }

  // ---------- sheet ----------

  function openSheet(contentHTML, opts = {}) {
    sheetContent.innerHTML = contentHTML;
    sheetPanel.classList.toggle("compact", !!opts.compact);
    sheet.hidden = false;
    bumpIdle();
  }
  function closeSheet() {
    sheet.hidden = true;
    sheetContent.innerHTML = "";
    osk.hide();
    clearTimeout(idleTimer);
  }
  function bumpIdle() {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(closeSheet, IDLE_MS);
  }
  sheet.addEventListener("click", (e) => { if (e.target.closest("[data-close]")) closeSheet(); });
  sheet.addEventListener("pointerdown", bumpIdle, true);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !sheet.hidden) closeSheet(); });

  const closeBtn = () => html`<button class="icon-btn" type="button" data-close aria-label="Close"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg></button>`;
  const backBtn = (action) => html`<button class="icon-btn" type="button" data-action="${action}" aria-label="Back"><svg viewBox="0 0 24 24"><path d="M15 6l-6 6 6 6"/></svg></button>`;

  // ---------- tap menu ----------

  function openTapMenu(number) {
    const tap = state.taps.find((t) => t.number === number);
    if (!tap) return;
    const b = tap.beer;
    if (!b) return openSearch(number);
    openSheet(html`
      <div class="sheet-head">
        <div style="flex:1;min-width:0">
          <div class="sheet-sub">Tap ${number}</div>
          <h2 class="sheet-title">${esc(b.name)}</h2>
        </div>
        ${closeBtn()}
      </div>
      <div class="sheet-body">
        <div class="menu-hero">
          <div class="menu-hero-art" data-art>${artHTML(b)}</div>
          <div class="menu-hero-text">
            <h3>${esc(b.brewery || "")}</h3>
            <p>${[b.style, b.abv != null ? fmtAbv(b.abv) + " ABV" : "", b.ibu != null ? fmtIbu(b.ibu) + " IBU" : ""].filter(Boolean).map(esc).join(" · ")}</p>
            <p style="margin-top:6px;font-size:12px;color:var(--ink-3)">${esc(ago(tap.tapped_at))}</p>
          </div>
        </div>
        ${b.description ? html`<p style="color:var(--ink-2);font-size:14px;line-height:1.5;margin:0 0 14px;white-space:pre-line">${esc(b.description)}</p>` : ""}
        <div class="btn-stack">
          <button class="btn btn-primary btn-lg" type="button" data-action="search" data-tap="${number}">
            <svg viewBox="0 0 24 24"><path d="M4 12h10M10 6l6 6-6 6M18 5v14"/></svg> Change beer on tap ${number}
          </button>
          <button class="btn" type="button" data-action="edit-beer" data-id="${b.id}" data-tap="${number}">
            <svg viewBox="0 0 24 24"><path d="M4 20h4l10-10-4-4L4 16zM13 7l4 4"/></svg> Edit details
          </button>
          <button class="btn btn-danger" type="button" data-action="kick" data-tap="${number}">
            <svg viewBox="0 0 24 24"><path d="M5 7h14M9 7V4h6v3M7 7l1 13h8l1-13"/></svg> Keg kicked — clear tap ${number}
          </button>
        </div>
      </div>`, { compact: true });
  }

  async function kickTap(number) {
    try {
      applyState(await api(`/api/taps/${number}`, { method: "DELETE" }));
      closeSheet();
      toast(`Tap ${number} cleared — beer moved to the archive`);
    } catch (err) { toast(err.message, true); }
  }

  // ---------- search ----------

  function openSearch(targetTap) {
    openSheet(html`
      <div class="sheet-head">
        <div style="flex:1;min-width:0">
          <div class="sheet-sub">${targetTap ? `Choose a beer for tap ${targetTap}` : "Find a beer"}</div>
          <h2 class="sheet-title">Search beers</h2>
        </div>
        <button class="icon-btn" type="button" data-action="toggle-osk" aria-label="Keyboard"><svg viewBox="0 0 24 24"><path d="M3 7h18v10H3zM7 11h1M11 11h1M15 11h1M7 14h10"/></svg></button>
        ${closeBtn()}
      </div>
      <div class="search-row">
        <div class="search-field">
          <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg>
          <input id="search-input" class="search-input" type="search" placeholder="Beer, brewery or style…" autocomplete="off" autocorrect="off" spellcheck="false" enterkeyhint="search">
          <button class="search-clear" type="button" data-action="clear-search" aria-label="Clear"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg></button>
        </div>
        <button class="btn" type="button" data-action="manual" data-tap="${targetTap || ""}">
          <svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg> Manual
        </button>
      </div>
      <div class="sheet-body"><div id="results" class="results"></div></div>`);

    const input = $("#search-input");
    const field = input.closest(".search-field");
    let timer = null;
    input.addEventListener("input", () => {
      field.classList.toggle("has-text", !!input.value);
      clearTimeout(timer);
      timer = setTimeout(() => runSearch(input.value, targetTap), 350);
    });
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") { clearTimeout(timer); runSearch(input.value, targetTap); } });
    osk.attach(input, () => { clearTimeout(timer); runSearch(input.value, targetTap); });
    input.focus({ preventScroll: true });
    if (osk.enabled) osk.show(input);
    showRecent(targetTap);
  }

  async function showRecent(targetTap) {
    const results = $("#results");
    if (!results) return;
    results.innerHTML = `<div class="spinner"></div>`;
    try {
      const { beers } = await api("/api/archive");
      if (!$("#results") || $("#search-input").value) return;
      if (!beers.length) {
        results.innerHTML = html`<div class="empty-state"><strong>Type to search</strong>Find a beer online, or tap <b>Manual</b> to add your own.</div>`;
        return;
      }
      results.innerHTML = html`<div class="section-label">Recently on tap</div>` +
        beers.slice(0, 12).map((b) => resultHTML({ ...b, beer_id: b.id, source: "library" }, targetTap)).join("");
    } catch (_) {
      results.innerHTML = "";
    }
  }

  function resultHTML(r, targetTap) {
    const key = ++resultHTML.seq;
    resultHTML.cache[key] = r;
    const src = r.source === "library" ? "In your library" : (state.providers.find((p) => p.source === r.source) || {}).label || r.source;
    return html`<button class="result" type="button" data-action="pick" data-key="${key}" data-tap="${targetTap || ""}">
      <div class="result-art" data-art>${artHTML(r)}</div>
      <div class="result-text">
        <div class="result-name">${esc(r.name)}</div>
        <div class="result-sub">${esc(r.brewery || "Unknown brewery")}</div>
        <div class="result-tags">
          ${r.style ? html`<span class="pill pill-style">${esc(r.style)}</span>` : ""}
          ${r.abv != null ? html`<span class="pill">${fmtAbv(r.abv)}</span>` : ""}
          <span class="source-tag ${r.source === "library" ? "library" : ""}">${r.on_tap ? "On tap now · " : ""}${esc(src)}</span>
        </div>
      </div>
      <svg class="result-go" viewBox="0 0 24 24"><path d="M9 6l6 6-6 6"/></svg>
    </button>`;
  }
  resultHTML.seq = 0;
  resultHTML.cache = {};

  async function runSearch(q, targetTap) {
    const results = $("#results");
    if (!results) return;
    q = q.trim();
    if (q.length < 2) { showRecent(targetTap); return; }
    const seq = ++searchSeq;
    results.innerHTML = `<div class="spinner"></div>`;
    try {
      const data = await api(`/api/search?q=${encodeURIComponent(q)}`);
      if (seq !== searchSeq || !$("#results")) return;
      const errs = data.errors.map((e) => html`<div class="notice warn">${esc(e.label)} search unavailable (${esc(e.error).slice(0, 80)})</div>`).join("");
      if (!data.results.length) {
        results.innerHTML = errs + html`<div class="empty-state"><strong>No beers found for “${esc(q)}”</strong>Try the brewery name, or add it manually.</div>`;
        return;
      }
      results.innerHTML = errs + data.results.map((r) => resultHTML(r, targetTap)).join("");
    } catch (err) {
      if (seq === searchSeq && $("#results")) results.innerHTML = html`<div class="empty-state">${esc(err.message)}</div>`;
    }
  }

  // ---------- editor ----------

  function openEditor(beer, targetTap, opts = {}) {
    const isLibrary = beer.beer_id != null || (beer.id != null && beer.source !== undefined && beer.source !== "openfoodfacts" && beer.source !== "untappd" && opts.fromLibrary);
    const beerId = beer.beer_id != null ? beer.beer_id : (opts.fromLibrary ? beer.id : null);
    const tapButtons = targetTap
      ? html`<button class="btn btn-primary btn-lg btn-grow" type="button" data-action="save" data-tap="${targetTap}">Put on tap ${targetTap}</button>`
      : html`<div class="tap-choice">${state.taps.map((t) => html`<button class="btn btn-primary" type="button" data-action="save" data-tap="${t.number}">Tap ${t.number}</button>`).join("")}</div>`;
    openSheet(html`
      <div class="sheet-head">
        ${opts.back ? backBtn(opts.back) : ""}
        <div style="flex:1;min-width:0">
          <div class="sheet-sub">${beerId != null ? "Edit beer" : "New beer"}${targetTap ? ` · tap ${targetTap}` : ""}</div>
          <h2 class="sheet-title">${esc(beer.name || "Add a beer")}</h2>
        </div>
        <button class="icon-btn" type="button" data-action="toggle-osk" aria-label="Keyboard"><svg viewBox="0 0 24 24"><path d="M3 7h18v10H3zM7 11h1M11 11h1M15 11h1M7 14h10"/></svg></button>
        ${closeBtn()}
      </div>
      <div class="sheet-body">
        <form id="editor" class="editor" autocomplete="off">
          <div>
            <div class="editor-art" data-art>${artHTML(beer)}</div>
            <div class="editor-art-actions">
              <label class="btn">
                <svg viewBox="0 0 24 24"><path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3"/></svg> Photo / upload
                <input type="file" name="file" accept="image/*">
              </label>
              <input type="url" name="label_url" value="${esc(beer.label_url || "")}" placeholder="…or paste image URL" style="height:44px;padding:0 12px;border-radius:12px;border:1px solid var(--card-border);background:rgba(0,0,0,.35);font-size:13px;width:100%;outline:none">
            </div>
          </div>
          <div class="editor-fields">
            <div class="field wide"><label>Beer name</label><input name="name" value="${esc(beer.name || "")}" required placeholder="e.g. Pliny the Elder"></div>
            <div class="field"><label>Brewery</label><input name="brewery" value="${esc(beer.brewery || "")}" placeholder="Brewery"></div>
            <div class="field"><label>Style</label><input name="style" value="${esc(beer.style || "")}" placeholder="IPA, Stout, Lager…"></div>
            <div class="field"><label>ABV %</label><input name="abv" inputmode="decimal" value="${beer.abv != null ? esc(beer.abv) : ""}" placeholder="6.5"></div>
            <div class="field"><label>IBU</label><input name="ibu" inputmode="numeric" value="${beer.ibu != null ? esc(beer.ibu) : ""}" placeholder="45"></div>
            <div class="field wide"><label>Description</label><textarea name="description" placeholder="What does it taste like?">${esc(beer.description || "")}</textarea></div>
          </div>
        </form>
      </div>
      <div class="sheet-foot">
        ${beerId != null && !beer.on_tap && opts.fromLibrary ? html`<button class="btn btn-danger" type="button" data-action="delete-beer" data-id="${beerId}">Delete</button>` : ""}
        ${beerId != null && targetTap && opts.editOnly ? html`<button class="btn btn-primary btn-lg btn-grow" type="button" data-action="save" data-tap="">Save changes</button>` : tapButtons}
      </div>`);

    const form = $("#editor");
    form._beer = beer;
    form._beerId = beerId;
    form.querySelectorAll("input, textarea").forEach((el) => osk.attach(el));
    const fileInput = form.querySelector('input[type="file"]');
    fileInput.addEventListener("change", () => {
      const file = fileInput.files[0];
      if (!file) return;
      const art = form.querySelector("[data-art]");
      art.innerHTML = html`<img src="${URL.createObjectURL(file)}" alt="">`;
      form.querySelector('[name="label_url"]').value = "";
    });
    const urlInput = form.querySelector('[name="label_url"]');
    urlInput.addEventListener("change", () => {
      if (urlInput.value.trim()) {
        form.querySelector("[data-art]").innerHTML = html`<img src="${esc(urlInput.value.trim())}" alt="">`;
        fileInput.value = "";
      }
    });
    if (!beer.name) {
      form.querySelector('[name="name"]').focus({ preventScroll: true });
      if (osk.enabled) osk.show(form.querySelector('[name="name"]'));
    }
  }

  async function saveEditor(tapNumber) {
    const form = $("#editor");
    if (!form) return;
    const fd = new FormData(form);
    const fields = {
      name: fd.get("name").trim(),
      brewery: fd.get("brewery").trim(),
      style: fd.get("style").trim(),
      abv: fd.get("abv").trim(),
      ibu: fd.get("ibu").trim(),
      description: fd.get("description").trim(),
    };
    if (!fields.name) { toast("Give the beer a name", true); form.querySelector('[name="name"]').focus(); return; }
    const file = form.querySelector('input[type="file"]').files[0];
    const labelUrl = fd.get("label_url").trim();
    const original = form._beer;
    const buttons = sheet.querySelectorAll(".sheet-foot .btn");
    buttons.forEach((b) => (b.disabled = true));
    try {
      let beerId = form._beerId;
      if (beerId != null) {
        const patch = { ...fields };
        if (labelUrl !== (original.label_url || "") && !file) patch.label_url = labelUrl;
        await api(`/api/beers/${beerId}`, { method: "PUT", json: patch });
      } else {
        const created = await api("/api/beers", {
          method: "POST",
          json: { ...fields, label_url: file ? "" : labelUrl, source: original.source || "manual", source_id: original.source_id || null },
        });
        beerId = created.id;
      }
      if (file) {
        const upload = new FormData();
        upload.append("label", file, file.name || "label.jpg");
        await api(`/api/beers/${beerId}/label`, { method: "POST", form: upload });
      }
      if (tapNumber) {
        applyState(await api(`/api/taps/${tapNumber}`, { method: "POST", json: { beer_id: beerId } }));
        toast(`${fields.name} is now on tap ${tapNumber}`);
      } else {
        await loadState();
        toast("Saved");
      }
      closeSheet();
    } catch (err) {
      toast(err.message, true);
      buttons.forEach((b) => (b.disabled = false));
    }
  }

  async function deleteBeer(id) {
    if (!confirm("Delete this beer from the archive? This can't be undone.")) return;
    try {
      await api(`/api/beers/${id}`, { method: "DELETE" });
      toast("Deleted");
      openArchive();
    } catch (err) { toast(err.message, true); }
  }

  // ---------- archive ----------

  async function openArchive() {
    openSheet(html`
      <div class="sheet-head">
        <div style="flex:1;min-width:0">
          <div class="sheet-sub">The cellar</div>
          <h2 class="sheet-title">Archive</h2>
        </div>
        ${closeBtn()}
      </div>
      <div class="sheet-body"><div id="archive" class="archive-grid"><div class="spinner"></div></div></div>`);
    try {
      const { beers } = await api("/api/archive");
      const grid = $("#archive");
      if (!grid) return;
      if (!beers.length) {
        grid.outerHTML = html`<div class="empty-state"><strong>Nothing archived yet</strong>Beers land here after they've been kicked off a tap.</div>`;
        return;
      }
      grid.innerHTML = beers.map((b) => {
        const key = ++resultHTML.seq;
        resultHTML.cache[key] = b;
        const when = b.last_untapped ? `Last poured ${fmtDate(b.last_untapped)}` : `Added ${fmtDate(b.created_at)}`;
        const days = Math.round(b.seconds_on_tap / 86400);
        return html`<button class="archive-card" type="button" data-action="archive-pick" data-key="${key}">
          <div class="archive-art" data-art>${artHTML(b)}</div>
          <div class="archive-text">
            <div class="archive-name">${esc(b.name)}</div>
            <div class="archive-sub">${esc(b.brewery || b.style || "")}</div>
            <div class="archive-when">${esc(when)}${b.times_tapped ? ` · ${b.times_tapped}× · ${days}d` : ""}</div>
          </div>
        </button>`;
      }).join("");
    } catch (err) { toast(err.message, true); }
  }

  // ---------- actions ----------

  board.addEventListener("click", (e) => {
    const card = e.target.closest("[data-tap]");
    if (card) openTapMenu(Number(card.dataset.tap));
  });
  $("#btn-archive").addEventListener("click", openArchive);
  $("#btn-add").addEventListener("click", () => openSearch(null));

  sheet.addEventListener("click", (e) => {
    const el = e.target.closest("[data-action]");
    if (!el) return;
    const tap = el.dataset.tap ? Number(el.dataset.tap) : null;
    switch (el.dataset.action) {
      case "search": openSearch(tap); break;
      case "kick": kickTap(tap); break;
      case "manual": openEditor({ source: "manual" }, tap, { back: "search:" + (tap || "") }); break;
      case "clear-search": { const i = $("#search-input"); i.value = ""; i.dispatchEvent(new Event("input")); i.focus(); break; }
      case "toggle-osk": osk.toggle(); break;
      case "pick": {
        const r = resultHTML.cache[el.dataset.key];
        openEditor(r, tap, { back: "search:" + (tap || ""), fromLibrary: r.source === "library" });
        break;
      }
      case "archive-pick": {
        const b = resultHTML.cache[el.dataset.key];
        openEditor({ ...b, beer_id: b.id }, null, { back: "archive", fromLibrary: true });
        break;
      }
      case "edit-beer": {
        const t = state.taps.find((x) => x.number === tap);
        if (t && t.beer) openEditor({ ...t.beer, beer_id: t.beer.id, on_tap: true }, tap, { back: "tap:" + tap, fromLibrary: true, editOnly: true });
        break;
      }
      case "save": saveEditor(tap); break;
      case "delete-beer": deleteBeer(Number(el.dataset.id)); break;
      default:
        if (el.dataset.action.startsWith("search:")) openSearch(Number(el.dataset.action.slice(7)) || null);
        else if (el.dataset.action === "archive") openArchive();
        else if (el.dataset.action.startsWith("tap:")) openTapMenu(Number(el.dataset.action.slice(4)));
    }
  });

  // ---------- on-screen keyboard ----------

  const osk = {
    enabled: false,
    target: null,
    onEnter: null,
    shift: false,
    layout: [
      ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"],
      ["q", "w", "e", "r", "t", "y", "u", "i", "o", "p"],
      ["a", "s", "d", "f", "g", "h", "j", "k", "l", "'"],
      ["shift", "z", "x", "c", "v", "b", "n", "m", "-", "backspace"],
      ["hide", "&", ".", "space", ",", "go"],
    ],
    init() {
      const stored = localStorage.getItem("osk");
      const coarse = window.matchMedia("(pointer: coarse)").matches;
      const mobile = /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent);
      this.enabled = stored != null ? stored === "1" : coarse && !mobile;
      this.render();
      oskEl.addEventListener("pointerdown", (e) => {
        const key = e.target.closest(".osk-key");
        if (!key) return;
        e.preventDefault();
        key.classList.add("down");
        setTimeout(() => key.classList.remove("down"), 120);
        this.press(key.dataset.key);
      });
      // Keep focus on the input when tapping keyboard keys.
      oskEl.addEventListener("mousedown", (e) => e.preventDefault());
      document.addEventListener("focusin", (e) => {
        if (this.enabled && e.target.matches("input:not([type=file]), textarea") && !sheet.hidden) this.show(e.target);
      });
    },
    render() {
      const icons = {
        backspace: `<svg viewBox="0 0 24 24"><path d="M9 6h12v12H9l-6-6zM12 9l6 6M18 9l-6 6"/></svg>`,
        shift: `<svg viewBox="0 0 24 24"><path d="M12 4l8 9h-5v7H9v-7H4z"/></svg>`,
        hide: `<svg viewBox="0 0 24 24"><path d="M3 5h18v10H3zM8 19l4 3 4-3"/></svg>`,
        go: "Search",
        space: "",
      };
      oskEl.innerHTML = this.layout.map((row) => `<div class="osk-row">` + row.map((k) => {
        const cls = ["osk-key"];
        if (["shift", "backspace", "hide", "go"].includes(k)) cls.push("wide", k);
        if (k === "space") cls.push("space");
        if (k === "shift" && this.shift) cls.push("on");
        const label = icons[k] !== undefined ? icons[k] : (this.shift ? k.toUpperCase() : k);
        return `<button class="${cls.join(" ")}" type="button" data-key="${esc(k)}" tabindex="-1">${label}</button>`;
      }).join("") + `</div>`).join("");
    },
    attach(input, onEnter) {
      input.addEventListener("focus", () => { this.target = input; this.onEnter = onEnter || null; if (this.enabled) this.show(input); });
    },
    show(input) {
      if (input) this.target = input;
      const go = oskEl.querySelector(".osk-key.go");
      if (go) go.textContent = this.onEnter ? "Search" : "Done";
      oskEl.hidden = false;
      document.documentElement.classList.add("osk-open");
      document.documentElement.style.setProperty("--osk-h", `${oskEl.offsetHeight}px`);
      if (this.target) setTimeout(() => this.target.scrollIntoView({ block: "nearest" }), 50);
    },
    hide() {
      oskEl.hidden = true;
      document.documentElement.classList.remove("osk-open");
      document.documentElement.style.setProperty("--osk-h", "0px");
    },
    toggle() {
      this.enabled = !this.enabled;
      localStorage.setItem("osk", this.enabled ? "1" : "0");
      if (this.enabled) {
        const el = document.activeElement && document.activeElement.matches("input, textarea") ? document.activeElement : (this.target || $("#search-input"));
        if (el) { el.focus({ preventScroll: true }); this.show(el); }
      } else this.hide();
      toast(this.enabled ? "On-screen keyboard on" : "On-screen keyboard off");
    },
    press(key) {
      const t = this.target;
      if (key === "hide") { this.hide(); return; }
      if (key === "shift") { this.shift = !this.shift; this.render(); return; }
      if (!t) return;
      const val = t.value;
      const start = t.selectionStart != null ? t.selectionStart : val.length;
      const end = t.selectionEnd != null ? t.selectionEnd : val.length;
      let next = val, caret = start;
      if (key === "backspace") {
        if (start !== end) { next = val.slice(0, start) + val.slice(end); caret = start; }
        else if (start > 0) { next = val.slice(0, start - 1) + val.slice(end); caret = start - 1; }
      } else if (key === "go") {
        // "Search" runs the search; "Done" just dismisses the keyboard.
        if (this.onEnter) this.onEnter();
        this.hide();
        t.blur();
        return;
      } else {
        let ch = key === "space" ? " " : key;
        if (this.shift && ch.length === 1) { ch = ch.toUpperCase(); this.shift = false; this.render(); }
        next = val.slice(0, start) + ch + val.slice(end);
        caret = start + ch.length;
      }
      t.value = next;
      try { t.setSelectionRange(caret, caret); } catch (_) { /* type=search/url on some browsers */ }
      t.dispatchEvent(new Event("input", { bubbles: true }));
      t.focus({ preventScroll: true });
    },
  };

  // ---------- clock ----------

  function tickClock() {
    const now = new Date();
    $("#clock").textContent = now.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  }

  // ---------- boot ----------

  osk.init();
  tickClock();
  setInterval(tickClock, 15000);
  loadState();
  setInterval(poll, POLL_MS);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) loadState(); });
})();
