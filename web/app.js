/* Keepstack single-page client. Vanilla JS, no build step. */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const state = { token: localStorage.getItem("keepstack_token"), user: null,
  q: "", media: null, collection: null, tags: [], excludeTags: [], sort: "newest",
  offset: 0, limit: 24, total: 0 };

// ---------- API helper ----------
async function api(path, opts = {}) {
  const headers = opts.headers || {};
  if (state.token) headers["Authorization"] = "Bearer " + state.token;
  if (opts.json) { headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.json); }
  // fetch defaults to GET, which may not carry a body: JSON calls without an
  // explicit method (login, add tag, new collection, share) are POSTs.
  const method = opts.method || (opts.body !== undefined ? "POST" : "GET");
  const res = await fetch(path, { ...opts, method, headers });
  if (res.status === 401) { logout(); throw new Error("unauthorised"); }
  if (!res.ok) throw new Error(await errorText(res));
  return res.headers.get("content-type")?.includes("json") ? res.json() : res;
}
// FastAPI errors are JSON ({"detail": ...}); show the detail, not the raw body.
async function errorText(res) {
  const t = await res.text();
  try {
    const d = JSON.parse(t).detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) return d.map(x => `${(x.loc || []).slice(-1)[0]}: ${x.msg}`).join("; ");
  } catch {}
  return t || `${res.status} ${res.statusText}`;
}

function toast(msg, kind = "ok") {
  const t = $("#toast"); t.textContent = msg; t.className = "toast " + kind;
  setTimeout(() => t.classList.add("hidden"), 2600);
}
const fmtBytes = (b) => { if (!b) return "0 B"; const u = ["B","KB","MB","GB","TB"]; const i = Math.floor(Math.log(b)/Math.log(1024)); return (b/Math.pow(1024,i)).toFixed(i?1:0)+" "+u[i]; };
const esc = (s) => (s ?? "").toString().replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

// ---------- auth ----------
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const r = await api("/api/auth/login", { json: { username: $("#login-user").value, password: $("#login-pass").value } });
    state.token = r.token; localStorage.setItem("keepstack_token", r.token);
    state.user = r.user; enterApp();
  } catch { $("#login-error").textContent = "Invalid credentials"; $("#login-error").classList.remove("hidden"); }
});
$("#btn-logout").addEventListener("click", logout);
function logout() { localStorage.removeItem("keepstack_token"); state.token = null; state.user = null;
  $("#app").classList.add("hidden"); $("#login").classList.remove("hidden"); }

function enterApp() {
  $("#login").classList.add("hidden"); $("#app").classList.remove("hidden");
  $("#user-label").textContent = `${state.user.username} (${state.user.role})`;
  const isAdmin = state.user.role === "admin";
  $$(".admin-only").forEach(el => el.classList.toggle("hidden", !isAdmin));
  loadFilterOptions().then(loadLibrary);
}

// ---------- navigation ----------
$$(".nav-item").forEach(btn => btn.addEventListener("click", () => {
  $$(".nav-item").forEach(b => b.classList.remove("active")); btn.classList.add("active");
  const view = btn.dataset.view;
  $$(".view").forEach(v => v.classList.add("hidden"));
  $("#view-" + (view === "library" ? "library" : view)).classList.remove("hidden");
  if (view === "library") loadLibrary();
  if (view === "collections") loadCollections();
  if (view === "dashboard") loadDashboard();
  if (view === "users") loadUsers();
  if (view === "audit") loadAudit();
}));

// ---------- library / search ----------
// All list state lives in `state`; every filter/sort change goes through
// applyFilters() (back to page 1), paging only moves state.offset.
const normTag = (t) => (t || "").trim().toLowerCase();   // same rule as the server
function hasFilters() {
  return !!(state.q.trim() || state.media || state.collection || state.tags.length || state.excludeTags.length);
}
function filterParams() {
  const p = new URLSearchParams();
  if (state.q.trim()) p.set("q", state.q);
  if (state.media) p.set("media_type", state.media);
  if (state.collection) p.set("collection_id", state.collection);
  state.tags.forEach(t => p.append("tags", t));
  state.excludeTags.forEach(t => p.append("exclude_tags", t));
  p.set("sort", state.sort);
  return p;
}
function applyFilters() { state.offset = 0; loadLibrary(); }
function clearFilters() {
  state.q = ""; $("#search").value = ""; state.media = null; state.collection = null;
  state.tags = []; state.excludeTags = []; applyFilters();
}

let searchTimer;
$("#search").addEventListener("input", (e) => { state.q = e.target.value; clearTimeout(searchTimer); searchTimer = setTimeout(applyFilters, 250); });
$("#search").addEventListener("keydown", (e) => { if (e.key === "Enter") { clearTimeout(searchTimer); applyFilters(); } });
$("#sort").addEventListener("change", (e) => { state.sort = e.target.value; applyFilters(); });
$("#f-collection").addEventListener("change", (e) => { state.collection = e.target.value || null; applyFilters(); });
$("#pg-size").addEventListener("change", (e) => { state.limit = +e.target.value; applyFilters(); });
$("#pg-prev").addEventListener("click", () => { state.offset = Math.max(0, state.offset - state.limit); loadLibrary(); });
$("#pg-next").addEventListener("click", () => { state.offset += state.limit; loadLibrary(); });
function wireTagInput(sel, key) {
  const el = $(sel);
  const add = () => {
    const t = normTag(el.value); el.value = "";
    if (!t || state[key].includes(t)) return;
    state[key] = [...state[key], t]; applyFilters();
  };
  // Only Enter commits: tag names may legitimately contain commas.
  el.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); add(); } });
  el.addEventListener("change", () => { if (el.value && $$("#tag-options option").some(o => o.value === el.value)) add(); });
}
wireTagInput("#f-tags", "tags");
wireTagInput("#f-exclude", "excludeTags");

async function loadFilterOptions() {
  try {
    const [cols, tags] = await Promise.all([api("/api/collections"), api("/api/tags")]);
    $("#f-collection").innerHTML = `<option value="">Any collection</option>` +
      cols.collections.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join("");
    $("#f-collection").value = state.collection || "";
    $("#tag-options").innerHTML = tags.tags.map(t => `<option value="${esc(t.name)}">`).join("");
  } catch (err) { /* options are a convenience; typing still works */ }
}

function collectionName(id) {
  const o = $$("#f-collection option").find(o => o.value === String(id));
  return o ? o.textContent : "#" + id;
}

function renderActiveFilters() {
  const chips = [];
  const conflict = state.tags.filter(t => state.excludeTags.includes(t));
  if (state.q.trim()) chips.push({ label: `Keyword: "${state.q.trim()}"`, clear: () => { state.q = ""; $("#search").value = ""; } });
  if (state.collection) chips.push({ label: `Collection: ${collectionName(state.collection)}`, clear: () => { state.collection = null; } });
  state.tags.forEach(t => chips.push({ label: `Has: ${t}`, cls: conflict.includes(t) ? "conflict" : "",
    clear: () => { state.tags = state.tags.filter(x => x !== t); } }));
  state.excludeTags.forEach(t => chips.push({ label: `Not: ${t}`, cls: "exclude" + (conflict.includes(t) ? " conflict" : ""),
    clear: () => { state.excludeTags = state.excludeTags.filter(x => x !== t); } }));
  if (state.media) chips.push({ label: `Type: ${state.media}`, clear: () => { state.media = null; } });
  const box = $("#active-filters");
  box.classList.toggle("hidden", !chips.length);
  box.innerHTML = `<span class="muted">Filters:</span>` +
    chips.map((c, i) => `<span class="chip ${c.cls || ""}" data-i="${i}" title="Remove this filter">${esc(c.label)}</span>`).join("") +
    (conflict.length ? `<span class="warn">"${esc(conflict.join(", "))}" is both required and excluded, so nothing can match.</span>` : "") +
    `<button class="btn-link" id="clear-filters">Clear all</button>`;
  $$("#active-filters .chip").forEach(el => el.addEventListener("click", () => { chips[+el.dataset.i].clear(); applyFilters(); }));
  $("#clear-filters")?.addEventListener("click", clearFilters);
  $("#f-collection").value = state.collection || "";
}

// Only the response to the most recent request is rendered: older in-flight
// requests are aborted, and a sequence check drops anything that still lands.
let libSeq = 0, libAbort = null;
async function loadLibrary() {
  const seq = ++libSeq;
  libAbort?.abort();
  libAbort = new AbortController();
  renderActiveFilters();
  $("#grid").classList.add("loading");
  const p = filterParams();
  p.set("limit", state.limit); p.set("offset", state.offset);
  let data;
  try {
    data = await api("/api/assets?" + p.toString(), { signal: libAbort.signal });
  } catch (err) {
    if (seq !== libSeq || err.name === "AbortError") return;
    $("#grid").classList.remove("loading");
    renderLoadError(err);
    return;
  }
  if (seq !== libSeq) return;
  $("#grid").classList.remove("loading");
  // The page we were on can disappear (edits/deletes); fall back to the last page.
  if (!data.items.length && data.total > 0 && state.offset > 0) {
    state.offset = Math.floor((data.total - 1) / state.limit) * state.limit;
    return loadLibrary();
  }
  state.total = data.total;
  renderFacets(data.facets);
  renderGrid(data.items, data.total);
  renderPager(data.total);
}

function renderLoadError(err) {
  $("#grid").innerHTML = "";
  $("#pager").classList.add("hidden");
  const e = $("#empty");
  e.className = "empty error";
  e.innerHTML = `Couldn't load assets: ${esc(err.message)} <button class="btn" id="retry-load">Retry</button>`;
  $("#retry-load").addEventListener("click", loadLibrary);
}

function renderGrid(items, total) {
  $("#result-title").textContent = hasFilters() ? `Results (${total} matching)` : `Library (${total})`;
  const e = $("#empty");
  e.className = "empty" + (items.length ? " hidden" : "");
  if (!items.length) {
    e.innerHTML = hasFilters()
      ? `No assets match these filters. <button class="btn-link" id="empty-clear">Clear filters</button>`
      : "No assets yet. Click Upload to get started.";
    $("#empty-clear")?.addEventListener("click", clearFilters);
  }
  $("#grid").innerHTML = items.map(a => `
    <div class="card" data-uuid="${a.uuid}">
      <img class="thumb" loading="lazy" src="/api/assets/${a.uuid}/thumb" alt="${esc(a.alt_text || a.title)}">
      <div class="meta">
        <span class="title" title="${esc(a.title)}">${esc(a.title)}</span>
        <span class="sub"><span class="badge">${a.media_type}</span><span>${(a.created_at || "").slice(0, 10)}</span><span>${fmtBytes(a.size)}</span></span>
      </div>
    </div>`).join("");
  $$("#grid .card").forEach(c => c.addEventListener("click", () => openAsset(c.dataset.uuid)));
}

function renderPager(total) {
  const pages = Math.max(1, Math.ceil(total / state.limit));
  const page = Math.floor(state.offset / state.limit) + 1;
  $("#pager").classList.toggle("hidden", total === 0);
  const from = total ? state.offset + 1 : 0, to = Math.min(total, state.offset + state.limit);
  $("#pg-info").textContent = `Page ${page} of ${pages} · ${from}–${to} of ${total}`;
  $("#pg-prev").disabled = state.offset === 0;
  $("#pg-next").disabled = state.offset + state.limit >= total;
}

// Facet counts are catalog-wide (not the current matches); headings say so.
function renderFacets(facets) {
  $("#facet-media").innerHTML = (facets.media_types || []).map(m =>
    `<li class="${state.media===m.value?'active':''}" data-media="${esc(m.value)}" title="${m.count} assets in the catalog"><span>${esc(m.value)}</span><span class="count">${m.count}</span></li>`).join("");
  $("#facet-tags").innerHTML = (facets.tags || []).slice(0, 18).map(t =>
    `<li class="${state.tags.includes(t.value)?'active':''}" data-tag="${esc(t.value)}" title="${t.count} assets in the catalog; click to require this tag"><span>${esc(t.value)}</span><span class="count">${t.count}</span></li>`).join("");
  $$("#facet-media li").forEach(li => li.addEventListener("click", () => { state.media = state.media===li.dataset.media?null:li.dataset.media; applyFilters(); }));
  $$("#facet-tags li").forEach(li => li.addEventListener("click", () => {
    const t = li.dataset.tag;
    state.tags = state.tags.includes(t) ? state.tags.filter(x => x !== t) : [...state.tags, t];
    applyFilters();
  }));
}

// CSV export: fetch with the bearer token (a plain link could not send it),
// then hand the blob to the browser as a download.
$("#btn-export").addEventListener("click", async () => {
  const btn = $("#btn-export");
  btn.disabled = true; btn.textContent = "Exporting...";
  try {
    const res = await fetch("/api/assets/export.csv?" + filterParams().toString(),
                            { headers: { Authorization: "Bearer " + state.token } });
    if (res.status === 401) { logout(); throw new Error("session expired, please sign in again"); }
    if (!res.ok) throw new Error(await errorText(res));
    const blob = await res.blob();
    const name = /filename="([^"]+)"/.exec(res.headers.get("content-disposition") || "")?.[1] || "assets.csv";
    const url = URL.createObjectURL(blob);
    const a = Object.assign(document.createElement("a"), { href: url, download: name });
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
    toast("CSV downloaded");
  } catch (err) {
    toast("Export failed: " + err.message, "err");
  } finally {
    btn.disabled = false; btn.textContent = "Export CSV";
  }
});

// ---------- asset detail drawer ----------
const drawer = $("#drawer");
$("#drawer-close").addEventListener("click", closeDrawer);
$(".drawer-backdrop").addEventListener("click", closeDrawer);
function closeDrawer() { drawer.classList.add("hidden"); document.body.classList.remove("modal-open"); }

async function openAsset(uuid) {
  drawer.classList.remove("hidden");
  document.body.classList.add("modal-open");
  $("#drawer-body").innerHTML = '<p class="muted">Loading...</p>';
  const [d, meta, sim] = await Promise.all([
    api("/api/assets/" + uuid),
    api("/api/assets/" + uuid + "/metadata"),
    api("/api/assets/" + uuid + "/similar"),
  ]);
  const a = d.asset;
  const canEdit = ["admin","editor","contributor"].includes(state.user.role);
  const preview = a.media_type === "image"
    ? `<img class="detail-preview" src="/api/assets/${uuid}/preview" alt="${esc(a.alt_text||a.title)}">`
    : `<img class="detail-preview" src="/api/assets/${uuid}/thumb" alt="${esc(a.title)}">`;
  const dc = a.dublin_core || {};
  $("#drawer-body").innerHTML = `
    ${preview}
    <h2 class="detail-title">${esc(a.title)}</h2>
    <p class="muted">${a.media_type} &middot; ${a.ext?.toUpperCase()} &middot; ${fmtBytes(a.size)} ${a.width?`&middot; ${a.width}&times;${a.height}`:""}</p>
    <div class="btn-row">
      <a class="btn" href="/api/assets/${uuid}/file" download>Download</a>
      ${canEdit?`<button class="btn" id="d-share">Create share link</button>`:""}
      ${["admin","editor"].includes(state.user.role)?`<button class="btn btn-danger" id="d-delete">Delete</button>`:""}
    </div>

    ${canEdit ? `<div class="detail-section"><h4>Edit metadata</h4>
      <div class="edit-grid">
        <label>Title<input id="e-title" value="${esc(a.title)}"></label>
        <label>Description<textarea id="e-description" rows="2">${esc(a.description)}</textarea></label>
        <label>Alt text (accessibility)<input id="e-alt_text" value="${esc(a.alt_text)}"></label>
        <label>Creator<input id="e-dc_creator" value="${esc(a.dc_creator)}"></label>
        <label>Rights / license<input id="e-rights_statement" value="${esc(a.rights_statement)}"></label>
        <label>Retention until<input id="e-retention_until" type="date" value="${esc((a.retention_until||'').slice(0,10))}"></label>
      </div>
      <div class="btn-row"><button class="btn btn-primary" id="d-save">Save changes</button></div></div>` : ""}

    <div class="detail-section"><h4>Tags</h4>
      <div class="tag-row" id="tag-row">
        ${d.tags.map(t => `<span class="tag ${t.source==='ai'?'ai':''}">${esc(t.name)}${canEdit?`<span class="rm" data-tag="${esc(t.name)}">&times;</span>`:""}</span>`).join("")}
        ${canEdit?`<input class="tag-add" id="tag-add" placeholder="+ add tag">`:""}
      </div></div>

    ${sim.items.length?`<div class="detail-section"><h4>Similar assets</h4>
      <div class="similar-strip">${sim.items.slice(0,8).map(s=>`<img src="/api/assets/${s.uuid}/thumb" data-uuid="${s.uuid}" title="${esc(s.title)}">`).join("")}</div></div>`:""}

    <div class="detail-section"><h4>Dublin Core</h4>
      ${Object.entries(dc).filter(([,v])=>v).map(([k,v])=>`<div class="field-row"><span class="k">${k}</span><span class="v">${esc(v)}</span></div>`).join("")}
      <div class="btn-row"><a class="btn btn-ghost" href="/oai?verb=GetRecord&metadataPrefix=oai_dc&identifier=oai::${a.uuid}" target="_blank">View OAI-PMH XML</a></div>
    </div>

    ${Object.keys(meta.exif||{}).length||Object.keys(meta.iptc||{}).length?`<div class="detail-section"><h4>Embedded metadata (EXIF / IPTC)</h4>
      <div class="kv-scroll">${[...Object.entries(meta.iptc||{}),...Object.entries(meta.exif||{})].slice(0,40).map(([k,v])=>`<div class="field-row"><span class="k">${esc(k)}</span><span class="v">${esc(typeof v==='object'?JSON.stringify(v):v)}</span></div>`).join("")}</div></div>`:""}

    ${d.versions.length?`<div class="detail-section"><h4>Versions</h4>${d.versions.map(v=>`<div class="field-row"><span class="k">v${v.version_no}</span><span class="v">${esc(v.note||'')} &middot; ${fmtBytes(v.size)} &middot; ${(v.created_at||'').slice(0,10)}</span></div>`).join("")}</div>`:""}
  `;

  // wire detail actions
  $$("#drawer-body .similar-strip img").forEach(img => img.addEventListener("click", () => openAsset(img.dataset.uuid)));
  // Edits used to fail silently (unhandled rejection); report the reason.
  const attempt = async (what, fn) => {
    try { await fn(); } catch (err) { toast(`${what} failed: ${err.message}`, "err"); }
  };
  if (canEdit) {
    $("#d-save")?.addEventListener("click", () => attempt("Save", async () => {
      const body = {};
      ["title","description","alt_text","dc_creator","rights_statement","retention_until"].forEach(f => { const el = $("#e-"+f); if (el) body[f] = el.value; });
      await api("/api/assets/"+uuid, { method: "PATCH", json: body });
      toast("Saved"); openAsset(uuid); loadLibrary();
    }));
    $("#tag-add")?.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && e.target.value.trim()) attempt("Adding tag", async () => {
        await api("/api/assets/"+uuid+"/tags", { json: { name: e.target.value.trim() } });
        openAsset(uuid); loadLibrary(); loadFilterOptions();
      });
    });
    $$("#tag-row .rm").forEach(rm => rm.addEventListener("click", () => attempt("Removing tag", async () => {
      await api("/api/assets/"+uuid+"/tags/"+encodeURIComponent(rm.dataset.tag), { method: "DELETE" });
      openAsset(uuid); loadLibrary();
    })));
    $("#d-share")?.addEventListener("click", async () => {
      const r = await api("/api/shares", { json: { target_type: "asset", target_id: a.id, permission: "download" } });
      await navigator.clipboard?.writeText(r.url).catch(()=>{});
      toast("Share link copied: " + r.url);
    });
  }
  $("#d-delete")?.addEventListener("click", async () => {
    if (!confirm("Delete this asset?")) return;
    await attempt("Delete", async () => {
      await api("/api/assets/"+uuid, { method: "DELETE" });
      toast("Deleted"); closeDrawer(); loadLibrary();
    });
  });
}

// ---------- upload ----------
const uploadModal = $("#upload-modal");
$("#btn-upload").addEventListener("click", () => { uploadModal.classList.remove("hidden"); document.body.classList.add("modal-open"); });
$("#upload-cancel").addEventListener("click", () => { uploadModal.classList.add("hidden"); document.body.classList.remove("modal-open"); loadLibrary(); });
$(".modal-backdrop").addEventListener("click", () => { uploadModal.classList.add("hidden"); document.body.classList.remove("modal-open"); });
const dz = $("#dropzone"), fileInput = $("#file-input");
dz.addEventListener("click", () => fileInput.click());
dz.addEventListener("dragover", e => { e.preventDefault(); dz.classList.add("drag"); });
dz.addEventListener("dragleave", () => dz.classList.remove("drag"));
dz.addEventListener("drop", e => { e.preventDefault(); dz.classList.remove("drag"); uploadFiles(e.dataTransfer.files); });
fileInput.addEventListener("change", () => uploadFiles(fileInput.files));

async function uploadFiles(files) {
  const list = $("#upload-list");
  for (const file of files) {
    const row = document.createElement("div");
    row.className = "upload-item"; row.innerHTML = `<span>${esc(file.name)}</span><span>uploading...</span>`;
    list.prepend(row);
    const fd = new FormData(); fd.append("files", file); fd.append("run_ai", $("#ai-toggle").checked);
    try {
      const r = await api("/api/assets", { method: "POST", body: fd });
      const created = r.created[0];
      row.classList.add(created.duplicate ? "dup" : "ok");
      row.lastChild.textContent = created.duplicate ? "duplicate (deduped)" : "done";
    } catch (err) { row.classList.add("err"); row.lastChild.textContent = "failed"; }
  }
}

// ---------- collections ----------
async function loadCollections() {
  const data = await api("/api/collections");
  $("#collection-list").innerHTML = data.collections.map(c =>
    `<div class="collection-card" data-id="${c.id}"><span class="n">${esc(c.name)}</span><span class="muted">${c.asset_count} assets</span><span class="muted">${esc(c.description||"")}</span></div>`).join("")
    || '<p class="muted">No collections yet.</p>';
  $$("#collection-list .collection-card").forEach(card => card.addEventListener("click", () => {
    state.collection = card.dataset.id;
    $$(".nav-item").forEach(b => b.classList.remove("active"));
    $('.nav-item[data-view="library"]').classList.add("active");
    $$(".view").forEach(v => v.classList.add("hidden")); $("#view-library").classList.remove("hidden");
    applyFilters();
  }));
}
$("#btn-new-collection").addEventListener("click", async () => {
  const name = prompt("Collection name"); if (!name) return;
  await api("/api/collections", { json: { name } }); loadCollections(); loadFilterOptions();
});

// ---------- dashboard ----------
async function loadDashboard() {
  const s = await api("/api/stats");
  const cards = [
    ["Total assets", s.total_assets], ["Storage used", fmtBytes(s.total_bytes)],
    ["Saved by dedup", fmtBytes(s.dedup_saved_bytes)], ["Tags", s.tags], ["Collections", s.collections],
    ...Object.entries(s.by_type).map(([k,v]) => [k.charAt(0).toUpperCase()+k.slice(1), v]),
  ];
  $("#stat-cards").innerHTML = cards.map(([l,n]) => `<div class="stat-card"><div class="n">${n}</div><div class="l">${l}</div></div>`).join("");
}
$("#btn-fixity").addEventListener("click", async () => {
  $("#fixity-result").textContent = "Checking...";
  const r = await api("/api/admin/fixity", { method: "POST" });
  $("#fixity-result").textContent = r.ok ? `All ${r.checked} blobs verified. Integrity intact.` : `${r.failures.length} of ${r.checked} failed fixity.`;
});

// ---------- users ----------
async function loadUsers() {
  const data = await api("/api/users");
  $("#user-rows").innerHTML = data.users.map(u =>
    `<tr><td>${esc(u.username)}</td><td>${esc(u.email||"")}</td><td>${u.role}</td><td>${u.is_active?"Yes":"No"}</td><td>${(u.last_login||"").slice(0,16).replace("T"," ")}</td></tr>`).join("");
}
$("#btn-new-user").addEventListener("click", async () => {
  const username = prompt("Username"); if (!username) return;
  const password = prompt("Temporary password"); if (!password) return;
  const role = prompt("Role (admin/editor/contributor/viewer)", "contributor") || "viewer";
  await api("/api/users", { json: { username, password, role } }); loadUsers();
});

// ---------- audit ----------
async function loadAudit() {
  const data = await api("/api/audit");
  $("#audit-rows").innerHTML = data.entries.map(e =>
    `<tr><td>${(e.ts||"").slice(0,19).replace("T"," ")}</td><td>${esc(e.username||"-")}</td><td>${esc(e.action)}</td>
     <td>${esc(e.target_type||"")} ${esc((e.target_id||"").slice(0,8))}</td><td>${esc(e.detail||"")}</td><td>${esc(e.ip||"")}</td></tr>`).join("");
}

// ---------- boot ----------
(async function boot() {
  if (state.token) {
    try { state.user = await api("/api/auth/me"); enterApp(); return; } catch {}
  }
  $("#login").classList.remove("hidden");
})();
