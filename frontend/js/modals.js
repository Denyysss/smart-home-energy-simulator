/**
 * js/modals.js — Універсальне модальне вікно та всі CRUD-форми (профіль, тарифи, ліміти, будинки, прилади).
 */

import { $, KEY, L24, NIGHT, activeHouse, addEvent, apiFetch, ctx, defaultState, esc, eventsHtml, lucideTag, num, renderIcons, save, toast } from "./api.js";

let modalCtx = null;

export function openForm({ title, fields, onSave, saveText = "Зберегти", hideSave = false }) {
    modalCtx = { fields, onSave, hours: new Set() };
    $("mTitle").textContent = title;
    $("mSave").textContent = saveText;
    $("mSave").style.display = hideSave ? "none" : "";
    $("mBody").innerHTML = fields.map(f => {
        const id = "f_" + f.key;
        const hint = f.hint ? `<div class="f-h">${f.hint}</div>` : "";
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
    renderIcons($("mBody"));
    $("overlay").classList.add("show");
    const first = $("mBody").querySelector("input[type=text],input[type=number]");
    if (first) first.focus();
}

export function closeModal() {
    $("overlay").classList.remove("show");
    modalCtx = null;
}

export function initModalListeners() {
    ctx.openForm = openForm;
    ctx.closeModal = closeModal;

    $("mBody").addEventListener("click", e => {
        const hb = e.target.closest("[data-h]");
        const pb = e.target.closest("[data-p]");
        if (hb && modalCtx) {
            const h = +hb.dataset.h;
            modalCtx.hours.has(h) ? modalCtx.hours.delete(h) : modalCtx.hours.add(h);
            hb.classList.toggle("on");
        }
        if (pb && modalCtx) {
            const P = {
                all: [...Array(24).keys()],
                day: [7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22],
                night: [23, 0, 1, 2, 3, 4, 5, 6],
                morning: [6, 7, 8, 9],
                evening: [18, 19, 20, 21, 22],
                clear: [],
            };
            modalCtx.hours = new Set(P[pb.dataset.p]);
            $("mBody").querySelectorAll("[data-h]").forEach(b => b.classList.toggle("on", modalCtx.hours.has(+b.dataset.h)));
        }
    });

    $("overlay").addEventListener("mousedown", e => {
        if (e.target === $("overlay")) closeModal();
    });

    document.addEventListener("keydown", e => {
        if (e.key === "Escape") closeModal();
        if (e.key === "Enter" && modalCtx && e.target.tagName === "INPUT" && e.target.type !== "checkbox") $("mSave").click();
    });

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
            if (typeof res === "string") {
                toast(res);
                return;
            }
            closeModal();
        } catch (err) {
            toast("Помилка: " + err.message);
        }
    });
}

// ---------- Форми та дії над будинками й приладами ----------
export function openProfileForm() {
    const st = ctx.state;
    const isGuest = Boolean(st.auth?.user?.is_guest);
    const uname = st.auth?.user?.username ? `Логін: <b>${esc(st.auth.user.username)}</b>` : "";
    const guestNote = isGuest
        ? `<div class="muted" style="margin:6px 0 10px;color:#FACC15">Одноразовий гостьовий сеанс: після виходу або закриття сайту цей акаунт та його дані автоматично видаляються з БД.</div>`
        : "";
    const logoutLabel = isGuest ? "Вийти та видалити гостьовий акаунт" : "Вийти з акаунта";

    openForm({
        title: "Особистий профіль",
        fields: [
            { type: "html", key: "uinfo", html: (uname ? `<div class="muted" style="margin:4px 0 6px">${uname}</div>` : "") + guestNote },
            { key: "name", label: "Ваше ім'я", value: st.profile.name },
            { type: "html", key: "logout", html: `<div style="margin-top:14px"><button type="button" class="btn-r" style="width:100%" onclick="closeModal();logoutUser()">${logoutLabel}</button></div>` },
        ],
        onSave: async v => {
            const newName = v.name.trim();
            if (!newName) return "Введіть ім'я";
            if (st.auth && st.auth.token) {
                const updated = await apiFetch("/api/auth/me", {
                    method: "PUT",
                    body: JSON.stringify({ name: newName }),
                });
                st.auth.user = updated;
            }
            st.profile.name = newName;
            save();
            if (ctx.onRenderShell) ctx.onRenderShell();
            toast("Профіль оновлено");
        },
    });
}

export function openTariffForm() {
    const h = activeHouse();
    openForm({
        title: "Тарифи на електроенергію",
        fields: [
            { key: "day", label: "Денний тариф (07:00–23:00), грн/кВт·год", type: "number", step: "0.01", min: 0, value: h.dayRate },
            { key: "night", label: "Нічний тариф (23:00–07:00), грн/кВт·год", type: "number", step: "0.01", min: 0, value: h.nightRate },
        ],
        onSave: async v => {
            const d = num(v.day), n = num(v.night);
            if (!(d >= 0) || !(n >= 0)) return "Введіть коректні тарифи";
            const updated = await apiFetch(`/api/houses/${h.id}`, {
                method: "PUT",
                body: JSON.stringify({ day_rate: d, night_rate: n }),
            });
            Object.assign(h, updated);
            addEvent("info", "Змінено тарифи", `День ${d}, ніч ${n} грн`);
            if (ctx.onRefresh) await ctx.onRefresh();
            toast("Тарифи збережено в БД");
        },
    });
}

export function openLimitForm() {
    const h = activeHouse();
    openForm({
        title: "Ліміт автомата",
        fields: [
            { key: "volt", label: "Напруга, В", type: "number", min: 100, max: 440, value: h.voltage },
            { key: "amps", label: "Автомат, А", type: "number", min: 1, max: 200, value: h.amps, hint: "Типові: 16 А = 3680 Вт, 25 А = 5750 Вт, 32 А = 7360 Вт" },
        ],
        onSave: async v => {
            const a = num(v.volt), b = num(v.amps);
            if (!(a > 0) || !(b > 0)) return "Введіть коректні значення";
            const updated = await apiFetch(`/api/houses/${h.id}`, {
                method: "PUT",
                body: JSON.stringify({ voltage: a, amps: b, max_power_watt: a * b }),
            });
            Object.assign(h, updated);
            addEvent("info", "Змінено ліміт", `${a * b} Вт`);
            if (ctx.onRefresh) await ctx.onRefresh();
            toast("Ліміт збережено в БД");
        },
    });
}

export function openChartForm() {
    const s = ctx.state.settings;
    openForm({
        title: "Параметри графіків і сповіщень",
        fields: [
            { key: "showLimit", type: "check", label: "Показувати лінію ліміту автомата", value: s.showLimit },
            { key: "alerts", type: "check", label: "Сповіщення про перевантаження", value: s.alerts },
            { key: "warn", label: "Попередження при навантаженні, % від ліміту", type: "number", min: 10, max: 100, value: s.warnPercent },
            { key: "ymax", label: "Максимум осі Y, Вт (0 = авто)", type: "number", min: 0, value: s.chartMax },
        ],
        onSave: v => {
            s.showLimit = v.showLimit;
            s.alerts = v.alerts;
            s.warnPercent = Math.min(100, Math.max(10, num(v.warn) || 80));
            s.chartMax = Math.max(0, num(v.ymax) || 0);
            if (ctx.onRefresh) ctx.onRefresh();
            toast("Параметри збережено");
        },
    });
}

export function openApiForm(initFromDbFn) {
    openForm({
        title: "Адреса бекенду",
        fields: [{ key: "api", label: "URL (порожньо = поточний хост)", value: ctx.state.settings.api }],
        onSave: async v => {
            ctx.state.settings.api = v.api.trim().replace(/\/+$/, "");
            save();
            if (initFromDbFn) await initFromDbFn();
        },
    });
}

export function openHouseForm(h) {
    const edit = !!h;
    openForm({
        title: edit ? "Редагувати будинок" : "Новий будинок",
        fields: [
            { key: "name", label: "Назва", value: h?.name || "" },
            { key: "address", label: "Адреса", value: h?.address || "" },
            ...(edit
                ? [{
                    type: "html",
                    key: "gpv_btn",
                    html: `<div style="margin:6px 0 8px"><button type="button" class="btn-g btn-ic" style="width:100%;justify-content:center" onclick="closeModal();openLiveGpvModal()">${lucideTag("radio")}<span>Вибрати Область → Район → Село → Вулицю (живий ГПВ обленерго)</span></button></div>`,
                }]
                : []),
            { key: "volt", label: "Напруга, В", type: "number", value: h?.voltage ?? 230 },
            { key: "amps", label: "Автомат, А", type: "number", value: h?.amps ?? 16 },
            { key: "day", label: "Ціна вдень, грн/кВт·год", type: "number", step: "0.01", value: h?.dayRate ?? 4.32 },
            { key: "night", label: "Ціна вночі, грн/кВт·год", type: "number", step: "0.01", value: h?.nightRate ?? 2.16 },
        ],
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
                ctx.state.houses.push(created);
                ctx.state.activeHouse = created.id;
                addEvent("ok", "Додано будинок", created.name);
            }
            if (ctx.onRefresh) await ctx.onRefresh();
            toast("Збережено в БД");
        },
    });
}

export function openHouseSwitch() {
    openForm({
        title: "Обрати будинок",
        fields: [{
            key: "id",
            type: "select",
            label: "Будинок",
            value: ctx.state.activeHouse,
            options: ctx.state.houses.map(h => ({ v: h.id, l: `${h.name} — ${h.address || "без адреси"}` })),
        }],
        onSave: v => {
            ctx.state.activeHouse = +v.id;
            if (ctx.onRefresh) ctx.onRefresh();
        },
    });
}

export function openApplianceForm(idx, preCat) {
    if (!ctx.catalog.length) toast("Каталог не завантажено — перевірте бекенд");
    const h = activeHouse();
    const a = idx !== undefined ? h.appliances[idx] : null;
    openForm({
        title: a ? "Редагувати прилад" : "Додати прилад",
        fields: [
            { key: "cat", type: "select", label: "Прилад з каталогу", value: a?.catalogId ?? preCat ?? ctx.catalog[0]?.id, options: ctx.catalog.map(c => ({ v: c.id, l: `${c.name} (${c.power_watt} Вт)` })) },
            { key: "name", label: "Своя назва (необов'язково)", value: a && a.custom ? a.name : "" },
            { key: "power", label: "Своя потужність, Вт (порожньо = з каталогу)", type: "number", value: a && a.customPower ? a.power : "" },
            { key: "qty", label: "Кількість", type: "number", min: 1, max: 50, value: a?.qty ?? 1 },
            { key: "schedule", type: "hours", label: "Години роботи", value: a?.schedule || [] },
        ],
        saveText: a ? "Зберегти" : "Додати",
        onSave: async v => {
            const c = ctx.catalog.find(x => x.id === +v.cat);
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
                addEvent("ok", "Додано прилад", `${created.name}, ${created.power} Вт × ${created.qty}`);
            }
            if (ctx.onRefresh) await ctx.onRefresh();
            toast(a ? "Прилад оновлено в БД" : "Прилад додано в БД");
        },
    });
}

export function openEvents() {
    openForm({ title: "Події системи", hideSave: true, fields: [{ type: "html", key: "ev", html: eventsHtml() }] });
}

export async function resetAll() {
    if (!confirm("Скинути всі дані у базі даних та браузері до початкових?")) return;
    try {
        const houses = await apiFetch("/api/reset", { method: "POST" });
        const savedAuth = ctx.state.auth;
        const savedProfile = ctx.state.profile;
        localStorage.removeItem(KEY);
        ctx.state = defaultState();
        ctx.state.auth = savedAuth;
        ctx.state.profile = savedProfile;
        ctx.state.houses = houses;
        ctx.state.activeHouse = houses[0]?.id || 1;
        if (ctx.onRefresh) await ctx.onRefresh();
        toast("Дані у БД скинуто");
    } catch (e) {
        toast("Не вдалося скинути БД: " + e.message);
    }
}

export async function toggleAppliance(i) {
    const h = activeHouse();
    const a = h.appliances[i];
    a.enabled = !a.enabled;
    try {
        const updated = await apiFetch(`/api/houses/${h.id}/appliances/${a.id}`, {
            method: "PUT",
            body: JSON.stringify({ enabled: a.enabled }),
        });
        h.appliances[i] = updated;
    } catch (e) {
        toast(e.message);
    }
    if (ctx.onRefresh) await ctx.onRefresh();
}

export async function deleteAppliance(i) {
    const h = activeHouse();
    const a = h.appliances[i];
    if (!confirm(`Видалити «${a.name}»?`)) return;
    try {
        await apiFetch(`/api/houses/${h.id}/appliances/${a.id}`, { method: "DELETE" });
        h.appliances.splice(i, 1);
        addEvent("neutral", "Видалено прилад", a.name);
        if (ctx.onRefresh) await ctx.onRefresh();
    } catch (e) {
        toast(e.message);
    }
}

export function selectHouse(id) {
    ctx.state.activeHouse = id;
    if (ctx.onRefresh) ctx.onRefresh();
    toast("Будинок обрано");
}

export async function deleteHouse(id) {
    if (ctx.state.houses.length < 2) {
        toast("Має лишитись хоча б один будинок");
        return;
    }
    const h = ctx.state.houses.find(x => x.id === id);
    if (!confirm(`Видалити будинок «${h.name}»?`)) return;
    try {
        await apiFetch(`/api/houses/${id}`, { method: "DELETE" });
        ctx.state.houses = ctx.state.houses.filter(x => x.id !== id);
        if (ctx.state.activeHouse === id) ctx.state.activeHouse = ctx.state.houses[0].id;
        if (ctx.onRefresh) await ctx.onRefresh();
    } catch (e) {
        toast(e.message);
    }
}
