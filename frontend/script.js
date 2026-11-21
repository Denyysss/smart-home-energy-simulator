/**
 * script.js — ЕнергіяДім (SPA, інтегрований із SQLite БД через FastAPI)
 *
 * Структура:
 *   1. Конфігурація API (без хардкоду localhost) та Стан
 *   2. Універсальна модалка-форма openForm()
 *   3. Форми та CRUD-операції з БД (/api/houses, /api/houses/{id}/appliances)
 *   4. Виклик бекенду /api/simulate
 *   5. Рендер: shell та data
 *   6. Графіки Chart.js
 *   7. Навігація між сторінками
 */

const NIGHT = new Set([23, 0, 1, 2, 3, 4, 5, 6]);
const L24 = Array.from({ length: 24 }, (_, i) => String(i).padStart(2, "0") + ":00");
const PALETTE = ["#FACC15", "#38BDF8", "#818CF8", "#22C55E", "#F97316", "#EC4899", "#14B8A6", "#A3E635", "#F43F5E", "#60A5FA"];
const ICONS = {
    "Бойлер 80л": "🚰", "Холодильник": "🧊", "Освітлення (кімната)": "💡", "Пральна машина": "🧺", "Мікрохвильовка": "📡",
    "Кондиціонер": "❄️", "Телевізор": "📺", "Електрочайник": "☕", "Комп'ютер (ПК)": "🖥️", "Електроплита (1 конфорка)": "🍳",
    "Посудомийна машина": "🍽️", "Зарядка електромобіля": "🚗"
};
const CAT_ICON = { heating: "🔥", cooling: "❄️", lighting: "💡", entertainment: "📺" };
const VIEWS = {
    home: ["Головна", "Огляд споживання вашого будинку"],
    houses: ["Мої будинки", "Керуйте будинками та відстежуйте споживання електроенергії (збережено в БД)"],
    appliances: ["Прилади", "Додавайте, редагуйте та вмикайте прилади"],
    charts: ["Графіки", "Погодинне споживання та вартість"],
    analytics: ["Аналітика", "Прогноз витрат і поради з економії"],
    settings: ["Налаштування", "Профіль, тарифи, ліміти, графіки"],
};

// ============================================================
// 1. АВТОВИЗНАЧЕННЯ API (без хардкоду localhost:8000) + СТАН
// ============================================================
function detectDefaultApi() {
    if (window.ENERGYDIM_API_URL !== undefined) return window.ENERGYDIM_API_URL;
    // Якщо фронтенд роздається самим FastAPI або на продакшн-домені — використовуємо відносний шлях ""
    if (location.protocol.startsWith("http") && location.port !== "8080" && location.port !== "5500") {
        return "";
    }
    // Якщо відкритий через окремий dev-сервер (наприклад, порт 8080)
    const host = location.hostname || "localhost";
    const proto = location.protocol === "https:" ? "https:" : "http:";
    return `${proto}//${host}:8000`;
}

const KEY = "energydim.v3";
function defaultState() {
    return {
        auth: null, // { token, user: { id, name, username } }
        profile: { name: "Гість" },
        activeHouse: 1,
        settings: { api: detectDefaultApi(), showLimit: true, alerts: true, warnPercent: 80, chartMax: 0 },
        houses: [{ id: 1, name: "Мій будинок", address: "вул. Центральна, 8", voltage: 230, amps: 16, dayRate: 4.32, nightRate: 2.16, appliances: [] }],
        events: [],
    };
}
let state = load();
function load() {
    try {
        const s = JSON.parse(localStorage.getItem(KEY));
        if (s && s.settings) {
            return { ...defaultState(), ...s, settings: { ...defaultState().settings, ...s.settings } };
        }
    } catch (e) {}
    return defaultState();
}
function save() { localStorage.setItem(KEY, JSON.stringify(state)); }
const activeHouse = () => state.houses.find(h => h.id === state.activeHouse) || state.houses[0] || defaultState().houses[0];
const limitOf = h => (h.voltage || 230) * (h.amps || 16);
const apiUrl = path => (state.settings.api || "") + path;

let catalog = [];
let sim = null;
let range = "day";
let lastOverKey = null;

// ============================================================
// Утиліти
// ============================================================
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = v => parseFloat(String(v).replace(",", "."));
const fmt = (n, d = 2) => Number(n).toFixed(d);
function toast(msg) { const t = $("toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), 2200); }
function iconOf(a) { return ICONS[a.name] || CAT_ICON[a.category] || "🔌"; }
function dailyKwh(a) { return a.power * a.duty * a.qty * a.schedule.length / 1000; }
function scheduleText(s) {
    if (s.length === 24) return "Працює постійно";
    if (!s.length) return "Не заплановано";
    const x = [...s].sort((a, b) => a - b), r = []; let st = x[0], en = x[0];
    for (let i = 1; i < x.length; i++) { if (x[i] === en + 1) en = x[i]; else { r.push(`${st}:00 до ${en + 1}:00`); st = en = x[i]; } }
    r.push(`${st}:00 до ${en + 1}:00`);
    return "Працює з " + r.join(", ");
}
function addEvent(type, title, desc) {
    const time = new Date().toLocaleTimeString("uk-UA", { hour: "2-digit", minute: "2-digit" });
    state.events.unshift({ type, title, desc, time });
    state.events = state.events.slice(0, 20);
}

async function apiFetch(path, options = {}) {
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    if (state.auth && state.auth.token && !headers.Authorization) {
        headers.Authorization = "Bearer " + state.auth.token;
    }
    const r = await fetch(apiUrl(path), { ...options, headers });
    if (!r.ok) {
        let detail = "HTTP " + r.status;
        try { const err = await r.json(); if (err.detail) detail = typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail); } catch (e) {}
        if (r.status === 401 && !path.startsWith("/api/auth/login") && !path.startsWith("/api/auth/register")) {
            state.auth = null;
            save();
            showAuthScreen();
        }
        throw new Error(detail);
    }
    return r.json();
}

// ============================================================
// 2. УНІВЕРСАЛЬНА МОДАЛКА-ФОРМА
// ============================================================
let modalCtx = null;
function openForm({ title, fields, onSave, saveText = "Зберегти", hideSave = false }) {
    modalCtx = { fields, onSave, hours: new Set() };
    $("mTitle").textContent = title;
    $("mSave").textContent = saveText;
    $("mSave").style.display = hideSave ? "none" : "";
    $("mBody").innerHTML = fields.map(f => {
        const id = "f_" + f.key, hint = f.hint ? `<div class="f-h">${f.hint}</div>` : "";
        switch (f.type) {
            case "select":
                return `<label class="f-l" for="${id}">${f.label}</label><select class="f-i" id="${id}">${f.options.map(o =>
                    `<option value="${esc(o.v)}" ${String(o.v) === String(f.value) ? "selected" : ""}>${esc(o.l)}</option>`).join("")}</select>${hint}`;
            case "check":
                return `<label class="f-c"><input type="checkbox" id="${id}" ${f.value ? "checked" : ""}> ${f.label}</label>${hint}`;
            case "hours":
                modalCtx.hours = new Set(f.value || []);
                return `<label class="f-l">${f.label}</label><div class="hours" id="${id}">${L24.map((l, h) =>
                    `<button type="button" data-h="${h}" class="${NIGHT.has(h) ? "n" : ""} ${modalCtx.hours.has(h) ? "on" : ""}">${l}</button>`).join("")}</div>
                    <div class="presets">${[["all", "Усі 24"], ["day", "День (7–22)"], ["night", "Ніч (23–6)"], ["morning", "Ранок (6–9)"], ["evening", "Вечір (18–22)"], ["clear", "Очистити"]]
                        .map(([k, l]) => `<button type="button" data-p="${k}">${l}</button>`).join("")}</div>`;
            case "html":
                return f.html;
            default:
                return `<label class="f-l" for="${id}">${f.label}</label><input class="f-i" id="${id}" type="${f.type || "text"}" value="${esc(f.value ?? "")}"
                    ${f.step ? `step="${f.step}"` : ""} ${f.min !== undefined ? `min="${f.min}"` : ""} ${f.max !== undefined ? `max="${f.max}"` : ""}>${hint}`;
        }
    }).join("");
    $("overlay").classList.add("show");
    const first = $("mBody").querySelector("input[type=text],input[type=number]");
    if (first) first.focus();
}
function closeModal() { $("overlay").classList.remove("show"); modalCtx = null; }
$("mBody").addEventListener("click", e => {
    const hb = e.target.closest("[data-h]"), pb = e.target.closest("[data-p]");
    if (hb && modalCtx) { const h = +hb.dataset.h; modalCtx.hours.has(h) ? modalCtx.hours.delete(h) : modalCtx.hours.add(h); hb.classList.toggle("on"); }
    if (pb && modalCtx) {
        const P = { all: [...Array(24).keys()], day: [7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22], night: [23, 0, 1, 2, 3, 4, 5, 6], morning: [6, 7, 8, 9], evening: [18, 19, 20, 21, 22], clear: [] };
        modalCtx.hours = new Set(P[pb.dataset.p]);
        $("mBody").querySelectorAll("[data-h]").forEach(b => b.classList.toggle("on", modalCtx.hours.has(+b.dataset.h)));
    }
});
$("overlay").addEventListener("mousedown", e => { if (e.target === $("overlay")) closeModal(); });
document.addEventListener("keydown", e => { if (e.key === "Escape") closeModal(); if (e.key === "Enter" && modalCtx && e.target.tagName === "INPUT" && e.target.type !== "checkbox") $("mSave").click(); });
$("mSave").addEventListener("click", async () => {
    if (!modalCtx) return;
    const v = {};
    modalCtx.fields.forEach(f => {
        if (f.type === "html") return;
        if (f.type === "hours") v[f.key] = [...modalCtx.hours].sort((a, b) => a - b);
        else if (f.type === "check") v[f.key] = $("f_" + f.key).checked;
        else v[f.key] = $("f_" + f.key).value;
    });
    try {
        const res = await modalCtx.onSave(v);
        if (typeof res === "string") { toast("⚠️ " + res); return; }
        closeModal();
    } catch (err) {
        toast("⚠️ Помилка: " + err.message);
    }
});

// ============================================================
// 3. ФОРМИ ТА РОБОТА З БАЗОЮ ДАНИХ (/api/houses)
// ============================================================
function openProfileForm() {
    const uname = state.auth?.user?.username ? `Логін: <b>${esc(state.auth.user.username)}</b>` : "";
    openForm({
        title: "👤 Особистий профіль",
        fields: [
            { type: "html", key: "uinfo", html: uname ? `<div class="muted" style="margin:4px 0 10px">${uname}</div>` : "" },
            { key: "name", label: "Ваше ім'я", value: state.profile.name },
            { type: "html", key: "logout", html: `<div style="margin-top:14px"><button type="button" class="btn-r" style="width:100%" onclick="closeModal();logoutUser()">🚪 Вийти з акаунта</button></div>` },
        ],
        onSave: async v => {
            const newName = v.name.trim();
            if (!newName) return "Введіть ім'я";
            if (state.auth && state.auth.token) {
                const updated = await apiFetch("/api/auth/me", {
                    method: "PUT",
                    body: JSON.stringify({ name: newName }),
                });
                state.auth.user = updated;
            }
            state.profile.name = newName;
            save();
            renderShell();
            toast("Профіль оновлено");
        },
    });
}
function openTariffForm() {
    const h = activeHouse();
    openForm({ title: "🕒 Тарифи на електроенергію", fields: [
        { key: "day", label: "Денний тариф (07:00–23:00), грн/кВт·год", type: "number", step: "0.01", min: 0, value: h.dayRate },
        { key: "night", label: "Нічний тариф (23:00–07:00), грн/кВт·год", type: "number", step: "0.01", min: 0, value: h.nightRate } ],
        onSave: async v => {
            const d = num(v.day), n = num(v.night);
            if (!(d >= 0) || !(n >= 0)) return "Введіть коректні тарифи";
            const updated = await apiFetch(`/api/houses/${h.id}`, {
                method: "PUT",
                body: JSON.stringify({ day_rate: d, night_rate: n }),
            });
            Object.assign(h, updated);
            addEvent("info", "🕒 Змінено тарифи", `День ${d}, ніч ${n} грн`);
            await refresh();
            toast("Тарифи збережено в БД");
        } });
}
function openLimitForm() {
    const h = activeHouse();
    openForm({ title: "⚡ Ліміт автомата", fields: [
        { key: "volt", label: "Напруга, В", type: "number", min: 100, max: 440, value: h.voltage },
        { key: "amps", label: "Автомат, А", type: "number", min: 1, max: 200, value: h.amps, hint: "Типові: 16 А = 3680 Вт, 25 А = 5750 Вт, 32 А = 7360 Вт" } ],
        onSave: async v => {
            const a = num(v.volt), b = num(v.amps);
            if (!(a > 0) || !(b > 0)) return "Введіть коректні значення";
            const updated = await apiFetch(`/api/houses/${h.id}`, {
                method: "PUT",
                body: JSON.stringify({ voltage: a, amps: b, max_power_watt: a * b }),
            });
            Object.assign(h, updated);
            addEvent("info", "⚡ Змінено ліміт", `${a * b} Вт`);
            await refresh();
            toast("Ліміт збережено в БД");
        } });
}
function openChartForm() {
    const s = state.settings;
    openForm({ title: "⏱️ Параметри графіків і сповіщень", fields: [
        { key: "showLimit", type: "check", label: "Показувати лінію ліміту автомата", value: s.showLimit },
        { key: "alerts", type: "check", label: "Сповіщення про перевантаження", value: s.alerts },
        { key: "warn", label: "Попередження при навантаженні, % від ліміту", type: "number", min: 10, max: 100, value: s.warnPercent },
        { key: "ymax", label: "Максимум осі Y, Вт (0 = авто)", type: "number", min: 0, value: s.chartMax } ],
        onSave: v => {
            s.showLimit = v.showLimit; s.alerts = v.alerts;
            s.warnPercent = Math.min(100, Math.max(10, num(v.warn) || 80));
            s.chartMax = Math.max(0, num(v.ymax) || 0);
            refresh(); toast("Параметри збережено");
        } });
}
function openApiForm() {
    openForm({ title: "🔗 Адреса бекенду", fields: [{ key: "api", label: "URL (порожньо = поточний хост)", value: state.settings.api }],
        onSave: async v => { state.settings.api = v.api.trim().replace(/\/+$/, ""); save(); await initFromDb(); } });
}
function openHouseForm(h) {
    const edit = !!h;
    openForm({ title: edit ? "✏️ Редагувати будинок" : "🏠 Новий будинок", fields: [
        { key: "name", label: "Назва", value: h?.name || "" },
        { key: "address", label: "Адреса", value: h?.address || "" },
        { key: "volt", label: "Напруга, В", type: "number", value: h?.voltage ?? 230 },
        { key: "amps", label: "Автомат, А", type: "number", value: h?.amps ?? 16 },
        { key: "day", label: "Ціна вдень, грн/кВт·год", type: "number", step: "0.01", value: h?.dayRate ?? 4.32 },
        { key: "night", label: "Ціна вночі, грн/кВт·год", type: "number", step: "0.01", value: h?.nightRate ?? 2.16 } ],
        onSave: async v => {
            if (!v.name.trim()) return "Введіть назву";
            const payload = {
                name: v.name.trim(),
                address: v.address.trim(),
                voltage: num(v.volt) || 230,
                amps: num(v.amps) || 16,
                day_rate: num(v.day) >= 0 ? num(v.day) : 4.32,
                night_rate: num(v.night) >= 0 ? num(v.night) : 2.16,
            };
            if (edit) {
                const updated = await apiFetch(`/api/houses/${h.id}`, { method: "PUT", body: JSON.stringify(payload) });
                Object.assign(h, updated);
            } else {
                const created = await apiFetch("/api/houses", { method: "POST", body: JSON.stringify(payload) });
                state.houses.push(created);
                state.activeHouse = created.id;
                addEvent("ok", "🏠 Додано будинок", created.name);
            }
            await refresh();
            toast("Збережено в БД");
        } });
}
function openHouseSwitch() {
    openForm({ title: "🏠 Обрати будинок", fields: [{ key: "id", type: "select", label: "Будинок", value: state.activeHouse, options: state.houses.map(h => ({ v: h.id, l: `${h.name} — ${h.address}` })) }],
        onSave: v => { state.activeHouse = +v.id; refresh(); } });
}
function openApplianceForm(idx, preCat) {
    if (!catalog.length) { toast("⚠️ Каталог не завантажено — перевірте бекенд"); }
    const h = activeHouse(), a = idx !== undefined ? h.appliances[idx] : null;
    openForm({ title: a ? "✏️ Редагувати прилад" : "➕ Додати прилад", fields: [
        { key: "cat", type: "select", label: "Прилад з каталогу", value: a?.catalogId ?? preCat ?? catalog[0]?.id, options: catalog.map(c => ({ v: c.id, l: `${c.name} (${c.power_watt} Вт)` })) },
        { key: "name", label: "Своя назва (необов'язково)", value: a && a.custom ? a.name : "" },
        { key: "power", label: "Своя потужність, Вт (порожньо = з каталогу)", type: "number", value: a && a.customPower ? a.power : "" },
        { key: "qty", label: "Кількість", type: "number", min: 1, max: 50, value: a?.qty ?? 1 },
        { key: "schedule", type: "hours", label: "Години роботи", value: a?.schedule || [] } ],
        saveText: a ? "Зберегти" : "Додати",
        onSave: async v => {
            const c = catalog.find(x => x.id === +v.cat);
            if (!c) return "Оберіть прилад із каталогу";
            if (!v.schedule.length) return "Оберіть хоча б одну годину";
            const customPowerVal = num(v.power) > 0 ? num(v.power) : null;
            const customNameVal = v.name.trim() || null;
            const qty = Math.max(1, parseInt(v.qty) || 1);

            if (a) {
                const updated = await apiFetch(`/api/houses/${h.id}/appliances/${a.id}`, {
                    method: "PUT",
                    body: JSON.stringify({
                        catalog_id: c.id,
                        custom_name: customNameVal,
                        clear_custom_name: !customNameVal,
                        custom_power: customPowerVal,
                        clear_custom_power: !customPowerVal,
                        quantity: qty,
                        schedule: v.schedule,
                        enabled: a.enabled,
                    }),
                });
                h.appliances[idx] = updated;
            } else {
                const created = await apiFetch(`/api/houses/${h.id}/appliances`, {
                    method: "POST",
                    body: JSON.stringify({
                        catalog_id: c.id,
                        custom_name: customNameVal,
                        custom_power: customPowerVal,
                        quantity: qty,
                        schedule: v.schedule,
                        enabled: true,
                    }),
                });
                h.appliances.push(created);
                addEvent("ok", "➕ Додано прилад", `${created.name}, ${created.power} Вт × ${created.qty}`);
            }
            await refresh();
            toast(a ? "Прилад оновлено в БД" : "Прилад додано в БД");
        } });
}
function openEvents() {
    openForm({ title: "🔔 Події", hideSave: true, fields: [{ type: "html", key: "ev", html: eventsHtml() }] });
}
async function resetAll() {
    if (!confirm("Скинути всі дані у базі даних та браузері до початкових?")) return;
    try {
        const houses = await apiFetch("/api/reset", { method: "POST" });
        const savedAuth = state.auth;
        const savedProfile = state.profile;
        localStorage.removeItem(KEY);
        state = defaultState();
        state.auth = savedAuth;
        state.profile = savedProfile;
        state.houses = houses;
        state.activeHouse = houses[0]?.id || 1;
        await refresh();
        toast("Дані у БД скинуто");
    } catch (e) {
        toast("⚠️ Не вдалося скинути БД: " + e.message);
    }
}

// Дії над приладами / будинками (синхронізовані з БД)
async function toggleAppliance(i) {
    const h = activeHouse(), a = h.appliances[i];
    a.enabled = !a.enabled;
    try {
        const updated = await apiFetch(`/api/houses/${h.id}/appliances/${a.id}`, {
            method: "PUT",
            body: JSON.stringify({ enabled: a.enabled }),
        });
        h.appliances[i] = updated;
    } catch (e) {
        toast("⚠️ " + e.message);
    }
    await refresh();
}
async function deleteAppliance(i) {
    const h = activeHouse(), a = h.appliances[i];
    if (!confirm(`Видалити «${a.name}»?`)) return;
    try {
        await apiFetch(`/api/houses/${h.id}/appliances/${a.id}`, { method: "DELETE" });
        h.appliances.splice(i, 1);
        addEvent("neutral", "🗑️ Видалено прилад", a.name);
        await refresh();
    } catch (e) {
        toast("⚠️ " + e.message);
    }
}
function selectHouse(id) { state.activeHouse = id; refresh(); toast("Будинок обрано"); }
async function deleteHouse(id) {
    if (state.houses.length < 2) { toast("⚠️ Має лишитись хоча б один будинок"); return; }
    const h = state.houses.find(x => x.id === id);
    if (!confirm(`Видалити будинок «${h.name}»?`)) return;
    try {
        await apiFetch(`/api/houses/${id}`, { method: "DELETE" });
        state.houses = state.houses.filter(x => x.id !== id);
        if (state.activeHouse === id) state.activeHouse = state.houses[0].id;
        await refresh();
    } catch (e) {
        toast("⚠️ " + e.message);
    }
}

// ============================================================
// 4. АВТОРИЗАЦІЯ, БЕКЕНД ТА ІНІЦІАЛІЗАЦІЯ З БД
// ============================================================
let authMode = "login";

function switchAuthTab(mode) {
    authMode = mode;
    $("authTabLogin").classList.toggle("active", mode === "login");
    $("authTabReg").classList.toggle("active", mode === "register");
    $("authNameWrap").style.display = mode === "register" ? "block" : "none";
    $("authSubmitBtn").textContent = mode === "register" ? "Створити акаунт" : "Увійти";
    $("authErr").style.display = "none";
}

function showAuthScreen() {
    $("authErr").style.display = "none";
    $("authScreen").classList.add("show");
}

function hideAuthScreen() {
    $("authScreen").classList.remove("show");
}

async function submitAuth(e) {
    e.preventDefault();
    $("authErr").style.display = "none";
    const username = $("authUser").value.trim();
    const password = $("authPass").value;
    const name = $("authName").value.trim();

    if (authMode === "register" && !name) {
        $("authErr").textContent = "Введіть ваше ім'я";
        $("authErr").style.display = "block";
        return;
    }

    try {
        const endpoint = authMode === "register" ? "/api/auth/register" : "/api/auth/login";
        const body = authMode === "register" ? { name, username, password } : { username, password };
        const res = await apiFetch(endpoint, { method: "POST", body: JSON.stringify(body) });
        state.auth = { token: res.token, user: res.user };
        state.profile.name = res.user.name;
        state.events = [];
        lastOverKey = null;
        save();
        hideAuthScreen();
        await initFromDb();
        toast(`Вітаємо, ${res.user.name}!`);
    } catch (err) {
        $("authErr").textContent = err.message;
        $("authErr").style.display = "block";
    }
}

async function quickDemoLogin() {
    $("authErr").style.display = "none";
    const id = Date.now().toString(36).slice(-5);
    const username = `guest_${id}`;
    const password = `demo_${id}`;
    const name = `Гість ${id.toUpperCase()}`;
    try {
        const res = await apiFetch("/api/auth/register", {
            method: "POST",
            body: JSON.stringify({ name, username, password }),
        });
        state.auth = { token: res.token, user: res.user };
        state.profile.name = res.user.name;
        state.events = [];
        lastOverKey = null;
        save();
        hideAuthScreen();
        await initFromDb();
        toast(`Створено окремий демо-кабінет: ${res.user.name}`);
    } catch (err) {
        $("authErr").textContent = err.message;
        $("authErr").style.display = "block";
    }
}

function logoutUser() {
    state.auth = null;
    state.events = [];
    lastOverKey = null;
    sim = null;
    save();
    showAuthScreen();
    toast("Ви вийшли з акаунта");
}

async function initFromDb() {
    if (!state.auth || !state.auth.token) {
        showAuthScreen();
        return;
    }
    try {
        const [meData, catData, housesData] = await Promise.all([
            apiFetch("/api/auth/me"),
            apiFetch("/api/catalog"),
            apiFetch("/api/houses"),
        ]);
        state.auth.user = meData;
        state.profile.name = meData.name;
        catalog = catData;
        if (Array.isArray(housesData) && housesData.length > 0) {
            state.houses = housesData;
            if (!state.houses.some(h => h.id === state.activeHouse)) {
                state.activeHouse = state.houses[0].id;
            }
        }
        hideAuthScreen();
        $("apiErr").style.display = "none";
        await refresh();
    } catch (e) {
        if (!state.auth || !state.auth.token) return;
        $("apiErr").style.display = "block";
    }
}

async function runSim() {
    const h = activeHouse(), on = h.appliances.filter(a => a.enabled);
    const body = {
        appliances: on.map(a => ({
            name: a.name,
            power_watt: a.power,
            schedule: a.schedule,
            quantity: a.qty,
            is_cyclic: a.cyclic,
            duty_cycle: a.duty,
        })),
        max_power_watt: limitOf(h),
        day_rate: h.dayRate,
        night_rate: h.nightRate,
        outage_hours: h.outageHours || h.outage_hours || [],
    };
    try {
        sim = await apiFetch("/api/simulate", { method: "POST", body: JSON.stringify(body) });
        $("apiErr").style.display = "none";
    } catch (e) {
        sim = null;
        $("apiErr").style.display = "block";
    }
}

let simToken = 0;
async function refresh() {
    save(); renderShell();
    const t = ++simToken; await runSim(); if (t !== simToken) return;
    if (sim) {
        const key = activeHouse().id + ":" + sim.overload_hours.map(o => o.hour).join(",");
        if (key !== lastOverKey) {
            if (lastOverKey !== null && sim.overload_hours.length) {
                addEvent("warn", "⚠️ Перевищення ліміту", `Ліміт ${limitOf(activeHouse())} Вт, години: ${sim.overload_hours.map(o => o.hour + ":00").join(", ")}`);
            }
            lastOverKey = key; save();
        }
    }
    renderData();
}

// ============================================================
// 5. РЕНДЕР
// ============================================================
function eventsHtml() {
    if (!state.events.length) return `<div class="empty">Подій поки немає</div>`;
    return state.events.map(e => `<div class="ev"><div class="ev-i ${e.type}">${e.title.split(" ")[0]}</div><div class="grow"><div class="ev-t">${esc(e.title.split(" ").slice(1).join(" "))}</div><div class="ev-d">${esc(e.desc)}</div></div><span class="ev-time">${e.time}</span></div>`).join("");
}
function renderShell() {
    const h = activeHouse(), q = $("searchInput").value.trim().toLowerCase();
    $("profileName").textContent = state.profile.name; $("avatar").textContent = (state.profile.name[0] || "?").toUpperCase();
    $("heroName").textContent = h.name; $("heroAddr").textContent = "📍 " + (h.address || "адресу не вказано");
    $("hsName").textContent = h.address || h.name;
    $("rsTariff").textContent = `День: ${h.dayRate} / Ніч: ${h.nightRate} грн`; $("rsLimit").textContent = `${limitOf(h)} Вт (${h.voltage} В × ${h.amps} А)`;
    $("sProfile").textContent = `Ім'я: ${state.profile.name}` + (state.auth?.user?.username ? ` • Логін: ${state.auth.user.username}` : "");
    $("sTariff").textContent = `Будинок «${h.name}»: день ${h.dayRate} грн, ніч ${h.nightRate} грн за кВт·год`;
    $("sLimitTxt").textContent = `${h.voltage} В × ${h.amps} А = ${limitOf(h)} Вт`;
    const s = state.settings;
    $("sChart").textContent = `Лінія ліміту: ${s.showLimit ? "так" : "ні"} • Сповіщення: ${s.alerts ? "так" : "ні"} • Попередження: ${s.warnPercent}% • Макс. осі Y: ${s.chartMax || "авто"}`;
    $("sApi").textContent = s.api || "(поточний сервер / відносні URL)";
    $("appTitle").textContent = `Прилади — ${h.name}`;

    const row = (a, i) => `<div class="rs ${a.enabled ? "" : "off"}"><div class="ic ${a.category}">${iconOf(a)}</div><div class="grow"><div class="bold">${esc(a.name)}</div>
        <small class="muted" style="margin:0">${a.power} Вт • ${a.qty} шт.</small><br><small style="color:var(--b)">${fmt(dailyKwh(a), 1)} кВт·год/день</small></div>
        <label class="tg"><input type="checkbox" ${a.enabled ? "checked" : ""} onchange="toggleAppliance(${i})"><span></span></label></div>`;
    $("rsList").innerHTML = h.appliances.length ? h.appliances.map(row).join("") : `<div class="empty">Немає приладів</div>`;

    $("appGrid").innerHTML = h.appliances.length ? h.appliances.map((a, i) => `<div class="item ${a.enabled ? "" : "off"}"><div class="ic ${a.category}">${iconOf(a)}</div>
        <div class="grow"><div class="it-n">${esc(a.name)}</div><div class="it-d">${a.power} Вт • ${a.qty} шт.${a.cyclic ? " • цикл " + Math.round(a.duty * 100) + "%" : ""}</div>
        <div class="it-d">🕐 ${scheduleText(a.schedule)}</div><div class="it-k">${fmt(dailyKwh(a), 2)} кВт·год/день</div></div>
        <label class="tg"><input type="checkbox" ${a.enabled ? "checked" : ""} onchange="toggleAppliance(${i})"><span></span></label>
        <button class="mini" onclick="openApplianceForm(${i})">✏️</button><button class="mini del" onclick="deleteAppliance(${i})">🗑️</button></div>`).join("")
        : `<div class="empty">Приладів ще немає. Натисніть «+ Додати прилад».</div>`;

    const hs = state.houses.filter(x => !q || (x.name + " " + x.address).toLowerCase().includes(q));
    $("housesList").innerHTML = hs.length ? hs.map(x => `<div class="item ${x.id === h.id ? "active-house" : ""}"><div class="ic">🏠</div>
        <div class="grow"><div class="it-n">${esc(x.name)} ${x.id === h.id ? '<small class="green">● обрано</small>' : ""}</div><div class="it-d">📍 ${esc(x.address || "—")}</div>
        <div class="it-d">${x.appliances.length} приладів • ліміт ${limitOf(x)} Вт</div><div class="it-d">Тарифи: ${x.dayRate} / ${x.nightRate} грн</div><div class="it-k">≈ ${fmt(estimate(x).kwh, 1)} кВт·год • ${fmt(estimate(x).cost, 1)} грн / доба</div></div>
        ${x.id === h.id ? "" : `<button class="mini" onclick="selectHouse(${x.id})">Обрати</button>`}
        <button class="mini" onclick="openHouseForm(state.houses.find(z=>z.id===${x.id}))">✏️</button><button class="mini del" onclick="deleteHouse(${x.id})">🗑️</button></div>`).join("")
        : `<div class="empty">Нічого не знайдено</div>`;
    $("eventsList").innerHTML = eventsHtml();
    renderShellExtras(h);
}

function renderData() {
    const h = activeHouse(), s = state.settings, L = limitOf(h);
    if (!sim) { drawAll(); return; }
    let dk = 0, nk = 0; sim.hourly_kwh.forEach((k, i) => NIGHT.has(i) ? nk += k : dk += k);
    $("kKwh").textContent = fmt(sim.total_kwh); $("kCost").textContent = fmt(sim.total_cost); $("kSplit").textContent = `${fmt(dk)} / ${fmt(nk)}`;
    const pct = L ? sim.peak_watt / L * 100 : 0, over = sim.overload_hours.length > 0, warn = !over && pct >= s.warnPercent;
    $("gBar").style.width = Math.min(100, pct) + "%"; $("gBar").className = "bar-fill " + (over ? "bad" : warn ? "warn" : "");
    $("gBadge").className = "pill " + (over ? "bad" : warn ? "warn" : "ok"); $("gBadge").textContent = over ? "🔴 Перевантаження" : warn ? "🟠 Близько до ліміту" : "🟢 Нормальне";
    $("gText").textContent = `Пік ${fmt(sim.peak_watt, 0)} Вт о ${sim.peak_hour}:00 / ліміт ${L} Вт (${fmt(pct, 0)}%)`;
    $("overBanner").style.display = over && s.alerts ? "block" : "none";
    $("overText").textContent = `Автомат вибиває. Ліміт ${L} Вт, перевищення у годинах: ${sim.overload_hours.map(o => o.hour + ":00").join(", ")}.`;
    $("okBanner").style.display = !over ? "block" : "none"; $("okBanner").textContent = `✅ Навантаження в нормі. Пік ${fmt(sim.peak_watt, 0)} Вт (ліміт ${L} Вт).`;
    const b = $("bellBadge"); b.textContent = s.alerts ? sim.overload_hours.length : 0; b.classList.toggle("zero", !(s.alerts && sim.overload_hours.length));
    $("tKwh").textContent = fmt(sim.total_kwh, 1); $("tCost").textContent = fmt(sim.total_cost, 1); $("tPeak").textContent = fmt(sim.peak_watt, 0);
    $("tCount").textContent = h.appliances.filter(a => a.enabled).length + "/" + h.appliances.length;
    $("eventsList").innerHTML = eventsHtml();
    renderAnalytics(h, dk, nk);
    renderMeterAndSolar();
    renderOutagePanel(h);
    renderExtras(h, dk, nk);
    drawAll();
    setupScrollMotion();
    previewOptimization(false);
}

function renderAnalytics(h, dk, nk) {
    const mk = sim.total_kwh * 30, mc = sim.total_cost * 30;
    $("aMonthKwh").textContent = fmt(mk, 0); $("aMonthCost").textContent = fmt(mc, 0);
    const rows = sim.per_appliance.map(p => {
        let kwh = 0, cost = 0;
        p.hourly_watt.forEach((w, i) => { const k = w / 1000; kwh += k; cost += k * (NIGHT.has(i) ? h.nightRate : h.dayRate); });
        return { name: p.name, power: p.power_watt, kwh, cost };
    }).sort((a, b) => b.kwh - a.kwh);
    let saveCost = 0;
    sim.per_appliance.forEach(p => { if (p.power_watt >= 1000) p.hourly_watt.forEach((w, i) => { if (!NIGHT.has(i)) saveCost += w / 1000 * (h.dayRate - h.nightRate); }); });
    $("aSave").textContent = fmt(Math.max(0, saveCost * 30), 0) + " грн/міс";
    $("aTable").innerHTML = rows.length ? `<table><tr><th>Прилад</th><th>кВт·год</th><th>грн/доба</th><th>грн/міс</th></tr>${rows.map(r =>
        `<tr><td>${esc(r.name)}</td><td>${fmt(r.kwh)}</td><td>${fmt(r.cost)}</td><td>${fmt(r.cost * 30, 0)}</td></tr>`).join("")}</table>` : `<div class="empty">Немає даних</div>`;
    const tips = [];
    if (sim.overload_hours.length) tips.push(`⚠️ Автомат вибиває у ${sim.overload_hours.map(o => o.hour + ":00").join(", ")}. Розведіть потужні прилади по різних годинах або збільште номінал автомата.`);
    if (rows[0] && sim.total_kwh) tips.push(`🔥 Найбільше споживає «${rows[0].name}» — ${fmt(rows[0].kwh / sim.total_kwh * 100, 0)}% добового споживання.`);
    if (sim.total_kwh && dk / sim.total_kwh > 0.7) tips.push(`🌙 ${fmt(dk / sim.total_kwh * 100, 0)}% енергії споживається вдень. Перенесення потужних приладів (бойлер, пральна машина) на ніч заощадить ≈ ${fmt(saveCost * 30, 0)} грн/міс.`);
    if (h.dayRate && h.nightRate && h.nightRate < h.dayRate) tips.push(`💰 Нічний тариф дешевший у ${fmt(h.dayRate / h.nightRate, 1)} раза.`);
    if (!tips.length) tips.push("✅ Все добре! Додайте більше приладів, щоб побачити поради.");
    $("aTips").innerHTML = tips.map(t => `<div class="tip">${t}</div>`).join("");
    window._rows = rows;
}

// ============================================================
// 6. ГРАФІКИ
// ============================================================
const charts = {};
function mkChart(id, cfg) {
    if (charts[id]) { charts[id].destroy(); delete charts[id]; }
    const c = $(id); if (!c || !c.offsetParent) return;
    charts[id] = new Chart(c, cfg);
}
const axes = (ytitle, max) => ({
    y: { beginAtZero: true, max: max || undefined, grid: { color: "rgba(255,255,255,.05)" }, ticks: { color: "#64748B", font: { size: 10 } }, title: { display: true, text: ytitle, color: "#64748B" } },
    x: { grid: { display: false }, ticks: { color: "#64748B", font: { size: 9 }, maxRotation: 55 } } });
function hourColors() {
    const over = new Set(sim.overload_hours.map(o => o.hour));
    return L24.map((_, i) => over.has(i) ? "#EF4444" : NIGHT.has(i) ? "#818CF8" : "#FACC15");
}
function limitAnn(L) {
    if (!state.settings.showLimit) return {};
    return { limit: { type: "line", yMin: L, yMax: L, borderColor: "#EF4444", borderWidth: 2, borderDash: [6, 4],
        label: { display: true, content: `Ліміт ${L} Вт`, position: "start", backgroundColor: "rgba(239,68,68,.85)", color: "#fff", font: { size: 10 }, padding: 3 } } };
}
function legendHtml() {
    return `<span><i style="background:#FACC15"></i>День</span><span><i style="background:#818CF8"></i>Ніч</span><span><i style="background:#EF4444"></i>Перевантаження</span>` +
        (state.settings.showLimit ? `<span><i class="ln"></i>Ліміт автомата</span>` : "");
}
function drawAll() {
    try { drawBase(); } finally { drawExtra(); }
}
function drawBase() {
    document.querySelectorAll(".legend").forEach(e => e.innerHTML = legendHtml());
    const h = activeHouse();
    document.querySelectorAll(".legend2").forEach(e => e.innerHTML = `<span><i style="background:#22C55E"></i>День (${h.dayRate} грн)</span><span><i style="background:#818CF8"></i>Ніч (${h.nightRate} грн)</span>`);
    if (!sim) return;
    const L = limitOf(h), ymax = state.settings.chartMax;
    const outSet = new Set(sim.outage_hours || h.outageHours || []);
    const tip = { callbacks: { label: c => `${fmt(c.parsed.y, 0)} Вт`, afterLabel: c => outSet.has(c.dataIndex) ? "🕯️ Відключення світла (ГПВ)" : (NIGHT.has(c.dataIndex) ? "🌙 Ніч" : "☀️ День") } };
    const hourlyCfg = () => ({ type: "bar", data: { labels: L24, datasets: [{ data: sim.hourly_watt, backgroundColor: hourColors(), borderRadius: 3, barPercentage: .75 }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, annotation: { annotations: limitAnn(L) }, tooltip: tip }, scales: axes("Вт", ymax) } });
    mkChart("chartHome", hourlyCfg());

    const n = range === "week" ? 7 : 30;
    $("rangeNote").textContent = range === "day" ? "Споживання по годинах за добу." : `Прогноз за поточним розкладом: кожна доба = ${fmt(sim.total_kwh)} кВт·год.`;
    if (range === "day") mkChart("chartWatt", hourlyCfg());
    else mkChart("chartWatt", { type: "bar", data: { labels: Array.from({ length: n }, (_, i) => `${i + 1}`), datasets: [{ data: Array(n).fill(sim.total_kwh), backgroundColor: "#FACC15", borderRadius: 3 }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => `${fmt(c.parsed.y)} кВт·год` } } }, scales: axes("кВт·год") } });

    mkChart("chartCost", { type: "bar", data: { labels: L24, datasets: [{ data: sim.hourly_cost, backgroundColor: L24.map((_, i) => NIGHT.has(i) ? "#818CF8" : "#22C55E"), borderRadius: 3, barPercentage: .75 }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => `${fmt(c.parsed.y, 3)} грн` } } }, scales: axes("грн") } });

    mkChart("chartStack", { type: "bar", data: { labels: L24, datasets: sim.per_appliance.map((p, i) => ({ label: p.name, data: p.hourly_watt, backgroundColor: PALETTE[i % PALETTE.length], borderRadius: 2 })) },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { labels: { color: "#94A3B8", boxWidth: 10, font: { size: 10 } } }, annotation: { annotations: limitAnn(L) } },
            scales: { x: { ...axes("").x, stacked: true }, y: { ...axes("Вт", ymax).y, stacked: true } } } });

    const rows = window._rows || [];
    mkChart("chartShare", { type: "doughnut", data: { labels: rows.map(r => r.name), datasets: [{ data: rows.map(r => +r.kwh.toFixed(3)), backgroundColor: rows.map((_, i) => PALETTE[i % PALETTE.length]), borderColor: "#162238", borderWidth: 2 }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "right", labels: { color: "#94A3B8", boxWidth: 10, font: { size: 11 } } }, tooltip: { callbacks: { label: c => `${c.label}: ${fmt(c.parsed)} кВт·год` } } } } });
}

// ============================================================
// 7. НАВІГАЦІЯ
// ============================================================
function setView(v) {
    if (!VIEWS[v]) v = "home";
    document.body.dataset.page = v;
    document.querySelectorAll(".view").forEach(e => e.classList.toggle("active", e.id === "view-" + v));
    document.querySelectorAll(".nav-item").forEach(e => e.classList.toggle("active", e.dataset.view === v));
    $("pageTitle").textContent = VIEWS[v][0]; $("pageSub").textContent = VIEWS[v][1];
    if (location.hash !== "#" + v) history.replaceState(null, "", "#" + v);
    const sc = document.querySelector(".center");
    if (sc) sc.scrollTo(0, 0);
    drawAll();
    setupScrollMotion();
}

// ============================================================
// ДОДАТКОВІ БЛОКИ (приладів, будинків, аналітики, експорт)
// ============================================================
function estimate(house) {
    let kwh = 0, cost = 0;
    house.appliances.filter(a => a.enabled).forEach(a => a.schedule.forEach(hr => {
        const k = a.power * a.duty * a.qty / 1000; kwh += k; cost += k * (NIGHT.has(hr) ? house.nightRate : house.dayRate);
    }));
    return { kwh, cost };
}
function hourlyLoad(house) {
    const w = Array(24).fill(0);
    house.appliances.filter(a => a.enabled).forEach(a => a.schedule.forEach(hr => { w[hr] += a.power * a.duty * a.qty; }));
    return w;
}
function renderShellExtras(h) {
    const on = h.appliances.filter(a => a.enabled);
    const inst = h.appliances.reduce((s, a) => s + a.power * a.qty, 0), e = estimate(h);
    $("appSum").innerHTML = `
        <div class="kpi"><div class="kpi-ic y">🔌</div><div class="kpi-v">${on.length}/${h.appliances.length}</div><div class="kpi-l">приладів увімкнено</div></div>
        <div class="kpi"><div class="kpi-ic b">⚡</div><div class="kpi-v">${fmt(inst / 1000, 1)} кВт</div><div class="kpi-l">встановлена потужність</div></div>
        <div class="kpi"><div class="kpi-ic p">📊</div><div class="kpi-v">${fmt(e.kwh, 1)}</div><div class="kpi-l">кВт·год за добу (оцінка)</div></div>`;
    const load = hourlyLoad(h), mx = Math.max(1, ...load);
    const outSet = new Set(h.outageHours || h.outage_hours || []);
    $("timeline").innerHTML = h.appliances.length
        ? `<div class="tl-h"><span></span><div>${L24.map((l, i) => `<span>${i % 3 === 0 ? i : ""}</span>`).join("")}</div></div>` +
          h.appliances.map((a, appIdx) => `<div class="tl-r" style="${a.enabled ? "" : "opacity:.4"}"><span class="tl-n">${iconOf(a)} ${esc(a.name)}</span><div class="tl-c">${L24.map((l, i) => {
              const on = a.schedule.includes(i);
              const cls = (on ? (NIGHT.has(i) ? "n" : "d") : "") + (outSet.has(i) ? " out" : "");
              return `<i data-tl-app="${appIdx}" data-tl-hr="${i}" class="${cls.trim()}" title="${esc(a.name)} — ${l} (${on ? "увімкнено" : "вимкнено"}${outSet.has(i) ? ", ГПВ" : ""}). Клікніть для перемикання"></i>`;
          }).join("")}</div></div>`).join("") +
          `<div class="tl-r"><span class="tl-n"><b>Σ Потужність</b></span><div class="tl-c">${load.map((w, i) =>
              `<i title="${L24[i]}: ${outSet.has(i) ? "0 Вт (відключення ГПВ)" : fmt(w, 0) + " Вт"}" style="background:${outSet.has(i) ? "rgba(239,68,68,.2)" : "rgba(239,68,68," + (0.12 + 0.88 * w / mx).toFixed(2) + ")"}"></i>`).join("")}</div></div>`
        : `<div class="empty">Додайте прилади, щоб побачити розклад</div>`;
    $("gallery").innerHTML = catalog.length ? catalog.map(c => `<button onclick="openApplianceForm(undefined, ${c.id})"><i>${ICONS[c.name] || CAT_ICON[c.category] || "🔌"}</i><b>${esc(c.name)}</b><small>${c.power_watt} Вт${c.is_cyclic ? " • цикл " + Math.round(c.duty_cycle * 100) + "%" : ""}</small></button>`).join("")
        : `<div class="empty">Каталог недоступний (перевірте бекенд)</div>`;
}
function renderExtras(h, dk, nk) {
    const rows = window._rows || [], tot = rows.reduce((s, r) => s + r.kwh, 0) || 1;
    $("topList").innerHTML = rows.length ? rows.slice(0, 5).map((r, i) => `<div class="rowbar"><span>${esc(r.name)}</span><span class="tr"><i style="width:${(r.kwh / tot * 100).toFixed(0)}%;background:${PALETTE[i % PALETTE.length]}"></i></span><span>${fmt(r.kwh, 1)} кВт·год</span></div>`).join("")
        : `<div class="empty">Немає даних</div>`;
    const L = limitOf(h);
    const hrs = L24.map((l, i) => ({ i, rate: NIGHT.has(i) ? h.nightRate : h.dayRate, free: L - sim.hourly_watt[i] }))
        .sort((a, b) => a.rate - b.rate || b.free - a.free).slice(0, 5);
    $("aBest").innerHTML = `<small class="muted">Нічні години з найбільшим запасом потужності — запускайте бойлер, пральну машину, посудомийку.</small>` +
        hrs.map(x => `<div class="rowbar"><span>${L24[x.i]}</span><span class="tr"><i style="width:${Math.max(3, Math.min(100, x.free / L * 100)).toFixed(0)}%;background:#22C55E"></i></span><span>${fmt(x.free, 0)} Вт</span></div>`).join("");
    const kwh = sim.total_kwh, actual = sim.total_cost, allDay = kwh * h.dayRate, allNight = kwh * h.nightRate, mx = Math.max(allDay, actual, allNight, 0.01);
    const tr = [["Фактично", actual, "#FACC15"], ["Все вдень", allDay, "#EF4444"], ["Все вночі", allNight, "#22C55E"]];
    $("aTariff").innerHTML = `<small class="muted">Скільки коштувала б доба за різних режимів споживання.</small>` +
        tr.map(([n, v, col]) => `<div class="rowbar"><span>${n}</span><span class="tr"><i style="width:${(v / mx * 100).toFixed(0)}%;background:${col}"></i></span><span>${fmt(v)} грн</span></div>`).join("") +
        `<small class="muted">Різниця денного й нічного тарифу: ${fmt(allDay - allNight)} грн на добу.</small>`;
    const co2d = kwh * 0.4, co2y = co2d * 365;
    const stat = (v, l) => `<div class="stat"><b>${v}</b><small>${l}</small></div>`;
    $("aEco").innerHTML = stat(fmt(co2d, 1) + " кг", "CO₂ за добу (≈0.4 кг/кВт·год)") + stat(fmt(co2y, 0) + " кг", "CO₂ за рік") + stat(fmt(co2y / 21, 0), "дерев потрібно для поглинання за рік") +
        stat(fmt(actual * 365, 0) + " грн", "витрати за рік (прогноз)") + stat((kwh ? fmt(actual / kwh) : "—") + " грн", "середня ціна кВт·год") + stat(fmt(sim.peak_watt / 1000, 2) + " кВт", "пікове навантаження");
}
function drawExtra() {
    const hs = state.houses, ests = hs.map(estimate);
    mkChart("chartHouses", { type: "bar", data: { labels: hs.map(x => x.name), datasets: [
        { label: "кВт·год/доба", data: ests.map(e => +e.kwh.toFixed(2)), backgroundColor: "#FACC15", borderRadius: 4 },
        { label: "грн/доба", data: ests.map(e => +e.cost.toFixed(2)), backgroundColor: "#38BDF8", borderRadius: 4 } ] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { labels: { color: "#94A3B8", boxWidth: 10 } } }, scales: axes("") } });
    if (!sim) return;
    const h = activeHouse(), L = limitOf(h);
    let dk = 0, nk = 0; sim.hourly_kwh.forEach((k, i) => NIGHT.has(i) ? nk += k : dk += k);
    mkChart("chartDN", { type: "doughnut", data: { labels: ["День", "Ніч"], datasets: [{ data: [+dk.toFixed(3), +nk.toFixed(3)], backgroundColor: ["#FACC15", "#818CF8"], borderColor: "#162238", borderWidth: 2 }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "right", labels: { color: "#94A3B8", boxWidth: 10 } }, tooltip: { callbacks: { label: c => `${c.label}: ${fmt(c.parsed)} кВт·год` } } } } });
    mkChart("chartLine", { type: "line", data: { labels: L24, datasets: [{ data: sim.hourly_watt, borderColor: "#38BDF8", backgroundColor: "rgba(56,189,248,.18)", fill: true, tension: .3, pointRadius: 2, stepped: false }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, annotation: { annotations: limitAnn(L) }, tooltip: { callbacks: { label: c => `${fmt(c.parsed.y, 0)} Вт` } } }, scales: axes("Вт", state.settings.chartMax) } });
    let run = 0; const cum = sim.hourly_cost.map(v => +(run += v).toFixed(3));
    mkChart("chartCum", { type: "line", data: { labels: L24, datasets: [{ data: cum, borderColor: "#22C55E", backgroundColor: "rgba(34,197,94,.18)", fill: true, tension: .25, pointRadius: 2 }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => `${fmt(c.parsed.y)} грн` } } }, scales: axes("грн") } });
}

function exportData() {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify(state, null, 2)], { type: "application/json" }));
    a.download = "energydim-backup.json"; a.click(); URL.revokeObjectURL(a.href); toast("Файл збережено");
}

// ============================================================
// 8. АВТОМАТИЧНИЙ РОЗУМНИЙ ОПТИМІЗАТОР (POST /api/optimize)
// ============================================================
let lastOptimization = null;

function overloadLabel(n) {
    if (n === 0) return "0 перевантажень";
    if (n === 1) return "1 перевантаження";
    if (n >= 2 && n <= 4) return `${n} перевантаження`;
    return `${n} перевантажень`;
}

function shortHours(s) {
    if (!s || !s.length) return "—";
    const x = [...s].sort((a, b) => a - b), r = [];
    let st = x[0], en = x[0];
    for (let i = 1; i < x.length; i++) {
        if (x[i] === en + 1) en = x[i];
        else { r.push(`${String(st).padStart(2, "0")}:00–${String(en + 1).padStart(2, "0")}:00`); st = en = x[i]; }
    }
    r.push(`${String(st).padStart(2, "0")}:00–${String(en + 1).padStart(2, "0")}:00`);
    return r.join(", ");
}

async function fetchOptimization() {
    const h = activeHouse(), on = h.appliances.filter(a => a.enabled);
    if (!on.length) return null;
    const body = {
        appliances: on.map(a => ({
            id: a.id,
            name: a.name,
            power_watt: a.power,
            schedule: a.schedule,
            quantity: a.qty,
            is_cyclic: a.cyclic,
            duty_cycle: a.duty,
        })),
        max_power_watt: limitOf(h),
        day_rate: h.dayRate,
        night_rate: h.nightRate,
        outage_hours: h.outageHours || h.outage_hours || [],
    };
    return await apiFetch("/api/optimize", { method: "POST", body: JSON.stringify(body) });
}

function buildOptSummaryHtml(opt) {
    const b = opt.before, a = opt.after;
    const changed = (opt.changes || []).filter(c => c.changed);
    const headline = `До оптимізації: <span class="opt-before">${fmt(b.total_cost)} грн (${overloadLabel(b.overload_count)})</span> → Після: <span class="opt-after">${fmt(a.total_cost)} грн (${overloadLabel(a.overload_count)}, <span class="opt-save">економія ${fmt(opt.saved_monthly_uah, 0)} грн/міс</span>)</span>`;
    const rowsHtml = changed.length
        ? changed.map(c => `<div class="opt-change-row"><b>${esc(c.name)}</b><span>${shortHours(c.old_schedule)} <span class="opt-arrow">→</span> <span style="color:#86EFAC;font-weight:600">${shortHours(c.new_schedule)}</span></span></div>`).join("")
        : `<div class="opt-change-row"><span style="color:#86EFAC">✅ Розклад уже максимально оптимізовано! Усі гнучкі потужні прилади працюють у нічній зоні (23:00–07:00) без перевантажень.</span></div>`;
    return { headline, rowsHtml, hasChanges: changed.length > 0 };
}

async function previewOptimization(userClicked = false) {
    try {
        const opt = await fetchOptimization();
        if (!opt) return;
        lastOptimization = opt;
        const { headline, rowsHtml, hasChanges } = buildOptSummaryHtml(opt);
        $("optHeadline").innerHTML = headline;
        $("optChanges").innerHTML = rowsHtml;
        $("optResult").style.display = "block";
        $("btnApplyOpt").style.display = hasChanges ? "" : "none";
        if (userClicked) {
            if (!$("view-home").classList.contains("active")) setView("home");
            $("optCard").scrollIntoView({ behavior: "smooth", block: "center" });
            if (!hasChanges) toast("✅ Розклад уже оптимальний!");
        }
    } catch (e) {
        if (userClicked) toast("⚠️ Помилка оптимізації: " + e.message);
    }
}

async function openOptimizeModal() {
    try {
        const opt = await fetchOptimization();
        if (!opt) { toast("⚠️ Додайте та увімкніть прилади"); return; }
        lastOptimization = opt;
        const { headline, rowsHtml, hasChanges } = buildOptSummaryHtml(opt);
        openForm({
            title: "✨ Розумний оптимізатор розкладу",
            saveText: "✅ Застосувати",
            hideSave: !hasChanges,
            fields: [{
                type: "html",
                key: "opt",
                html: `<div class="opt-headline" style="margin-top:10px">${headline}</div><div class="opt-changes" style="margin-top:12px">${rowsHtml}</div>`,
            }],
            onSave: async () => { await applyOptimization(); },
        });
    } catch (e) {
        toast("⚠️ " + e.message);
    }
}

async function applyOptimization() {
    const h = activeHouse();
    try {
        const res = await apiFetch(`/api/houses/${h.id}/optimize?apply=true`, { method: "POST" });
        if (res.house) {
            Object.assign(h, res.house);
        } else if (lastOptimization) {
            const byId = new Map(lastOptimization.changes.map(c => [c.id, c.new_schedule]));
            h.appliances.forEach(a => { if (byId.has(a.id)) a.schedule = byId.get(a.id); });
        }
        addEvent("ok", "✨ Розклад оптимізовано", `Після: ${fmt(res.after.total_cost)} грн/доба (економія ${fmt(res.saved_monthly_uah, 0)} грн/міс)`);
        await refresh();
        toast(`✨ Застосовано! Економія ${fmt(res.saved_monthly_uah, 0)} грн/міс`);
    } catch (e) {
        toast("⚠️ Не вдалося застосувати: " + e.message);
    }
}
$("importFile").addEventListener("change", e => {
    const f = e.target.files[0]; if (!f) return;
    const r = new FileReader();
    r.onload = () => { try { const s = JSON.parse(r.result); if (!Array.isArray(s.houses) || !s.houses.length) throw 0; state = { ...defaultState(), ...s }; save(); refresh(); toast("Імпортовано"); } catch (err) { toast("⚠️ Некоректний файл"); } };
    r.readAsText(f); e.target.value = "";
});

// ============================================================
// 9. MOTION DESIGN: SCROLL REVEAL, PARALLAX & CURSOR SPOTLIGHT
// ============================================================
let scrollObserver = null;

function setupScrollMotion() {
    const activeView = document.querySelector(".view.active");
    if (!activeView) return;
    const targets = activeView.querySelectorAll(".hero, .page-hero, .kpi, .card, .item, .quick button, .stat");

    if (scrollObserver) scrollObserver.disconnect();
    const sc = $("scrollContainer") || document.querySelector(".center");

    let batch = 0;
    let batchTimer = null;
    scrollObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                const delay = Math.min(batch * 50, 300);
                batch++;
                clearTimeout(batchTimer);
                batchTimer = setTimeout(() => { batch = 0; }, 120);
                entry.target.style.setProperty("--stagger", `${delay}ms`);
                entry.target.classList.add("in-view");
                scrollObserver.unobserve(entry.target);
            }
        });
    }, { root: null, threshold: 0.02 });

    const vh = window.innerHeight || 900;
    targets.forEach((el, idx) => {
        el.classList.add("reveal-item");
        const rect = el.getBoundingClientRect();
        if (rect.top < vh + 40) {
            // Елементи на першому екрані одразу анімуються каскадом
            el.style.setProperty("--stagger", `${Math.min(idx * 45, 280)}ms`);
            requestAnimationFrame(() => el.classList.add("in-view"));
        } else {
            // Елементи нижче за екраном анімуються під час скролу вниз
            el.classList.remove("in-view");
            scrollObserver.observe(el);
        }
    });
}

// Індикатор прогресу скролу через requestAnimationFrame (без навантаження на GPU)
const scEl = $("scrollContainer") || document.querySelector(".center");
const progEl = $("scrollProgress");
if (scEl && progEl) {
    let ticking = false;
    scEl.addEventListener("scroll", () => {
        if (!ticking) {
            requestAnimationFrame(() => {
                const maxScroll = Math.max(1, scEl.scrollHeight - scEl.clientHeight);
                const pct = Math.min(100, Math.max(0, (scEl.scrollTop / maxScroll) * 100));
                progEl.style.width = `${pct.toFixed(1)}%`;
                ticking = false;
            });
            ticking = true;
        }
    }, { passive: true });
}

// ============================================================
// 10. ГПВ (ВІДКЛЮЧЕННЯ + ECOFLOW), ПРЕСЕТИ, ОКУПНІСТЬ ТА PDF-ЗВІТ
// ============================================================
const OUTAGE_QUEUES = {
    none: [],
    q1: [8, 9, 10, 11, 20, 21, 22, 23],
    q2: [0, 1, 2, 3, 12, 13, 14, 15],
    q3: [4, 5, 6, 7, 16, 17, 18, 19],
};

function renderOutagePanel(h) {
    const outArr = (sim && sim.outage_hours) ? sim.outage_hours : (h.outageHours || h.outage_hours || []);
    const outSet = new Set(outArr);

    $("outageGrid").innerHTML = L24.map((l, hr) =>
        `<button type="button" class="outage-hour ${outSet.has(hr) ? "off" : ""}" onclick="toggleOutageHour(${hr})" title="Година ${l}">${String(hr).padStart(2, "0")}:00</button>`
    ).join("");

    const bk = sim && sim.outage_backup;
    if (!bk || outSet.size === 0) {
        $("outageStats").innerHTML = `<small class="muted" style="margin:6px 0 0">Оберіть години відключення вище або чергу ГПВ, щоб розрахувати необхідну зарядну станцію (EcoFlow / інвертор).</small>`;
        return;
    }

    const affectedHtml = (bk.affected_appliances || []).length
        ? `<div class="opt-changes">${bk.affected_appliances.map(a =>
            `<div class="opt-change-row"><b>${esc(a.name)}</b><span>Без світла: ${shortHours(a.blocked_hours)} • потрібно <b>${fmt(a.needed_wh, 0)} Вт·год</b></span></div>`
        ).join("")}</div>`
        : `<small class="muted" style="margin:8px 0 0">У вибрані години відключень жоден увімкнений прилад не запланований.</small>`;

    $("outageStats").innerHTML = `
        <div class="stats">
            <div class="stat"><b>${bk.outage_hours_count} год</b><small>без світла за добу</small></div>
            <div class="stat"><b style="color:var(--o)">${fmt(bk.unserved_kwh, 2)} кВт·год</b><small>випадає за добу (${fmt(bk.unserved_wh, 0)} Вт·год)</small></div>
            <div class="stat"><b style="color:var(--b)">${bk.recommended_wh} Вт·год</b><small>потрібна ємність (+20% запас)</small></div>
            <div class="stat"><b style="color:var(--y)">${bk.recommended_inverter_watt} Вт</b><small>потрібна потужність інвертора</small></div>
        </div>
        <div class="ecoflow-box">
            <div>
                <small class="muted" style="margin:0">Рекомендована зарядна станція / інвертор</small>
                <div class="ecoflow-model">🔋 ${esc(bk.recommended_model)}</div>
            </div>
            <button class="btn-y sm" onclick="previewOptimization(true)">✨ Перенести прилади з годин відключень</button>
        </div>
        ${affectedHtml}`;
}

async function saveOutageHours(hoursArr) {
    const h = activeHouse();
    const cleaned = [...new Set(hoursArr)].sort((a, b) => a - b);
    h.outageHours = cleaned;
    h.outage_hours = cleaned;
    try {
        const updated = await apiFetch(`/api/houses/${h.id}`, {
            method: "PUT",
            body: JSON.stringify({ outage_hours: cleaned }),
        });
        Object.assign(h, updated);
    } catch (e) {
        toast("⚠️ " + e.message);
    }
    await refresh();
}

async function toggleOutageHour(hr) {
    const h = activeHouse();
    const cur = new Set(h.outageHours || h.outage_hours || []);
    cur.has(hr) ? cur.delete(hr) : cur.add(hr);
    await saveOutageHours([...cur]);
}

async function setOutageQueue(qKey) {
    const hrs = OUTAGE_QUEUES[qKey] || [];
    addEvent("info", "🕯️ Змінено графік ГПВ", hrs.length ? `Обрано ${hrs.length} год відключень` : "Відключення вимкнено");
    await saveOutageHours(hrs);
    toast(hrs.length ? `Застосовано графік ГПВ (${hrs.length} год)` : "Відключення прибрано");
}

async function applyPreset(presetKey) {
    const h = activeHouse();
    const names = { workday: "Робочий день", weekend: "Вихідний", winter: "Зима (Обігрів)", summer: "Літо (Кондиціонер)", eco: "Еко-ніч" };
    try {
        const updated = await apiFetch(`/api/houses/${h.id}/preset`, {
            method: "POST",
            body: JSON.stringify({ preset: presetKey }),
        });
        Object.assign(h, updated);
        addEvent("ok", "🎬 Завантажено сценарій", names[presetKey] || presetKey);
        await refresh();
        toast(`🎬 Застосовано сценарій: ${names[presetKey] || presetKey}`);
    } catch (e) {
        toast("⚠️ Не вдалося завантажити сценарій: " + e.message);
    }
}

function renderMeterAndSolar() {
    if (!sim || !sim.tariff_comparison) return;
    const tc = sim.tariff_comparison;
    const mx = Math.max(tc.monthly_1zone, tc.monthly_2zone, tc.monthly_3zone, 1);
    const pb = tc.payback_months_2zone !== null
        ? `Окупність двозонного лічильника (~${fmt(tc.meter_cost_uah, 0)} грн): <b style="color:var(--g)">${tc.payback_months_2zone} міс.</b> (економія <b>${fmt(tc.monthly_save_2zone, 0)} грн/міс</b>)`
        : `Економія двозонного тарифу поки мінімальна — перенесіть частину навантаження на ніч (23:00–07:00).`;

    $("aMeterCard").innerHTML = `
        <small class="muted">Порівняння місячних витрат за 1-зонним, 2-зонним та 3-зонним обліком.</small>
        <div class="rowbar"><span>1-зонний</span><span class="tr"><i style="width:${(tc.monthly_1zone / mx * 100).toFixed(0)}%;background:#EF4444"></i></span><span>${fmt(tc.monthly_1zone, 0)} грн/міс</span></div>
        <div class="rowbar"><span>2-зонний (ніч 50%)</span><span class="tr"><i style="width:${(tc.monthly_2zone / mx * 100).toFixed(0)}%;background:#FACC15"></i></span><span>${fmt(tc.monthly_2zone, 0)} грн/міс</span></div>
        <div class="rowbar"><span>3-зонний</span><span class="tr"><i style="width:${(tc.monthly_3zone / mx * 100).toFixed(0)}%;background:#38BDF8"></i></span><span>${fmt(tc.monthly_3zone, 0)} грн/міс</span></div>
        <div class="tip" style="margin-top:10px;border-top:1px solid var(--bd);border-bottom:none">📟 ${pb}</div>`;

    const yrSave = tc.solar_1_5kw_monthly_save_uah * 12;
    $("aSolarCard").innerHTML = `
        <small class="muted">Оцінка покриття денного навантаження (08:00–17:00) сонячною панеллю 1.5 кВт.</small>
        <div class="stats">
            <div class="stat"><b style="color:var(--y)">${fmt(tc.solar_1_5kw_daily_covered_kwh, 2)} кВт·год</b><small>покриває СЕС за добу</small></div>
            <div class="stat"><b style="color:var(--g)">${fmt(tc.solar_1_5kw_monthly_save_uah, 0)} грн</b><small>економія за місяць</small></div>
            <div class="stat"><b style="color:var(--b)">${fmt(yrSave, 0)} грн</b><small>економія за рік</small></div>
        </div>`;
}

function downloadPdfReport() {
    if (!sim) { toast("⚠️ Немає даних для звіту"); return; }
    const h = activeHouse();
    const tc = sim.tariff_comparison || {};
    const bk = sim.outage_backup || {};
    const nowStr = new Date().toLocaleString("uk-UA");
    const rows = (window._rows || []).map(r =>
        `<tr><td>${esc(r.name)}</td><td>${fmt(r.power, 0)} Вт</td><td>${fmt(r.kwh, 2)} кВт·год</td><td>${fmt(r.cost, 2)} грн</td><td>${fmt(r.cost * 30, 0)} грн</td></tr>`
    ).join("");

    $("printReport").innerHTML = `
        <h1>⚡ Паспорт енергоспоживання будинку — «${esc(h.name)}»</h1>
        <div class="pr-meta">Адреса: ${esc(h.address || "—")} • Власник: ${esc(state.profile.name)} • Сформовано: ${nowStr}</div>
        <div class="pr-kpis">
            <div class="pr-kpi"><b>${fmt(sim.total_kwh)} кВт·год</b><span>Споживання за добу</span></div>
            <div class="pr-kpi"><b>${fmt(sim.total_cost)} грн</b><span>Вартість за добу</span></div>
            <div class="pr-kpi"><b>${fmt(sim.peak_watt, 0)} / ${limitOf(h)} Вт</b><span>Пік (о ${sim.peak_hour}:00) / Ліміт</span></div>
            <div class="pr-kpi"><b>${fmt((tc.monthly_2zone ?? sim.total_cost * 30), 0)} грн/міс</b><span>Прогноз (2-зонний тариф)</span></div>
        </div>
        <h2>1. Параметри електромережі та тарифів</h2>
        <table>
            <tr><th>Напруга</th><th>Автомат</th><th>Ліміт потужності</th><th>Тариф день / ніч</th><th>Перевантаження</th></tr>
            <tr><td>${h.voltage} В</td><td>${h.amps} А</td><td>${limitOf(h)} Вт</td><td>${h.dayRate} / ${h.nightRate} грн</td><td>${sim.overload_hours.length ? sim.overload_hours.map(o => o.hour + ":00").join(", ") : "Немає"}</td></tr>
        </table>
        <h2>2. Структура споживання по приладах</h2>
        <table>
            <tr><th>Прилад</th><th>Потужність</th><th>кВт·год/доба</th><th>грн/доба</th><th>грн/міс</th></tr>
            ${rows || "<tr><td colspan='5'>Немає увімкнених приладів</td></tr>"}
        </table>
        <h2>3. Окупність лічильників, СЕС та резервне живлення</h2>
        <table>
            <tr><th>Показник</th><th>Значення</th></tr>
            <tr><td>Місячний чек (1-зонний / 2-зонний / 3-зонний)</td><td>${fmt(tc.monthly_1zone || 0, 0)} / ${fmt(tc.monthly_2zone || 0, 0)} / ${fmt(tc.monthly_3zone || 0, 0)} грн</td></tr>
            <tr><td>Окупність 2-зонного лічильника (~2000 грн)</td><td>${tc.payback_months_2zone ? tc.payback_months_2zone + " міс." : "—"}</td></tr>
            <tr><td>Покриття від сонячної панелі 1.5 кВт</td><td>${fmt(tc.solar_1_5kw_daily_covered_kwh || 0, 2)} кВт·год/доба (${fmt(tc.solar_1_5kw_monthly_save_uah || 0, 0)} грн/міс)</td></tr>
            <tr><td>Графік відключень (ГПВ) та EcoFlow</td><td>${(sim.outage_hours || []).length ? `${sim.outage_hours.length} год (${shortHours(sim.outage_hours)}) → ${esc(bk.recommended_model || "")}` : "Відключення не задано"}</td></tr>
        </table>`;
    window.print();
}

// ============================================================
// СТАРТ
// ============================================================
document.querySelectorAll(".nav-item").forEach(b => b.addEventListener("click", () => setView(b.dataset.view)));
$("profileBtn").addEventListener("click", openProfileForm);
$("bellBtn").addEventListener("click", openEvents);
$("houseSwitch").addEventListener("click", openHouseSwitch);
$("searchInput").addEventListener("input", () => { renderShell(); if (!$("view-houses").classList.contains("active")) setView("houses"); });
$("rangePills").addEventListener("click", e => {
    const b = e.target.closest("button"); if (!b) return;
    range = b.dataset.r; document.querySelectorAll("#rangePills button").forEach(x => x.classList.toggle("active", x === b)); drawAll();
});
$("timeline").addEventListener("click", async e => {
    const cell = e.target.closest("[data-tl-hr]");
    if (!cell) return;
    const appIdx = +cell.dataset.tlApp;
    const hr = +cell.dataset.tlHr;
    const h = activeHouse();
    const a = h.appliances[appIdx];
    if (!a) return;
    const set = new Set(a.schedule);
    if (set.has(hr)) {
        if (set.size <= 1) { toast("⚠️ Залиште хоча б одну годину або вимкніть прилад тумблером"); return; }
        set.delete(hr);
    } else {
        set.add(hr);
    }
    const newSched = [...set].sort((x, y) => x - y);
    a.schedule = newSched;
    renderShellExtras(h);
    try {
        const updated = await apiFetch(`/api/houses/${h.id}/appliances/${a.id}`, {
            method: "PUT",
            body: JSON.stringify({ schedule: newSched }),
        });
        h.appliances[appIdx] = updated;
    } catch (err) {
        toast("⚠️ " + err.message);
    }
    await refresh();
});
window.addEventListener("hashchange", () => setView(location.hash.slice(1)));
setView(location.hash.slice(1) || "home");
renderShell();
initFromDb();
