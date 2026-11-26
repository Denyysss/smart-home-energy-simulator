/**
 * js/app.js — Головна точка входу SPA ЕнергіяДім: рендер сторінок, навігація, motion-ефекти та ініціалізація модулів.
 */

import {
    $, L24, LUCIDE_BY_CAT, LUCIDE_BY_NAME, NIGHT, PALETTE, VIEWS,
    activeHouse, addEvent, animateNumber, apiFetch, ctx, dailyKwh,
    esc, estimate, eventsHtml, fmt, hourlyLoad, iconOf, limitOf,
    lucideTag, renderIcons, save, scheduleText, toast,
} from "./api.js";
import { drawAll } from "./charts.js";
import {
    applyOptimization, applyPreset, downloadPdfReport, exportData,
    initBackupImportListener, openOptimizeModal, previewOptimization, renderMeterAndSolar,
} from "./optimizer.js";
import {
    forceSyncHouseGpv, onGpvOblastChange, onGpvRaionChange,
    onGpvSettlementChange, onGpvSettlementFilter, onGpvStreetInput,
    openLiveGpvModal, renderLiveHeroPower, renderOutagePanel,
    selectGpvModalQueue, setGpvPowerFilter, setOutageQueue,
    startGpvAutoSyncTimer, toggleOutageHour,
} from "./gpv.js";
import {
    closeModal, deleteAppliance, deleteHouse, initModalListeners,
    openApiForm, openApplianceForm, openChartForm, openEvents,
    openHouseForm, openHouseSwitch, openLimitForm, openProfileForm,
    openTariffForm, resetAll, selectHouse, toggleAppliance,
} from "./modals.js";
import {
    initFromDb, initGuestUnloadCleanup, logoutUser,
    quickDemoLogin, submitAuth, switchAuthTab,
} from "./auth.js";
import {
    initPwaAndMobileSupport, openMobileAppModal, triggerPwaInstall,
} from "./pwa.js";

// ---------- Запуск симуляції та оновлення екрана ----------
async function runSim() {
    const h = activeHouse();
    const on = h.appliances.filter(a => a.enabled);
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
        ctx.sim = await apiFetch("/api/simulate", { method: "POST", body: JSON.stringify(body) });
        $("apiErr").style.display = "none";
    } catch (e) {
        ctx.sim = null;
        $("apiErr").style.display = "block";
    }
}

let simToken = 0;
export async function refresh() {
    save();
    renderShell();
    const t = ++simToken;
    await runSim();
    if (t !== simToken) return;
    if (ctx.sim) {
        const key = activeHouse().id + ":" + ctx.sim.overload_hours.map(o => o.hour).join(",");
        if (key !== ctx.lastOverKey) {
            if (ctx.lastOverKey !== null && ctx.sim.overload_hours.length) {
                addEvent("warn", "Перевищення ліміту", `Ліміт ${limitOf(activeHouse())} Вт, години: ${ctx.sim.overload_hours.map(o => o.hour + ":00").join(", ")}`);
            }
            ctx.lastOverKey = key;
            save();
        }
    }
    renderData();
}

// ---------- Рендер каркасу та аналітики ----------
export function renderShell() {
    const st = ctx.state;
    const h = activeHouse();
    const q = $("searchInput").value.trim().toLowerCase();

    $("profileName").textContent = st.profile.name;
    $("avatar").textContent = (st.profile.name[0] || "?").toUpperCase();
    $("heroName").textContent = h.name;
    const addrSpan = $("heroAddr").querySelector("span");
    if (addrSpan) addrSpan.textContent = h.address || "адресу не вказано (натисніть іконку геомітки зверху)";
    $("hsName").textContent = h.address || h.name;
    renderLiveHeroPower(h);

    $("rsTariff").textContent = `День: ${h.dayRate} / Ніч: ${h.nightRate} грн`;
    $("rsLimit").textContent = `${limitOf(h)} Вт (${h.voltage} В × ${h.amps} А)`;
    $("sProfile").textContent =
        `Ім'я: ${st.profile.name}` +
        (st.auth?.user?.username ? ` • Логін: ${st.auth.user.username}` : "") +
        (st.auth?.user?.is_guest ? " • Одноразовий гість" : "");
    $("sTariff").textContent = `Будинок «${h.name}»: день ${h.dayRate} грн, ніч ${h.nightRate} грн за кВт·год`;
    $("sLimitTxt").textContent = `${h.voltage} В × ${h.amps} А = ${limitOf(h)} Вт`;
    const s = st.settings;
    $("sChart").textContent = `Лінія ліміту: ${s.showLimit ? "так" : "ні"} • Сповіщення: ${s.alerts ? "так" : "ні"} • Попередження: ${s.warnPercent}% • Макс. осі Y: ${s.chartMax || "авто"}`;
    $("sApi").textContent = s.api || "(поточний сервер / відносні URL)";
    $("appTitle").textContent = `Прилади — ${h.name}`;

    const row = (a, i) => `<div class="rs ${a.enabled ? "" : "off"}"><div class="ic ${a.category}">${iconOf(a)}</div><div class="grow"><div class="bold">${esc(a.name)}</div>
        <small class="muted" style="margin:0">${a.power} Вт • ${a.qty} шт.</small><br><small style="color:var(--b)">${fmt(dailyKwh(a), 1)} кВт·год/день</small></div>
        <label class="tg"><input type="checkbox" ${a.enabled ? "checked" : ""} onchange="toggleAppliance(${i})"><span></span></label></div>`;
    $("rsList").innerHTML = h.appliances.length ? h.appliances.map(row).join("") : `<div class="empty">Немає приладів</div>`;

    $("appGrid").innerHTML = h.appliances.length
        ? h.appliances.map((a, i) => `<div class="item ${a.enabled ? "" : "off"}"><div class="ic ${a.category}">${iconOf(a)}</div>
            <div class="grow"><div class="it-n">${esc(a.name)}</div><div class="it-d">${a.power} Вт • ${a.qty} шт.${a.cyclic ? " • цикл " + Math.round(a.duty * 100) + "%" : ""}</div>
            <div class="it-d">${lucideTag("clock")} <span>${scheduleText(a.schedule)}</span></div><div class="it-k">${fmt(dailyKwh(a), 2)} кВт·год/день</div></div>
            <label class="tg"><input type="checkbox" ${a.enabled ? "checked" : ""} onchange="toggleAppliance(${i})"><span></span></label>
            <button class="mini" onclick="openApplianceForm(${i})" title="Редагувати">${lucideTag("pencil")}</button>
            <button class="mini del" onclick="deleteAppliance(${i})" title="Видалити">${lucideTag("trash-2")}</button></div>`).join("")
        : `<div class="empty">Приладів ще немає. Натисніть «+ Додати прилад».</div>`;

    const hs = st.houses.filter(x => !q || (x.name + " " + x.address).toLowerCase().includes(q));
    $("housesList").innerHTML = hs.length
        ? hs.map(x => `<div class="item ${x.id === h.id ? "active-house" : ""}"><div class="ic">${lucideTag("home")}</div>
            <div class="grow"><div class="it-n">${esc(x.name)} ${x.id === h.id ? '<small class="green">● обрано</small>' : ""}</div><div class="it-d">${lucideTag("map-pin")} <span>${esc(x.address || "—")}</span></div>
            <div class="it-d">${x.appliances.length} приладів • ліміт ${limitOf(x)} Вт</div><div class="it-d">Тарифи: ${x.dayRate} / ${x.nightRate} грн</div><div class="it-k">≈ ${fmt(estimate(x).kwh, 1)} кВт·год • ${fmt(estimate(x).cost, 1)} грн / доба</div></div>
            ${x.id === h.id ? "" : `<button class="mini" onclick="selectHouse(${x.id})">Обрати</button>`}
            <button class="mini" onclick="openHouseFormById(${x.id})" title="Редагувати">${lucideTag("pencil")}</button>
            <button class="mini del" onclick="deleteHouse(${x.id})" title="Видалити">${lucideTag("trash-2")}</button></div>`).join("")
        : `<div class="empty">Нічого не знайдено</div>`;

    $("eventsList").innerHTML = eventsHtml();
    renderShellExtras(h);
    renderIcons();
}

function renderShellExtras(h) {
    const on = h.appliances.filter(a => a.enabled);
    const inst = h.appliances.reduce((s, a) => s + a.power * a.qty, 0);
    const e = estimate(h);
    $("appSum").innerHTML = `
        <div class="kpi"><div class="kpi-ic y">${lucideTag("plug-zap")}</div><div class="kpi-v num">${on.length}/${h.appliances.length}</div><div class="kpi-l">приладів увімкнено</div></div>
        <div class="kpi"><div class="kpi-ic b">${lucideTag("zap")}</div><div class="kpi-v num">${fmt(inst / 1000, 1)} кВт</div><div class="kpi-l">встановлена потужність</div></div>
        <div class="kpi"><div class="kpi-ic p">${lucideTag("bar-chart-2")}</div><div class="kpi-v num">${fmt(e.kwh, 1)}</div><div class="kpi-l">кВт·год за добу (оцінка)</div></div>`;

    const load = hourlyLoad(h);
    const mx = Math.max(1, ...load);
    const outSet = new Set(h.outageHours || h.outage_hours || []);
    $("timeline").innerHTML = h.appliances.length
        ? `<div class="tl-h"><span></span><div>${L24.map((l, i) => `<span>${i % 3 === 0 ? i : ""}</span>`).join("")}</div></div>` +
          h.appliances.map((a, appIdx) => `<div class="tl-r" style="${a.enabled ? "" : "opacity:.4"}"><span class="tl-n">${iconOf(a)} <span>${esc(a.name)}</span></span><div class="tl-c">${L24.map((l, i) => {
              const isOn = a.schedule.includes(i);
              const cls = (isOn ? (NIGHT.has(i) ? "n" : "d") : "") + (outSet.has(i) ? " out" : "");
              return `<i data-tl-app="${appIdx}" data-tl-hr="${i}" class="${cls.trim()}" title="${esc(a.name)} — ${l} (${isOn ? "увімкнено" : "вимкнено"}${outSet.has(i) ? ", ГПВ" : ""}). Клікніть для перемикання"></i>`;
          }).join("")}</div></div>`).join("") +
          `<div class="tl-r"><span class="tl-n"><b>Σ Потужність</b></span><div class="tl-c">${load.map((w, i) =>
              `<i title="${L24[i]}: ${outSet.has(i) ? "0 Вт (відключення ГПВ)" : fmt(w, 0) + " Вт"}" style="background:${outSet.has(i) ? "rgba(239,68,68,.2)" : "rgba(239,68,68," + (0.12 + 0.88 * w / mx).toFixed(2) + ")"}"></i>`).join("")}</div></div>`
        : `<div class="empty">Додайте прилади, щоб побачити розклад</div>`;

    $("gallery").innerHTML = ctx.catalog.length
        ? ctx.catalog.map(c => {
            const ic = LUCIDE_BY_NAME[c.name] || LUCIDE_BY_CAT[c.category] || "plug";
            return `<button onclick="openApplianceForm(undefined, ${c.id})"><i>${lucideTag(ic)}</i><b>${esc(c.name)}</b><small>${c.power_watt} Вт${c.is_cyclic ? " • цикл " + Math.round(c.duty_cycle * 100) + "%" : ""}</small></button>`;
        }).join("")
        : `<div class="empty">Каталог недоступний (перевірте бекенд)</div>`;
}

function renderData() {
    const sim = ctx.sim;
    const h = activeHouse();
    const s = ctx.state.settings;
    const L = limitOf(h);
    if (!sim) {
        drawAll();
        return;
    }
    let dk = 0, nk = 0;
    sim.hourly_kwh.forEach((k, i) => (NIGHT.has(i) ? (nk += k) : (dk += k)));

    animateNumber($("kKwh"), sim.total_kwh, 2);
    animateNumber($("kCost"), sim.total_cost, 2);
    $("kSplit").textContent = `${fmt(dk)} / ${fmt(nk)}`;

    const pct = L ? (sim.peak_watt / L) * 100 : 0;
    const over = sim.overload_hours.length > 0;
    const warn = !over && pct >= s.warnPercent;
    $("gBar").style.width = Math.min(100, pct) + "%";
    $("gBar").className = "bar-fill " + (over ? "bad" : warn ? "warn" : "");
    $("gBadge").className = "pill " + (over ? "bad" : warn ? "warn" : "ok");
    $("gBadge").textContent = over ? "Перевантаження" : warn ? "Близько до ліміту" : "Нормальне";
    $("gText").textContent = `Пік ${fmt(sim.peak_watt, 0)} Вт о ${sim.peak_hour}:00 / ліміт ${L} Вт (${fmt(pct, 0)}%)`;
    $("overBanner").style.display = over && s.alerts ? "block" : "none";
    $("overText").textContent = `Автомат вибиває. Ліміт ${L} Вт, перевищення у годинах: ${sim.overload_hours.map(o => o.hour + ":00").join(", ")}.`;
    $("okBanner").style.display = !over ? "block" : "none";
    $("okBanner").innerHTML = `<span class="inline-ic">${lucideTag("check-circle-2")} <span>Навантаження в нормі. Пік ${fmt(sim.peak_watt, 0)} Вт (ліміт ${L} Вт).</span></span>`;

    const b = $("bellBadge");
    b.textContent = s.alerts ? sim.overload_hours.length : 0;
    b.classList.toggle("zero", !(s.alerts && sim.overload_hours.length));

    animateNumber($("tKwh"), sim.total_kwh, 1);
    animateNumber($("tCost"), sim.total_cost, 1);
    animateNumber($("tPeak"), sim.peak_watt, 0);
    $("tCount").textContent = h.appliances.filter(a => a.enabled).length + "/" + h.appliances.length;

    $("eventsList").innerHTML = eventsHtml();
    renderAnalytics(h, dk);
    renderMeterAndSolar();
    renderOutagePanel(h);
    renderExtras(h);
    renderIcons();
    drawAll();
    setupScrollMotion();
    previewOptimization(false);
}

function renderAnalytics(h, dk) {
    const sim = ctx.sim;
    animateNumber($("aMonthKwh"), sim.total_kwh * 30, 0);
    animateNumber($("aMonthCost"), sim.total_cost * 30, 0);
    const rows = sim.per_appliance.map(p => {
        let kwh = 0, cost = 0;
        p.hourly_watt.forEach((w, i) => {
            const k = w / 1000;
            kwh += k;
            cost += k * (NIGHT.has(i) ? h.nightRate : h.dayRate);
        });
        return { name: p.name, power: p.power_watt, kwh, cost };
    }).sort((a, b) => b.kwh - a.kwh);

    let saveCost = 0;
    sim.per_appliance.forEach(p => {
        if (p.power_watt >= 1000) {
            p.hourly_watt.forEach((w, i) => {
                if (!NIGHT.has(i)) saveCost += (w / 1000) * (h.dayRate - h.nightRate);
            });
        }
    });
    animateNumber($("aSave"), Math.max(0, saveCost * 30), 0, " грн/міс");
    $("aTable").innerHTML = rows.length
        ? `<table><tr><th>Прилад</th><th>кВт·год</th><th>грн/доба</th><th>грн/міс</th></tr>${rows.map(r =>
            `<tr><td>${esc(r.name)}</td><td>${fmt(r.kwh)}</td><td>${fmt(r.cost)}</td><td>${fmt(r.cost * 30, 0)}</td></tr>`).join("")}</table>`
        : `<div class="empty">Немає даних</div>`;

    const tips = [];
    if (sim.overload_hours.length) tips.push(["alert-triangle", "ic-r", `Автомат вибиває у ${sim.overload_hours.map(o => o.hour + ":00").join(", ")}. Розведіть потужні прилади по різних годинах або збільште номінал автомата.`]);
    if (rows[0] && sim.total_kwh) tips.push(["flame", "ic-o", `Найбільше споживає «${rows[0].name}» — ${fmt((rows[0].kwh / sim.total_kwh) * 100, 0)}% добового споживання.`]);
    if (sim.total_kwh && dk / sim.total_kwh > 0.7) tips.push(["moon", "ic-p", `${fmt((dk / sim.total_kwh) * 100, 0)}% енергії споживається вдень. Перенесення потужних приладів на ніч заощадить ≈ ${fmt(saveCost * 30, 0)} грн/міс.`]);
    if (h.dayRate && h.nightRate && h.nightRate < h.dayRate) tips.push(["banknote", "ic-g", `Нічний тариф дешевший у ${fmt(h.dayRate / h.nightRate, 1)} раза.`]);
    if (!tips.length) tips.push(["check-circle-2", "ic-g", "Все добре! Додайте більше приладів, щоб побачити поради."]);
    $("aTips").innerHTML = tips.map(([ic, cls, t]) => `<div class="tip">${lucideTag(ic, cls)}<span>${t}</span></div>`).join("");
    ctx.rows = rows;
}

function renderExtras(h) {
    const sim = ctx.sim;
    const rows = ctx.rows || [];
    const tot = rows.reduce((s, r) => s + r.kwh, 0) || 1;
    $("topList").innerHTML = rows.length
        ? rows.slice(0, 5).map((r, i) => `<div class="rowbar"><span>${esc(r.name)}</span><span class="tr"><i style="width:${((r.kwh / tot) * 100).toFixed(0)}%;background:${PALETTE[i % PALETTE.length]}"></i></span><span>${fmt(r.kwh, 1)} кВт·год</span></div>`).join("")
        : `<div class="empty">Немає даних</div>`;

    const L = limitOf(h);
    const hrs = L24.map((l, i) => ({ i, rate: NIGHT.has(i) ? h.nightRate : h.dayRate, free: L - sim.hourly_watt[i] }))
        .sort((a, b) => a.rate - b.rate || b.free - a.free).slice(0, 5);
    $("aBest").innerHTML =
        `<small class="muted">Нічні години з найбільшим запасом потужності — запускайте бойлер, пральну машину, посудомийку.</small>` +
        hrs.map(x => `<div class="rowbar"><span>${L24[x.i]}</span><span class="tr"><i style="width:${Math.max(3, Math.min(100, (x.free / L) * 100)).toFixed(0)}%;background:#22C55E"></i></span><span>${fmt(x.free, 0)} Вт</span></div>`).join("");

    const kwh = sim.total_kwh, actual = sim.total_cost, allDay = kwh * h.dayRate, allNight = kwh * h.nightRate;
    const mx = Math.max(allDay, actual, allNight, 0.01);
    const tr = [["Фактично", actual, "#FACC15"], ["Все вдень", allDay, "#EF4444"], ["Все вночі", allNight, "#22C55E"]];
    $("aTariff").innerHTML =
        `<small class="muted">Скільки коштувала б доба за різних режимів споживання.</small>` +
        tr.map(([n, v, col]) => `<div class="rowbar"><span>${n}</span><span class="tr"><i style="width:${((v / mx) * 100).toFixed(0)}%;background:${col}"></i></span><span>${fmt(v)} грн</span></div>`).join("") +
        `<small class="muted">Різниця денного й нічного тарифу: ${fmt(allDay - allNight)} грн на добу.</small>`;

    const co2d = kwh * 0.4, co2y = co2d * 365;
    const stat = (v, l) => `<div class="stat"><b>${v}</b><small>${l}</small></div>`;
    $("aEco").innerHTML =
        stat(fmt(co2d, 1) + " кг", "CO₂ за добу (≈0.4 кг/кВт·год)") +
        stat(fmt(co2y, 0) + " кг", "CO₂ за рік") +
        stat(fmt(co2y / 21, 0), "дерев потрібно для поглинання за рік") +
        stat(fmt(actual * 365, 0) + " грн", "витрати за рік (прогноз)") +
        stat((kwh ? fmt(actual / kwh) : "—") + " грн", "середня ціна кВт·год") +
        stat(fmt(sim.peak_watt / 1000, 2) + " кВт", "пікове навантаження");
}

// ---------- Навігація та Motion Design ----------
let scrollObserver = null;

function setupScrollMotion() {
    const activeView = document.querySelector(".view.active");
    if (!activeView) return;
    const targets = activeView.querySelectorAll(".hero, .page-hero, .kpi, .card, .item, .quick button, .stat");
    if (scrollObserver) scrollObserver.disconnect();

    let batch = 0;
    let batchTimer = null;
    scrollObserver = new IntersectionObserver(entries => {
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
            el.style.setProperty("--stagger", `${Math.min(idx * 45, 280)}ms`);
            requestAnimationFrame(() => el.classList.add("in-view"));
        } else {
            el.classList.remove("in-view");
            scrollObserver.observe(el);
        }
    });
}

export function setView(v) {
    if (!VIEWS[v]) v = "home";
    document.body.dataset.page = v;
    document.querySelectorAll(".view").forEach(e => e.classList.toggle("active", e.id === "view-" + v));
    document.querySelectorAll(".nav-item").forEach(e => e.classList.toggle("active", e.dataset.view === v));
    $("pageTitle").textContent = VIEWS[v][0];
    $("pageSub").textContent = VIEWS[v][1];
    if (location.hash !== "#" + v) history.replaceState(null, "", "#" + v);
    const sc = document.querySelector(".center");
    if (sc) sc.scrollTo(0, 0);
    drawAll();
    setupScrollMotion();
}

// ---------- Зв'язування модулів та глобальних обробників для HTML ----------
ctx.onRefresh = refresh;
ctx.onRenderShell = renderShell;
ctx.onSetView = setView;

initModalListeners();
initGuestUnloadCleanup();
initBackupImportListener();
startGpvAutoSyncTimer();
initPwaAndMobileSupport();

function openHouseFormById(id) {
    openHouseForm(ctx.state.houses.find(z => z.id === id));
}

Object.assign(window, {
    $,
    activeHouse,
    setView,
    openHouseForm,
    openHouseFormById,
    openHouseSwitch,
    openApplianceForm,
    openTariffForm,
    openLimitForm,
    openChartForm,
    openApiForm: () => openApiForm(initFromDb),
    openProfileForm,
    openEvents,
    closeModal,
    resetAll,
    toggleAppliance,
    deleteAppliance,
    selectHouse,
    deleteHouse,
    previewOptimization,
    openOptimizeModal,
    applyOptimization,
    applyPreset,
    downloadPdfReport,
    exportData,
    setOutageQueue,
    toggleOutageHour,
    openLiveGpvModal,
    onGpvOblastChange,
    onGpvRaionChange,
    onGpvSettlementFilter,
    onGpvSettlementChange,
    onGpvStreetInput,
    setGpvPowerFilter,
    selectGpvModalQueue,
    forceSyncHouseGpv,
    switchAuthTab,
    submitAuth,
    quickDemoLogin,
    logoutUser,
    openMobileAppModal,
    triggerPwaInstall,
});

// Слухачі головної панелі та інтерактивної 24-годинної матриці
document.querySelectorAll(".nav-item").forEach(b => b.addEventListener("click", () => setView(b.dataset.view)));
$("profileBtn").addEventListener("click", openProfileForm);
$("bellBtn").addEventListener("click", openEvents);
$("houseSwitch").addEventListener("click", openHouseSwitch);
$("searchInput").addEventListener("input", () => {
    renderShell();
    if (!$("view-houses").classList.contains("active")) setView("houses");
});
$("rangePills").addEventListener("click", e => {
    const b = e.target.closest("button");
    if (!b) return;
    ctx.range = b.dataset.r;
    document.querySelectorAll("#rangePills button").forEach(x => x.classList.toggle("active", x === b));
    drawAll();
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
        if (set.size <= 1) {
            toast("Залиште хоча б одну годину або вимкніть прилад тумблером");
            return;
        }
        set.delete(hr);
    } else {
        set.add(hr);
    }
    const newSched = [...set].sort((x, y) => x - y);
    a.schedule = newSched;
    renderShellExtras(h);
    renderIcons($("timeline"));
    try {
        const updated = await apiFetch(`/api/houses/${h.id}/appliances/${a.id}`, {
            method: "PUT",
            body: JSON.stringify({ schedule: newSched }),
        });
        h.appliances[appIdx] = updated;
    } catch (err) {
        toast(err.message);
    }
    await refresh();
});

document.addEventListener("mousemove", e => {
    const card = e.target.closest(".card, .kpi, .item");
    if (!card) return;
    const rect = card.getBoundingClientRect();
    card.style.setProperty("--mx", `${e.clientX - rect.left}px`);
    card.style.setProperty("--my", `${e.clientY - rect.top}px`);
}, { passive: true });

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

window.addEventListener("hashchange", () => setView(location.hash.slice(1)));

// Старт програми
renderIcons();
setView(location.hash.slice(1) || "home");
renderShell();
initFromDb();
