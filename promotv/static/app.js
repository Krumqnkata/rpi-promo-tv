"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
const DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"];
const DAYS_LONG = ["понеделник", "вторник", "сряда", "четвъртък", "петък", "събота", "неделя"];
const MODES = { all: "По ред", shuffle: "Разбъркано", single: "Един файл" };

let files = [];
let status = null;
let busy = false; // pause list refresh while the user edits

// ---------------------------------------------------------------- helpers
async function api(path, opts = {}) {
  const o = Object.assign({ headers: {} }, opts);
  if (o.json !== undefined) {
    o.method = o.method || "POST";
    o.body = JSON.stringify(o.json);
    o.headers["Content-Type"] = "application/json";
  }
  const r = await fetch(path, o);
  let data = {};
  try { data = await r.json(); } catch (e) { /* empty */ }
  if (!r.ok) throw new Error(data.error || `Грешка ${r.status}`);
  return data;
}

let toastTimer;
function toast(msg, error = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("error", error);
  t.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), error ? 6000 : 2500);
}

function fmtSize(b) {
  if (b >= 1e9) return (b / 1e9).toFixed(2) + " GB";
  if (b >= 1e6) return (b / 1e6).toFixed(1) + " MB";
  return Math.max(1, Math.round(b / 1e3)) + " KB";
}
function fmtDur(s) {
  s = Math.round(s || 0);
  const m = Math.floor(s / 60), sec = String(s % 60).padStart(2, "0");
  return m >= 60 ? `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}
function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v === true) e.setAttribute(k, "");
    else if (v !== false && v != null) e.setAttribute(k, v);
  }
  for (const c of children.flat()) if (c != null) e.append(c);
  return e;
}

// ---------------------------------------------------------------- tabs
$$(".tab").forEach((b) => b.addEventListener("click", () => {
  $$(".tab").forEach((x) => x.classList.toggle("active", x === b));
  $$(".panel").forEach((p) => p.classList.toggle("hidden", p.id !== "tab-" + b.dataset.tab));
  if (b.dataset.tab === "schedule") loadSchedule();
  if (b.dataset.tab === "settings") loadSettings();
  if (b.dataset.tab === "play") loadPlayback();
}));

// ---------------------------------------------------------------- time sync
async function syncTime() {
  try {
    const r = await api("/api/time", { json: { epoch_ms: Date.now() } });
    if (r.correction && Math.abs(r.correction) > 60) toast("Часът на устройството беше сверен с телефона.");
  } catch (e) { /* ignore */ }
}

// ---------------------------------------------------------------- status
async function refreshStatus() {
  try {
    status = await api("/api/status");
  } catch (e) { return; }
  const s = status;
  $("#nowFile").textContent = s.player.current || (s.player.count ? "—" : "Няма файлове за показване");
  $("#btnPause").setAttribute("aria-label", s.player.paused ? "Продължи" : "Пауза");
  $("#icoPause").innerHTML = s.player.paused
    ? '<path d="M7 4v16l13-8z"/>'
    : '<path d="M7 5h4v14H7zM13 5h4v14h-4z"/>';
  const tv = $("#chipTv");
  const pw = s.tv.power;
  tv.textContent = !s.player.output ? "HDMI сигнал: спрян" : !s.tv.cec ? "ТВ: няма CEC връзка"
    : pw === "on" ? "ТВ: включен" : pw === "standby" ? "ТВ: изключен" : "ТВ: ?";
  tv.className = "chip " + (!s.player.output || pw === "standby" ? "off" : pw === "on" ? "on" : "");
  $("#dataWarn").classList.toggle("hidden", s.protection.data_persistent);
  $("#chipMode").textContent = (s.player.paused ? "Пауза · " : "") + MODES[s.settings.mode];
  const n = s.schedule.next;
  $("#chipSched").textContent = !s.schedule.enabled ? "График: изкл." :
    n ? `${n.kind === "on" ? "Вкл." : "Изкл."} ${DAYS[n.day]} ${n.time}` : "График: вкл.";
  $("#clock").textContent = s.time.now.slice(11, 16);
  $("#clock").classList.toggle("bad", !s.time.trusted);
  $("#timeWarn").classList.toggle("hidden", s.time.trusted);
  $("#disk").textContent = "Свободно: " + fmtSize(s.disk.free);
  $$(".file").forEach((li) => li.classList.toggle("playing", li.dataset.name === s.player.current));
}

$$("[data-player]").forEach((b) => b.addEventListener("click", async () => {
  try { await api("/api/player", { json: { action: b.dataset.player } }); setTimeout(refreshStatus, 300); }
  catch (e) { toast(e.message, true); }
}));
$$("[data-tv]").forEach((b) => b.addEventListener("click", async () => {
  try {
    await api("/api/tv", { json: { action: b.dataset.tv } });
    toast(b.dataset.tv === "on" ? "Включване на телевизора…" : "Изключване на телевизора…");
    setTimeout(refreshStatus, 1500);
  } catch (e) { toast(e.message, true); }
}));

// ---------------------------------------------------------------- files
async function loadFiles() {
  if (busy) return;
  try {
    const r = await api("/api/files");
    files = r.files;
    renderFiles(r.current);
  } catch (e) { /* offline */ }
}

function renderFiles(current) {
  const ul = $("#files");
  ul.replaceChildren();
  $("#filesEmpty").classList.toggle("hidden", files.length > 0);
  const active = files.filter((f) => f.enabled).length;
  $("#filesCount").textContent = files.length ? `${files.length} файла · ${active} се показват` : "Файлове";
  files.forEach((f, i) => {
    const meta = [f.type === "image" ? "Снимка" : "Видео"];
    if (f.type === "video" && f.duration) meta.push(fmtDur(f.duration));
    if (f.width) meta.push(`${f.width}×${f.height}`);
    meta.push(fmtSize(f.size));
    const thumb = el("div", { class: "thumb" }, f.thumb ? null : (f.type === "image" ? "IMG" : "VIDEO"));
    if (f.thumb) thumb.style.backgroundImage = `url(/thumbs/${f.thumb}.jpg)`;
    const toggle = el("input", { type: "checkbox", class: "switch small", checked: f.enabled,
      onchange: (e) => { f.enabled = e.target.checked; savePlaylist(); } });
    const li = el("li", { class: "file" + (f.enabled ? "" : " disabled") + (f.name === current ? " playing" : "")
        + (f.date_status !== "active" ? " " + f.date_status : ""), "data-name": f.name },
      thumb,
      el("div", {},
        el("div", { class: "fname" }, f.name, f.name === current ? el("span", { class: "badge" }, "НА ЕКРАНА") : null),
        el("div", { class: "fmeta" }, meta.join(" · ") + (f.processing ? " · проверява се…" : "")),
        datesBadge(f)),
      f.warnings.length ? el("div", { class: "fwarn" }, f.warnings.map((w) => el("p", {}, "⚠ " + w))) : null,
      el("div", { class: "factions" },
        el("label", { class: "toggle" }, toggle, "Показвай"),
        el("button", { class: "fbtn icon", title: "Нагоре", disabled: i === 0, onclick: () => move(i, -1) }, "↑"),
        el("button", { class: "fbtn icon", title: "Надолу", disabled: i === files.length - 1, onclick: () => move(i, 1) }, "↓"),
        el("button", { class: "fbtn", onclick: () => playNow(f.name) }, "Пусни"),
        el("button", { class: "fbtn", onclick: () => editDates(f) }, "Период"),
        el("button", { class: "fbtn", onclick: () => rename(f.name) }, "Име"),
        el("button", { class: "fbtn del", onclick: () => del(f.name) }, "Изтрий")));
    ul.append(li);
  });
}

const fmtDate = (d) => d ? `${d.slice(8, 10)}.${d.slice(5, 7)}.${d.slice(0, 4)}` : "";

function datesBadge(f) {
  if (!f.start && !f.end) return null;
  let txt = f.start && f.end ? `${fmtDate(f.start)} – ${fmtDate(f.end)}`
    : f.start ? `от ${fmtDate(f.start)}` : `до ${fmtDate(f.end)}`;
  if (f.date_status === "upcoming") txt = "Предстои · " + txt;
  else if (f.date_status === "expired") txt = "Изтекъл · " + txt;
  else txt = "Показва се · " + txt;
  return el("div", { class: "dates " + f.date_status }, txt);
}

function editDates(f) {
  const dlg = $("#datesDialog");
  $("#datesName").textContent = f.name;
  $("#dateStart").value = f.start || "";
  $("#dateEnd").value = f.end || "";
  busy = true;
  dlg.onclose = async () => {
    busy = false;
    if (dlg.returnValue !== "ok" && dlg.returnValue !== "clear") return;
    const body = dlg.returnValue === "clear" ? { start: null, end: null }
      : { start: $("#dateStart").value || null, end: $("#dateEnd").value || null };
    try {
      await api(`/api/files/${encodeURIComponent(f.name)}/dates`, { json: body });
      toast("Периодът е запазен.");
      loadFiles();
    } catch (e) { toast(e.message, true); }
  };
  dlg.returnValue = "";
  dlg.showModal();
}

async function savePlaylist() {
  try {
    await api("/api/playlist", { json: {
      order: files.map((f) => f.name),
      enabled: Object.fromEntries(files.map((f) => [f.name, f.enabled])),
    } });
    renderFiles(status && status.player.current);
  } catch (e) { toast(e.message, true); }
}
function move(i, d) {
  const j = i + d;
  [files[i], files[j]] = [files[j], files[i]];
  savePlaylist();
}
async function playNow(name) {
  try { await api(`/api/files/${encodeURIComponent(name)}/play`, { method: "POST" }); toast("Пуска се: " + name); setTimeout(refreshStatus, 500); }
  catch (e) { toast(e.message, true); }
}
async function del(name) {
  if (!confirm(`Да изтрия ли „${name}“? Това не може да се върне.`)) return;
  try { await api(`/api/files/${encodeURIComponent(name)}`, { method: "DELETE" }); toast("Изтрито."); loadFiles(); }
  catch (e) { toast(e.message, true); }
}
function rename(name) {
  const dlg = $("#renameDialog"), input = $("#renameInput");
  const dot = name.lastIndexOf(".");
  input.value = dot > 0 ? name.slice(0, dot) : name;
  busy = true;
  dlg.onclose = async () => {
    busy = false;
    if (dlg.returnValue !== "ok" || !input.value.trim()) return;
    try { await api(`/api/files/${encodeURIComponent(name)}/rename`, { json: { name: input.value.trim() } }); loadFiles(); }
    catch (e) { toast(e.message, true); }
  };
  dlg.returnValue = "";
  dlg.showModal();
}

// ---------------------------------------------------------------- upload
const queue = [];
let uploading = false;

function addUploads(list) {
  for (const file of list) {
    const bar = el("i");
    const pct = el("span", {}, "чака…");
    const item = el("div", { class: "up-item" },
      el("div", { class: "up-name" }, el("span", {}, file.name), pct),
      el("div", { class: "bar" }, bar));
    $("#uploads").append(item);
    queue.push({ file, item, bar, pct });
  }
  runQueue();
}

function runQueue() {
  if (uploading || !queue.length) return;
  uploading = true;
  const job = queue.shift();
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/upload");
  xhr.setRequestHeader("X-Filename", encodeURIComponent(job.file.name));
  xhr.setRequestHeader("Content-Type", "application/octet-stream");
  xhr.upload.onprogress = (e) => {
    if (!e.lengthComputable) return;
    const p = Math.round((e.loaded / e.total) * 100);
    job.bar.style.width = p + "%";
    job.pct.textContent = p < 100 ? `${p}% · ${fmtSize(e.loaded)} / ${fmtSize(e.total)}` : "обработва се…";
  };
  const finish = (ok, msg, warnings) => {
    uploading = false;
    job.item.classList.add(ok ? "done" : "fail");
    job.bar.style.width = "100%";
    job.pct.textContent = ok ? "готово" : "грешка";
    if (msg) job.item.append(el("div", { class: "hint" }, msg));
    if (warnings && warnings.length) job.item.append(el("div", { class: "fwarn" }, warnings.map((w) => el("p", {}, "⚠ " + w))));
    if (ok && !(warnings && warnings.length)) setTimeout(() => job.item.remove(), 4000);
    loadFiles();
    runQueue();
  };
  xhr.onload = () => {
    let data = {};
    try { data = JSON.parse(xhr.responseText); } catch (e) { /* */ }
    if (xhr.status === 200) finish(true, data.name !== job.file.name ? `Запазен като „${data.name}“` : "", data.warnings);
    else finish(false, data.error || `Грешка ${xhr.status}`);
  };
  xhr.onerror = () => finish(false, "Връзката прекъсна.");
  xhr.send(job.file);
}

$("#fileInput").addEventListener("change", (e) => { addUploads(e.target.files); e.target.value = ""; });
const dz = $("#dropZone");
["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
dz.addEventListener("drop", (e) => addUploads(e.dataTransfer.files));
window.addEventListener("beforeunload", (e) => { if (uploading) { e.preventDefault(); e.returnValue = ""; } });

// ---------------------------------------------------------------- playback settings
async function loadPlayback() {
  const s = (await api("/api/status")).settings;
  const fl = (await api("/api/files")).files;
  $$("input[name=mode]").forEach((r) => (r.checked = r.value === s.mode));
  const sel = $("#singleFile");
  sel.replaceChildren(...fl.map((f) => el("option", { value: f.name, selected: f.name === s.single_file }, f.name)));
  $("#singleField").classList.toggle("hidden", s.mode !== "single");
  $("#imageDuration").value = s.image_duration;
  $("#volume").value = s.volume;
  $("#volumeVal").textContent = s.volume;
  $("#fade").checked = s.fade;
  $("#fadeDuration").value = s.fade_duration;
  $("#fadeVal").textContent = s.fade_duration;
  $("#fadeField").classList.toggle("off", !s.fade);
}
$("#fade").addEventListener("change", (e) => $("#fadeField").classList.toggle("off", !e.target.checked));
$("#fadeDuration").addEventListener("input", (e) => ($("#fadeVal").textContent = e.target.value));
$$("input[name=mode]").forEach((r) => r.addEventListener("change", () =>
  $("#singleField").classList.toggle("hidden", r.value !== "single" || !r.checked)));
$("#volume").addEventListener("input", (e) => ($("#volumeVal").textContent = e.target.value));
$("#savePlayback").addEventListener("click", async () => {
  const mode = ($("input[name=mode]:checked") || {}).value || "all";
  try {
    await api("/api/settings/playback", { json: {
      mode, single_file: mode === "single" ? $("#singleFile").value : null,
      image_duration: parseInt($("#imageDuration").value, 10), volume: parseInt($("#volume").value, 10),
      fade: $("#fade").checked, fade_duration: parseFloat($("#fadeDuration").value),
    } });
    toast("Запазено.");
    refreshStatus();
  } catch (e) { toast(e.message, true); }
});

// ---------------------------------------------------------------- schedule
let schedule = null;

async function loadSchedule() {
  schedule = await api("/api/schedule");
  renderSchedule();
}
function renderSchedule() {
  $("#schedEnabled").checked = schedule.enabled;
  $("#days").classList.toggle("disabled", !schedule.enabled);
  const box = $("#days");
  box.replaceChildren();
  schedule.days.forEach((d, i) => {
    const row = el("div", { class: "day" + (d.enabled ? "" : " off") },
      el("b", {}, DAYS[i]),
      el("div", { class: "t" }, el("label", {}, "Включване"),
        el("input", { type: "time", value: d.on, disabled: !d.enabled, onchange: (e) => (d.on = e.target.value) })),
      el("div", { class: "t" }, el("label", {}, "Изключване"),
        el("input", { type: "time", value: d.off, disabled: !d.enabled, onchange: (e) => (d.off = e.target.value) })),
      el("input", { type: "checkbox", class: "switch small", checked: d.enabled, "aria-label": DAYS_LONG[i],
        onchange: (e) => { d.enabled = e.target.checked; renderSchedule(); } }));
    box.append(row);
  });
  const n = status && status.schedule.next;
  $("#schedNext").textContent = schedule.enabled && n
    ? `Следващо: ${n.kind === "on" ? "включване" : "изключване"} в ${DAYS_LONG[n.day]} (${n.date}) в ${n.time} ч.`
    : schedule.enabled ? "" : "Графикът е изключен – телевизорът се управлява само ръчно.";
}
$("#schedEnabled").addEventListener("change", (e) => { schedule.enabled = e.target.checked; renderSchedule(); });
function copyFromMonday(indices) {
  const m = schedule.days[0];
  indices.forEach((i) => (schedule.days[i] = { enabled: m.enabled, on: m.on, off: m.off }));
  renderSchedule();
}
$("#copyWeekdays").addEventListener("click", () => copyFromMonday([1, 2, 3, 4]));
$("#copyAll").addEventListener("click", () => copyFromMonday([1, 2, 3, 4, 5, 6]));
$("#saveSchedule").addEventListener("click", async () => {
  try {
    await api("/api/schedule", { json: schedule });
    toast("Графикът е запазен.");
    await refreshStatus();
    renderSchedule();
  } catch (e) { toast(e.message, true); }
});

// ---------------------------------------------------------------- settings
async function loadSettings() {
  const w = await api("/api/wifi");
  $("#wifiSsid").value = w.ssid;
  $("#wifiPass").value = w.password;
  const s = await api("/api/system");
  const info = $("#sysInfo");
  info.replaceChildren(
    el("dt", {}, "Адрес"), el("dd", {}, s.ips.map((ip) => "http://" + ip).join(", ")),
    el("dt", {}, "Име"), el("dd", {}, s.hostname),
    el("dt", {}, "Карта"), el("dd", {}, `${fmtSize(s.disk.free)} свободни от ${fmtSize(s.disk.total)}`),
    el("dt", {}, "Версия"), el("dd", {}, s.version));
  const st = await api("/api/status");
  $$("input[name=powerMode]").forEach((r) => (r.checked = r.value === st.settings.power_off_mode));
  renderProtection(st.protection);
}

$("#savePower").addEventListener("click", async () => {
  const mode = ($("input[name=powerMode]:checked") || {}).value;
  if (!mode) return;
  try { await api("/api/settings/playback", { json: { power_off_mode: mode } }); toast("Запазено."); }
  catch (e) { toast(e.message, true); }
});

function renderProtection(p) {
  const box = $("#protectBox");
  const pending = p.active !== p.next_boot;
  const label = p.active ? "Включена" : "Изключена";
  const extra = pending ? (p.next_boot ? " – ще се включи след рестарт" : " – ще се изключи след рестарт") : "";
  const btn = p.supported
    ? el("button", { class: p.next_boot ? "secondary" : "primary", style: "margin-top:0",
        onclick: () => setProtection(!p.next_boot) }, p.next_boot ? "Изключи защитата" : "Включи защитата")
    : el("p", { class: "hint", style: "margin:0" }, "Не се поддържа на това устройство.");
  box.replaceChildren(
    el("div", { class: "protect-state" }, el("span", { class: "dot " + (pending ? "pending" : p.active ? "on" : "") }), label + extra),
    btn);
}

async function setProtection(on) {
  const msg = on
    ? "Ще включа защитата. Raspberry Pi ще се рестартира (до 2-3 минути без картина). Продължаване?"
    : "Ще изключа защитата (напр. за обновяване). Raspberry Pi ще се рестартира. Продължаване?";
  if (!confirm(msg)) return;
  try { await api("/api/system", { json: { action: on ? "protect-on" : "protect-off" } }); toast("Изпълнява се – Пи-то ще се рестартира…"); }
  catch (e) { toast(e.message, true); }
}

$("#restoreFile").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  let backup;
  try { backup = JSON.parse(await file.text()); } catch (err) { toast("Файлът не е валиден JSON.", true); return; }
  const wifi = $("#restoreWifi").checked;
  if (!confirm(`Да възстановя ли настройките от „${file.name}“?` + (wifi ? " Wi-Fi мрежата също ще се смени и връзката ще прекъсне." : ""))) return;
  try {
    const r = await api("/api/restore", { json: { backup, restore_wifi: wifi } });
    toast("Възстановено: " + r.restored.join(", "));
    loadFiles();
    refreshStatus();
  } catch (err) { toast(err.message, true); }
});

// ---------------------------------------------------------------- screen preview
let screenTimer = null;
async function loadScreen() {
  const info = $("#screenInfo");
  info.textContent = "Зарежда се…";
  try {
    const r = await fetch("/api/screenshot?t=" + Date.now());
    if (!r.ok) {
      let msg = "Няма картина.";
      try { msg = (await r.json()).error || msg; } catch (e) { /* */ }
      $("#screenImg").removeAttribute("src");
      info.textContent = msg;
      return;
    }
    const url = URL.createObjectURL(await r.blob());
    const img = $("#screenImg");
    if (img.src.startsWith("blob:")) URL.revokeObjectURL(img.src);
    img.src = url;
    info.textContent = "Кадър от " + new Date().toLocaleTimeString("bg-BG", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch (e) { info.textContent = "Няма връзка."; }
}
$("#screenBtn").addEventListener("click", () => {
  const box = $("#screenBox");
  const open = box.classList.toggle("hidden") === false;
  $("#screenBtn").textContent = open ? "Скрий екрана" : "Покажи екрана";
  $("#screenBtn").setAttribute("aria-expanded", open);
  clearInterval(screenTimer);
  if (open) { loadScreen(); screenTimer = setInterval(() => { if (!document.hidden) loadScreen(); }, 15000); }
});
$("#screenRefresh").addEventListener("click", loadScreen);
$("#saveWifi").addEventListener("click", async () => {
  const ssid = $("#wifiSsid").value.trim(), password = $("#wifiPass").value;
  if (!confirm(`Мрежата ще се рестартира като „${ssid}“ с парола „${password}“. Продължаване?`)) return;
  try { await api("/api/wifi", { json: { ssid, password } }); toast("Запазено. Свържете се отново към новата мрежа."); }
  catch (e) { toast(e.message, true); }
});
const SYS_CONFIRM = {
  restart: "Плеърът ще се рестартира (няколко секунди черен екран).",
  reboot: "Raspberry Pi ще се рестартира (около 1 минута).",
  shutdown: "Raspberry Pi ще се изключи. За да тръгне отново, трябва да извадите и поставите захранването.",
};
$$("[data-sys]").forEach((b) => b.addEventListener("click", async () => {
  if (!confirm(SYS_CONFIRM[b.dataset.sys] + " Продължаване?")) return;
  try { await api("/api/system", { json: { action: b.dataset.sys } }); toast("Изпълнява се…"); }
  catch (e) { toast(e.message, true); }
}));

// ---------------------------------------------------------------- hint for captive-portal windows
const hint = $("#cnaHint");
if (!localStorage.getItem("cnaHintSeen")) {
  hint.classList.remove("hidden");
  hint.addEventListener("click", () => { hint.classList.add("hidden"); localStorage.setItem("cnaHintSeen", "1"); });
}

// ---------------------------------------------------------------- start
syncTime();
refreshStatus();
loadFiles();
setInterval(refreshStatus, 3000);
setInterval(() => { if (!uploading) loadFiles(); }, 10000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) { syncTime(); refreshStatus(); loadFiles(); } });
