/**
 * js/api.js — Спільний стан застосунку, запити до FastAPI бекенду та базові утиліти UI.
 */

export const NIGHT = new Set([23, 0, 1, 2, 3, 4, 5, 6]);
export const L24 = Array.from({ length: 24 }, (_, i) => String(i).padStart(2, "0") + ":00");
export const PALETTE = ["#FACC15", "#38BDF8", "#818CF8", "#22C55E", "#F97316", "#EC4899", "#14B8A6", "#A3E635", "#F43F5E", "#60A5FA"];

export const LUCIDE_BY_NAME = {
    "Бойлер 80л": "flame",
    "Холодильник": "refrigerator",
    "Освітлення (кімната)": "lightbulb",
    "Пральна машина": "washing-machine",
    "Мікрохвильовка": "microwave",
    "Кондиціонер": "snowflake",
    "Телевізор": "tv",
    "Електрочайник": "coffee",
    "Комп'ютер (ПК)": "monitor",
    "Електроплита (1 конфорка)": "cooking-pot",
    "Посудомийна машина": "sparkles",
    "Зарядка електромобіля": "car-front",
};

export const LUCIDE_BY_CAT = {
    heating: "flame",
    cooling: "snowflake",
    lighting: "lightbulb",
    entertainment: "tv",
    kitchen: "utensils",
    laundry: "waves",
    water: "droplets",
    transport: "car-front",
};

export const VIEWS = {
    home: ["Головна", "Огляд споживання вашого будинку"],
    houses: ["Мої будинки", "Керуйте будинками та відстежуйте споживання електроенергії (збережено в БД)"],
    appliances: ["Прилади", "Додавайте, редагуйте та вмикайте прилади"],
    charts: ["Графіки", "Погодинне споживання та вартість"],
    analytics: ["Аналітика", "Прогноз витрат і поради з економії"],
    settings: ["Налаштування", "Профіль, тарифи, ліміти, графіки"],
};

export const KEY = "energydim.v3";

export function detectDefaultApi() {
    if (window.ENERGYDIM_API_URL !== undefined) return window.ENERGYDIM_API_URL;
    const isLocalHost = location.hostname === "localhost" || location.hostname === "127.0.0.1";
    if (location.protocol === "file:") {
        return "http://127.0.0.1:8000";
    }
    if (isLocalHost && location.port && location.port !== "8000") {
        return `${location.protocol}//${location.hostname}:8000`;
    }
    return "";
}

export function defaultState() {
    return {
        auth: null,
        profile: { name: "Гість" },
        activeHouse: 1,
        settings: { api: detectDefaultApi(), showLimit: true, alerts: true, warnPercent: 80, chartMax: 0 },
        houses: [{ id: 1, name: "Мій будинок", address: "", voltage: 230, amps: 16, dayRate: 4.32, nightRate: 2.16, appliances: [] }],
        events: [],
    };
}

export function loadState() {
    try {
        const s = JSON.parse(localStorage.getItem(KEY));
        if (s && s.settings) {
            if (s.auth?.user?.is_guest) {
                s.auth = null;
                s.houses = defaultState().houses;
                s.events = [];
            }
            return { ...defaultState(), ...s, settings: { ...defaultState().settings, ...s.settings } };
        }
    } catch (e) {}
    return defaultState();
}

// Спільний реактивний контекст між усіма ES-модулями
export const ctx = {
    state: loadState(),
    catalog: [],
    sim: null,
    range: "day",
    lastOverKey: null,
    lastOptimization: null,
    rows: [],
    openForm: null,
    closeModal: null,
    onRefresh: null,
    onRenderShell: null,
    onShowAuth: null,
    onSetView: null,
};

export function save() {
    const st = ctx.state;
    if (st.auth?.user?.is_guest) {
        const cleanCopy = {
            ...defaultState(),
            settings: st.settings,
        };
        localStorage.setItem(KEY, JSON.stringify(cleanCopy));
        return;
    }
    localStorage.setItem(KEY, JSON.stringify(st));
}

export const activeHouse = () =>
    ctx.state.houses.find(h => h.id === ctx.state.activeHouse) || ctx.state.houses[0] || defaultState().houses[0];

export const limitOf = h => (h.voltage || 230) * (h.amps || 16);
export const apiUrl = path => (ctx.state.settings.api || "") + path;

// ---------- Утиліти DOM, форматування та анімації ----------
export const $ = id => document.getElementById(id);
export const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const num = v => parseFloat(String(v).replace(",", "."));
export const fmt = (n, d = 2) => Number(n).toFixed(d);

export function renderIcons(root = document) {
    if (window.lucide && typeof window.lucide.createIcons === "function") {
        window.lucide.createIcons({ root });
    }
}

export function lucideTag(name, cls = "") {
    return `<i data-lucide="${esc(name)}"${cls ? ` class="${esc(cls)}"` : ""}></i>`;
}

export function iconNameOf(a) {
    return LUCIDE_BY_NAME[a.name] || LUCIDE_BY_CAT[a.category] || "plug";
}

export function iconOf(a) {
    return lucideTag(iconNameOf(a));
}

export function toast(msg) {
    const t = $("toast");
    if (!t) return;
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => t.classList.remove("show"), 2300);
}

const _numAnim = new WeakMap();
export function animateNumber(el, target, decimals = 2, suffix = "", duration = 480) {
    if (!el) return;
    if (!Number.isFinite(target)) {
        el.textContent = "—";
        return;
    }
    const prev = _numAnim.get(el);
    if (prev && prev.raf) cancelAnimationFrame(prev.raf);

    const startVal = prev && Number.isFinite(prev.val) ? prev.val : 0;
    if (Math.abs(target - startVal) < 0.001) {
        el.textContent = fmt(target, decimals) + suffix;
        _numAnim.set(el, { val: target, raf: null });
        return;
    }

    const startTime = performance.now();
    function step(now) {
        const progress = Math.min(1, (now - startTime) / duration);
        const eased = 1 - Math.pow(1 - progress, 4);
        const current = startVal + (target - startVal) * eased;
        el.textContent = fmt(current, decimals) + suffix;
        if (progress < 1) {
            const raf = requestAnimationFrame(step);
            _numAnim.set(el, { val: current, raf });
        } else {
            el.textContent = fmt(target, decimals) + suffix;
            _numAnim.set(el, { val: target, raf: null });
        }
    }
    const raf = requestAnimationFrame(step);
    _numAnim.set(el, { val: startVal, raf });
}

export function dailyKwh(a) {
    return a.power * a.duty * a.qty * a.schedule.length / 1000;
}

export function scheduleText(s) {
    if (s.length === 24) return "Працює постійно";
    if (!s.length) return "Не заплановано";
    const x = [...s].sort((a, b) => a - b), r = [];
    let st = x[0], en = x[0];
    for (let i = 1; i < x.length; i++) {
        if (x[i] === en + 1) en = x[i];
        else { r.push(`${st}:00 до ${en + 1}:00`); st = en = x[i]; }
    }
    r.push(`${st}:00 до ${en + 1}:00`);
    return "Працює з " + r.join(", ");
}

export function shortHours(s) {
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

export function estimate(house) {
    let kwh = 0, cost = 0;
    house.appliances.filter(a => a.enabled).forEach(a => a.schedule.forEach(hr => {
        const k = a.power * a.duty * a.qty / 1000;
        kwh += k;
        cost += k * (NIGHT.has(hr) ? house.nightRate : house.dayRate);
    }));
    return { kwh, cost };
}

export function hourlyLoad(house) {
    const w = Array(24).fill(0);
    house.appliances.filter(a => a.enabled).forEach(a => a.schedule.forEach(hr => {
        w[hr] += a.power * a.duty * a.qty;
    }));
    return w;
}

const EV_ICON = { warn: "alert-triangle", ok: "check-circle-2", info: "info", neutral: "bell" };

export function addEvent(type, title, desc) {
    const time = new Date().toLocaleTimeString("uk-UA", { hour: "2-digit", minute: "2-digit" });
    const cleanTitle = title.replace(/^[^\p{L}\p{N}]+/u, "").trim() || title;
    ctx.state.events.unshift({ type, title: cleanTitle, desc, time });
    ctx.state.events = ctx.state.events.slice(0, 20);
}

export function eventsHtml() {
    if (!ctx.state.events.length) return `<div class="empty">Подій поки немає</div>`;
    return ctx.state.events.map(e => {
        const ic = EV_ICON[e.type] || "bell";
        return `<div class="ev"><div class="ev-i ${e.type}">${lucideTag(ic)}</div><div class="grow"><div class="ev-t">${esc(e.title)}</div><div class="ev-d">${esc(e.desc)}</div></div><span class="ev-time">${e.time}</span></div>`;
    }).join("");
}

export async function apiFetch(path, options = {}) {
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    if (ctx.state.auth && ctx.state.auth.token && !headers.Authorization) {
        headers.Authorization = "Bearer " + ctx.state.auth.token;
    }
    let r;
    try {
        r = await fetch(apiUrl(path), { ...options, headers });
    } catch (netErr) {
        throw new Error("Бекенд не запущено або недоступний (порт 8000). Запустіть сервер у терміналі: python -m uvicorn main:app --reload --port 8000");
    }
    if (!r.ok) {
        let detail = "HTTP " + r.status;
        try {
            const err = await r.json();
            if (err.detail) detail = typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail);
        } catch (e) {}
        if (r.status === 401 && !path.startsWith("/api/auth/login") && !path.startsWith("/api/auth/register") && !path.startsWith("/api/auth/guest")) {
            ctx.state.auth = null;
            save();
            if (ctx.onShowAuth) ctx.onShowAuth();
        }
        throw new Error(detail);
    }
    return r.json();
}
