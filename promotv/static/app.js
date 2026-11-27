"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
const DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"];
const DAYS_LONG = ["понеделник", "вторник", "сряда", "четвъртък", "петък", "събота", "неделя"];
const MODES = { all: "По ред", shuffle: "Разбъркано", single: "Един файл в цикъл" };

const ICON = {
  up: '<svg viewBox="0 0 24 24"><path d="M12 7l6 6-1.4 1.4L12 9.8l-4.6 4.6L6 13z"/></svg>',
  down: '<svg viewBox="0 0 24 24"><path d="M12 17l-6-6 1.4-1.4 4.6 4.6 4.6-4.6L18 11z"/></svg>',
  chev: '<svg class="chev" viewBox="0 0 24 24"><path d="M12 15.5l-6-6 1.4-1.4 4.6 4.6 4.6-4.6L18 9.5z"/></svg>',
  video: '<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>',
  image: '<svg viewBox="0 0 24 24"><path d="M4 5h16v14H4zm2 2v8.6l3.5-3.6 2.5 2.5 3-3.5 3 3.4V7zm3 1.5a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3z"/></svg>',
};

let files = [];
let status = null;
let busy = false;          // pause list refresh while a dialog is open
let openItem = null;       // name of the expanded playlist row
let live = false;          // "live frame" mode on the screen

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
  if (b >= 1e9) return (b / 1e9).toFixed(2).replace(".", ",") + " GB";
  if (b >= 1e6) return (b / 1e6).toFixed(1).replace(".", ",") + " MB";
  return Math.max(1, Math.round(b / 1e3)) + " KB";
}
function fmtDur(s) {
  s = Math.round(s || 0);
  const m = Math.floor(s / 60), sec = String(s % 60).padStart(2, "0");
  return m >= 60 ? `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}
const fmtDate = (d) => d ? `${d.slice(8, 10)}.${d.slice(5, 7)}.${d.slice(0, 4)}` : "";
const fmtDateShort = (d) => d ? `${d.slice(8, 10)}.${d.slice(5, 7)}` : "";

function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k === "html") e.innerHTML = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v === true) e.setAttribute(k, "");
    else if (v !== false && v != null) e.setAttribute(k, v);
  }
  for (const c of children.flat()) if (c != null && c !== false) e.append(c);
  return e;
}

// ---------------------------------------------------------------- tabs
$$(".tab").forEach((b) => b.addEventListener("click", () => {
  $$(".tab").forEach((x) => x.classList.toggle("active", x === b));
  $$(".panel").forEach((p) => p.classList.toggle("hidden", p.id !== "tab-" + b.dataset.tab));
  if (b.dataset.tab === "schedule") loadSchedule();
  if (b.dataset.tab === "settings") loadSettings();
  if (b.dataset.tab === "play") loadPlayback();
  const panel = $("#tab-" + b.dataset.tab);
  if (panel.getBoundingClientRect().top < 0) panel.scrollIntoView({ block: "start" });
}));

// ---------------------------------------------------------------- time sync
async function syncTime() {
  try {
    const r = await api("/api/time", { json: { epoch_ms: Date.now() } });
    if (r.correction && Math.abs(r.correction) > 60) toast("Часът на устройството е сверен с телефона.");
  } catch (e) { /* ignore */ }
}

// ---------------------------------------------------------------- status + screen
async function refreshStatus() {
  try { status = await api("/api/status"); } catch (e) { return; }
  const s = status;
  const p = s.player;
  const pw = s.tv.power;
  const tvOff = !p.output || pw === "standby";

  $("#nowFile").textContent = p.current || (files.length ? "Нищо не се показва" : "Плейлистът е празен");
  $("#btnPause").setAttribute("aria-label", p.paused ? "Продължи" : "Пауза");
  $("#icoPause").innerHTML = p.paused ? '<path d="M7 4v16l13-8z"/>' : '<path d="M7 5h4v14H7zM13 5h4v14h-4z"/>';

  const onair = $("#onair");
  onair.className = "onair " + (tvOff ? "" : p.paused ? "paused" : p.current ? "live" : "");
  $("#onairText").textContent = !p.output ? "Без сигнал" : pw === "standby" ? "Изключен"
    : p.paused ? "Пауза" : p.current ? "В ефир" : "Празно";

  const power = $(".power");
  power.classList.toggle("is-on", !tvOff && pw === "on");
  power.classList.toggle("is-off", tvOff);

  const tv = $("#chipTv");
  tv.textContent = !p.output ? "HDMI сигналът е спрян" : !s.tv.cec ? "Няма връзка с телевизора (CEC)"
    : pw === "on" ? "Телевизорът е включен" : pw === "standby" ? "Телевизорът е изключен" : "Телевизор: неизвестно";
  tv.className = tvOff ? "off" : pw === "on" ? "ok" : "";
  $("#chipMode").textContent = MODES[s.settings.mode];
  const n = s.schedule.next;
  $("#chipSched").textContent = !s.schedule.enabled ? "График: изключен"
    : n ? `${n.kind === "on" ? "Включва се" : "Изключва се"} ${DAYS[n.day]} в ${n.time}` : "График: включен";
  $("#chipSched").className = s.schedule.enabled ? "ok" : "";

  $("#clock").textContent = s.time.now.slice(11, 16);
  $("#clock").classList.toggle("bad", !s.time.trusted);
  $("#timeWarn").classList.toggle("hidden", s.time.trusted);
  $("#dataWarn").classList.toggle("hidden", s.protection.data_persistent);
  renderHealth(s.health);
  $("#disk").textContent = fmtSize(s.disk.free) + " свободни";

  $$(".item").forEach((li) => li.classList.toggle("playing", li.dataset.name === p.current));
  if (!live) paintScreen();
}

const fmtTemp = (t) => `${String(Math.round(t * 10) / 10).replace(".", ",")} °C`;

function renderHealth(h) {
  const pill = $("#temp");
  if (!h || h.temp == null) { pill.classList.add("hidden"); }
  else {
    pill.classList.remove("hidden");
    pill.textContent = `${Math.round(h.temp)}°`;
    pill.className = "pill temp" + (h.level === "hot" ? " hot" : h.level === "warm" ? " warm" : "");
  }
  const box = $("#healthWarn");
  let msg = "", cls = "warn";
  if (h && h.undervoltage_now) {
    msg = "Захранването е слабо в момента. Raspberry Pi може да забие или да насича. Използвайте захранване 5 V / 2,5 A, най-добре официалното, и къс кабел.";
    cls = "danger";
  } else if (h && h.level === "hot") {
    msg = `Raspberry Pi прегрява (${fmtTemp(h.temp)}) и се забавя, видеото може да насича. Сложете радиатори и осигурете въздух около него.`;
    cls = "danger";
  } else if (h && h.level === "warm" && h.throttled_now) {
    msg = `Raspberry Pi е топъл (${fmtTemp(h.temp)}) и вече намалява скоростта си. Радиатор или проветрива кутия ще помогнат.`;
  }
  box.textContent = msg;
  box.className = "notice " + cls + (msg ? "" : " hidden");
}

function healthFacts(h) {
  if (!h) return [];
  const temp = h.temp == null ? "неизвестна"
    : fmtTemp(h.temp) + (h.level === "hot" ? ", прегрява" : h.level === "warm" ? ", топъл" : ", нормална");
  let power = "неизвестно";
  if (h.known) {
    power = h.undervoltage_now ? "слабо в момента"
      : h.undervoltage_past ? "имало е спадове след последното включване" : "нормално";
  }
  const facts = [el("dt", {}, "Температура"), el("dd", {}, temp), el("dt", {}, "Захранване"), el("dd", {}, power)];
  if (h.known && (h.throttled_now || h.throttled_past)) {
    facts.push(el("dt", {}, "Забавяне"), el("dd", {}, h.throttled_now ? "в момента се забавя заради топлина или захранване"
      : "забавял се е след последното включване"));
  }
  return facts;
}

let screenFor = null;   // which file the screen image currently shows
function paintScreen() {
  const s = status;
  if (!s) return;
  const screen = $("#screen"), img = $("#screenImg"), msg = $("#screenMsg");
  const p = s.player;
  const cur = p.current;
  const f = files.find((x) => x.name === cur);
  screen.classList.toggle("blank", !cur);
  screen.classList.toggle("dim", !p.output || s.tv.power === "standby");
  if (!live && screenFor !== cur) {
    screenFor = cur;
    if (f && f.thumb) img.src = `/thumbs/${f.thumb}.jpg`;
    else img.removeAttribute("src");
  }
  if (!cur) msg.textContent = files.length ? "Няма активни файлове – проверете периодите и превключвателите" : "Качете първия файл от „Плейлист“";
  else if (!p.output) msg.textContent = "HDMI сигналът е спрян";
  else if (!img.getAttribute("src")) msg.textContent = f && f.type === "image" ? "Снимка" : "Видео";
  else msg.textContent = "";
  if (!live) {
    const meta = f ? [f.type === "image" ? "Снимка" : "Видео", f.duration && f.type === "video" ? fmtDur(f.duration) : null,
      f.width ? `${f.width}×${f.height}` : null].filter(Boolean).join(", ") : "";
    $("#screenInfo").textContent = meta;
  }
}

$$("[data-player]").forEach((b) => b.addEventListener("click", async () => {
  try { await api("/api/player", { json: { action: b.dataset.player } }); setTimeout(refreshStatus, 300); }
  catch (e) { toast(e.message, true); }
}));
$$("[data-tv]").forEach((b) => b.addEventListener("click", async () => {
  try {
    await api("/api/tv", { json: { action: b.dataset.tv } });
    toast(b.dataset.tv === "on" ? "Телевизорът се включва…" : "Телевизорът се изключва…");
    setTimeout(refreshStatus, 1500);
  } catch (e) { toast(e.message, true); }
}));

// live frame from the TV
let liveTimer = null;
async function loadScreen() {
  const info = $("#screenInfo");
  try {
    const r = await fetch("/api/screenshot?t=" + Date.now());
    if (!r.ok) {
      let m = "Няма картина.";
      try { m = (await r.json()).error || m; } catch (e) { /* */ }
      info.textContent = m;
      return;
    }
    const url = URL.createObjectURL(await r.blob());
    const img = $("#screenImg");
    if (img.src.startsWith("blob:")) URL.revokeObjectURL(img.src);
    img.src = url;
    $("#screenMsg").textContent = "";
    info.textContent = "Жив кадър от " + new Date().toLocaleTimeString("bg-BG", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch (e) { info.textContent = "Няма връзка с устройството."; }
}
$("#screenBtn").addEventListener("click", () => {
  live = !live;
  $("#screenBtn").setAttribute("aria-pressed", live);
  clearInterval(liveTimer);
  if (live) {
    $("#screenInfo").textContent = "Зарежда се кадър…";
    loadScreen();
    liveTimer = setInterval(() => { if (!document.hidden) loadScreen(); }, 10000);
  } else {
    screenFor = null;
    paintScreen();
  }
});

// ---------------------------------------------------------------- playlist
async function loadFiles() {
  if (busy) return;
  try {
    const r = await api("/api/files");
    files = r.files;
    renderFiles(r.current);
    paintScreen();
  } catch (e) { /* offline */ }
}

function tagsFor(f, current) {
  const t = [];
  if (f.name === current) t.push(el("span", { class: "tag live" }, "В ефир"));
  if (!f.enabled) t.push(el("span", { class: "tag" }, "Скрит"));
  if (f.start || f.end) {
    const range = f.start && f.end ? `${fmtDateShort(f.start)} – ${fmtDateShort(f.end)}`
      : f.start ? `от ${fmtDateShort(f.start)}` : `до ${fmtDateShort(f.end)}`;
    if (f.date_status === "upcoming") t.push(el("span", { class: "tag upcoming" }, `Предстои ${range}`));
    else if (f.date_status === "expired") t.push(el("span", { class: "tag" }, `Изтекъл ${range}`));
    else t.push(el("span", { class: "tag period" }, range));
  }
  if (f.warnings.length) t.push(el("span", { class: "tag warn" }, "Може да насича"));
  return t;
}

function renderFiles(current) {
  const ul = $("#files");
  ul.replaceChildren();
  $("#filesEmpty").classList.toggle("hidden", files.length > 0);
  const shown = files.filter((f) => f.enabled && f.date_status === "active").length;
  $("#filesCount").textContent = files.length ? `Плейлист, ${shown} от ${files.length}` : "Плейлист";
  files.forEach((f, i) => {
    const thumb = el("span", { class: "thumb", html: f.thumb ? "" : ICON[f.type === "image" ? "image" : "video"] });
    if (f.thumb) thumb.style.backgroundImage = `url(/thumbs/${f.thumb}.jpg)`;
    const meta = el("span", { class: "meta" },
      el("span", {}, f.type === "image" ? "Снимка" : "Видео"),
      f.type === "video" && f.duration ? el("span", {}, fmtDur(f.duration)) : null,
      f.width ? el("span", {}, `${f.width}×${f.height}`) : null,
      el("span", {}, fmtSize(f.size)),
      f.processing ? el("span", {}, "проверява се…") : null);
    const isOpen = openItem === f.name;
    const off = !f.enabled || f.date_status !== "active";
    const li = el("li", { class: "item" + (off ? " off" : "") + (f.name === current ? " playing" : "") + (isOpen ? " open" : ""), "data-name": f.name },
      el("button", { class: "item-main", "aria-expanded": isOpen ? "true" : "false",
          onclick: () => { openItem = isOpen ? null : f.name; renderFiles(status && status.player.current); } },
        el("span", { class: "pos" }, String(i + 1)),
        thumb,
        el("span", { class: "item-text" },
          el("span", { class: "name" }, f.name),
          meta,
          el("span", { class: "tags" }, tagsFor(f, current))),
        el("span", { html: ICON.chev })));
    if (isOpen) {
      const toggle = el("input", { type: "checkbox", class: "switch small", checked: f.enabled,
        onchange: (e) => { f.enabled = e.target.checked; savePlaylist(); } });
      li.append(el("div", { class: "item-body" },
        f.warnings.length ? el("div", { class: "warnings" }, f.warnings.map((w) => el("p", {}, w))) : null,
        el("label", { class: "toggle" }, toggle, "Показвай в плейлиста"),
        el("div", { class: "actions" },
          el("button", { class: "act play", onclick: () => playNow(f.name) }, "Пусни сега"),
          el("button", { class: "act icon", "aria-label": "По-нагоре", disabled: i === 0, onclick: () => move(i, -1), html: ICON.up }),
          el("button", { class: "act icon", "aria-label": "По-надолу", disabled: i === files.length - 1, onclick: () => move(i, 1), html: ICON.down }),
          el("button", { class: "act", onclick: () => editDates(f) }, "Период"),
          el("button", { class: "act", onclick: () => rename(f.name) }, "Преименувай"),
          el("button", { class: "act del", onclick: () => del(f.name) }, "Изтрий"))));
    }
    ul.append(li);
  });
}

async function savePlaylist() {
  try {
    await api("/api/playlist", { json: {
      order: files.map((f) => f.name),
      enabled: Object.fromEntries(files.map((f) => [f.name, f.enabled])),
    } });
    await loadFiles();
  } catch (e) { toast(e.message, true); }
}
function move(i, d) {
  const j = i + d;
  [files[i], files[j]] = [files[j], files[i]];
  renderFiles(status && status.player.current);
  savePlaylist();
}
async function playNow(name) {
  try { await api(`/api/files/${encodeURIComponent(name)}/play`, { method: "POST" }); toast("Пуска се: " + name); setTimeout(refreshStatus, 500); }
  catch (e) { toast(e.message, true); }
}
async function del(name) {
  if (!confirm(`Да изтрия ли „${name}“? Файлът се изтрива окончателно.`)) return;
  try { await api(`/api/files/${encodeURIComponent(name)}`, { method: "DELETE" }); openItem = null; toast("Изтрито."); loadFiles(); }
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
    try {
      const r = await api(`/api/files/${encodeURIComponent(name)}/rename`, { json: { name: input.value.trim() } });
      openItem = r.name;
      toast("Преименувано.");
      loadFiles();
    } catch (e) { toast(e.message, true); }
  };
  dlg.returnValue = "";
  dlg.showModal();
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
      toast(body.start || body.end ? `Период: ${fmtDate(body.start) || "…"} – ${fmtDate(body.end) || "…"}` : "Периодът е премахнат.");
      loadFiles();
    } catch (e) { toast(e.message, true); }
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
    const pct = el("span", {}, "чака");
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
    job.pct.textContent = p < 100 ? `${p}%  ${fmtSize(e.loaded)} от ${fmtSize(e.total)}` : "проверява се…";
  };
  const finish = (ok, msg, warnings) => {
    uploading = false;
    job.item.classList.add(ok ? "done" : "fail");
    job.bar.style.width = "100%";
    job.pct.textContent = ok ? "качено" : "не е качено";
    if (msg) job.item.append(el("p", { class: "hint" }, msg));
    if (warnings && warnings.length) job.item.append(el("div", { class: "warnings" }, warnings.map((w) => el("p", {}, w))));
    if (ok && !(warnings && warnings.length)) setTimeout(() => job.item.remove(), 4000);
    loadFiles();
    runQueue();
  };
  xhr.onload = () => {
    let data = {};
    try { data = JSON.parse(xhr.responseText); } catch (e) { /* */ }
    if (xhr.status === 200) finish(true, data.name !== job.file.name ? `Запазен като „${data.name}“.` : "", data.warnings);
    else finish(false, data.error || `Грешка ${xhr.status}`);
  };
  xhr.onerror = () => finish(false, "Връзката с устройството прекъсна. Проверете Wi-Fi и опитайте отново.");
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
  $("#singleFile").replaceChildren(...fl.map((f) => el("option", { value: f.name, selected: f.name === s.single_file }, f.name)));
  $("#singleField").classList.toggle("hidden", s.mode !== "single");
  $("#imageDuration").value = s.image_duration;
  $("#volume").value = s.volume;
  $("#volumeVal").textContent = s.volume;
  $("#fade").checked = s.fade;
  $("#fadeDuration").value = s.fade_duration;
  $("#fadeVal").textContent = s.fade_duration;
  $("#fadeField").classList.toggle("off", !s.fade);
}
$$("input[name=mode]").forEach((r) => r.addEventListener("change", () =>
  $("#singleField").classList.toggle("hidden", r.value !== "single" || !r.checked)));
$("#volume").addEventListener("input", (e) => ($("#volumeVal").textContent = e.target.value));
$("#fade").addEventListener("change", (e) => $("#fadeField").classList.toggle("off", !e.target.checked));
$("#fadeDuration").addEventListener("input", (e) => ($("#fadeVal").textContent = e.target.value));
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
let openDay = null;
const toMin = (t) => { const [h, m] = t.split(":").map(Number); return h * 60 + m; };

async function loadSchedule() {
  schedule = await api("/api/schedule");
  renderSchedule();
}
function segments(d) {
  const on = toMin(d.on), off = toMin(d.off);
  if (off > on) return [[on, off]];
  return [[on, 1440], [0, off]];   // past midnight
}
function renderSchedule() {
  $("#schedEnabled").checked = schedule.enabled;
  const box = $("#days");
  box.classList.toggle("disabled", !schedule.enabled);
  box.replaceChildren(el("div", { class: "scale", "aria-hidden": "true" },
    el("b", {}), el("span", {}, el("i", {}, "0"), el("i", {}, "6"), el("i", {}, "12"), el("i", {}, "18"), el("i", {}, "24")),
    el("b", {}), el("b", {})));
  const now = new Date();
  const today = (now.getDay() + 6) % 7;
  schedule.days.forEach((d, i) => {
    const track = el("span", { class: "track" });
    if (d.enabled) segments(d).forEach(([a, b]) => {
      const seg = el("i");
      seg.style.left = (a / 1440 * 100) + "%";
      seg.style.width = ((b - a) / 1440 * 100) + "%";
      track.append(seg);
    });
    if (i === today) {
      const mark = el("span", { class: "now" });
      mark.style.left = ((now.getHours() * 60 + now.getMinutes()) / 1440 * 100) + "%";
      track.append(mark);
    }
    const editing = openDay === i && d.enabled;
    const row = el("div", { class: "day" + (d.enabled ? "" : " off") },
      el("div", { class: "day-row" },
        el("span", { class: "day-name", title: DAYS_LONG[i] }, DAYS[i]),
        track,
        el("button", { class: "day-times", disabled: !d.enabled, "aria-expanded": editing ? "true" : "false",
          onclick: () => { openDay = editing ? null : i; renderSchedule(); } },
          d.enabled ? `${d.on}–${d.off}` : "почивен"),
        el("input", { type: "checkbox", class: "switch small", checked: d.enabled, "aria-label": "Работен ден: " + DAYS_LONG[i],
          onchange: (e) => { d.enabled = e.target.checked; openDay = d.enabled ? i : null; renderSchedule(); } })),
      editing ? el("div", { class: "day-edit" },
        el("div", {}, el("label", { for: "on" + i }, "Включване"),
          el("input", { type: "time", id: "on" + i, value: d.on, onchange: (e) => { d.on = e.target.value; renderSchedule(); } })),
        el("div", {}, el("label", { for: "off" + i }, "Изключване"),
          el("input", { type: "time", id: "off" + i, value: d.off, onchange: (e) => { d.off = e.target.value; renderSchedule(); } })))
        : null);
    box.append(row);
  });
  const n = status && status.schedule.next;
  $("#schedNext").textContent = !schedule.enabled ? "Изключено. Телевизорът се управлява само ръчно."
    : n ? `Следва: ${n.kind === "on" ? "включване" : "изключване"} в ${DAYS_LONG[n.day]}, ${n.date} в ${n.time}.`
      : "Включено.";
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
    openDay = null;
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
  $("#sysInfo").replaceChildren(
    el("dt", {}, "Адрес"), el("dd", {}, s.ips.map((ip) => "http://" + ip).join(", ")),
    el("dt", {}, "Име"), el("dd", {}, s.hostname),
    el("dt", {}, "Карта"), el("dd", {}, `${fmtSize(s.disk.free)} свободни от ${fmtSize(s.disk.total)}`),
    el("dt", {}, "Версия"), el("dd", {}, s.version));
  const st = await api("/api/status");
  $("#sysInfo").append(...healthFacts(st.health));
  $$("input[name=powerMode]").forEach((r) => (r.checked = r.value === st.settings.power_off_mode));
  renderProtection(st.protection);
}

$("#saveWifi").addEventListener("click", async () => {
  const ssid = $("#wifiSsid").value.trim(), password = $("#wifiPass").value;
  if (!confirm(`Мрежата ще се рестартира като „${ssid}“ с парола „${password}“. Продължаване?`)) return;
  try { await api("/api/wifi", { json: { ssid, password } }); toast("Wi-Fi е запазен. Свържете се отново към новата мрежа."); }
  catch (e) { toast(e.message, true); }
});

$("#savePower").addEventListener("click", async () => {
  const mode = ($("input[name=powerMode]:checked") || {}).value;
  if (!mode) return;
  try { await api("/api/settings/playback", { json: { power_off_mode: mode } }); toast("Запазено."); }
  catch (e) { toast(e.message, true); }
});

function renderProtection(p) {
  const pending = p.active !== p.next_boot;
  const label = pending ? (p.next_boot ? "Включва се при следващия рестарт" : "Изключва се при следващия рестарт")
    : p.active ? "Включена" : "Изключена";
  const btn = p.supported
    ? el("button", { class: "btn" + (p.next_boot ? "" : " primary"), onclick: () => setProtection(!p.next_boot) },
        p.next_boot ? "Изключи" : "Включи")
    : el("span", { class: "hint", style: "margin:0" }, "Не се поддържа тук");
  $("#protectBox").replaceChildren(
    el("div", { class: "protect-state" }, el("span", { class: "dot " + (pending ? "pending" : p.active ? "on" : "") }), label),
    btn);
}
async function setProtection(on) {
  const msg = on
    ? "Защитата ще се включи и Пи-то ще се рестартира (2–3 минути без картина). Продължаване?"
    : "Защитата ще се изключи (например за обновяване) и Пи-то ще се рестартира. Продължаване?";
  if (!confirm(msg)) return;
  try { await api("/api/system", { json: { action: on ? "protect-on" : "protect-off" } }); toast("Пи-то ще се рестартира след малко…"); }
  catch (e) { toast(e.message, true); }
}

$("#restoreFile").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  let backup;
  try { backup = JSON.parse(await file.text()); } catch (err) { toast("Файлът не е резервно копие от PromoTV.", true); return; }
  const wifi = $("#restoreWifi").checked;
  if (!confirm(`Да възстановя ли настройките от „${file.name}“?` + (wifi ? " Wi-Fi мрежата също ще се смени и връзката ще прекъсне." : ""))) return;
  try {
    const r = await api("/api/restore", { json: { backup, restore_wifi: wifi } });
    toast("Възстановено: " + r.restored.join(", ") + ".");
    loadFiles();
    refreshStatus();
  } catch (err) { toast(err.message, true); }
});

const SYS_CONFIRM = {
  restart: "Плеърът ще се рестартира – няколко секунди черен екран.",
  reboot: "Пи-то ще се рестартира – около минута без картина.",
  shutdown: "Пи-то ще се изключи. За да тръгне отново, извадете и поставете захранването.",
};
$$("[data-sys]").forEach((b) => b.addEventListener("click", async () => {
  if (!confirm(SYS_CONFIRM[b.dataset.sys] + " Продължаване?")) return;
  try { await api("/api/system", { json: { action: b.dataset.sys } }); toast("Изпълнява се…"); }
  catch (e) { toast(e.message, true); }
}));

// ---------------------------------------------------------------- hint for captive-portal windows
try {
  if (!localStorage.getItem("cnaHintSeen")) {
    $("#cnaHint").classList.remove("hidden");
    $("#cnaClose").addEventListener("click", () => { $("#cnaHint").classList.add("hidden"); localStorage.setItem("cnaHintSeen", "1"); });
  }
} catch (e) { /* storage blocked */ }

// ---------------------------------------------------------------- start
syncTime();
loadFiles().then(refreshStatus);
setInterval(refreshStatus, 3000);
setInterval(() => { if (!uploading) loadFiles(); }, 10000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) { syncTime(); refreshStatus(); loadFiles(); } });
