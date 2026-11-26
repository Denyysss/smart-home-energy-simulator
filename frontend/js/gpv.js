/**
 * js/gpv.js — Довідник КАТОТТГ (Область -> Район -> Село/Місто -> Вулиця),
 * жива синхронізація фактичних графіків ГПВ «Вінницяобленерго» (e-svitlo) та калькулятор EcoFlow.
 */

import { $, L24, activeHouse, addEvent, apiFetch, ctx, esc, fmt, lucideTag, renderIcons, shortHours, toast } from "./api.js";

const OUTAGE_QUEUES = {
    none: [],
    q1: [8, 9, 10, 11, 20, 21, 22, 23],
    q2: [0, 1, 2, 3, 12, 13, 14, 15],
    q3: [4, 5, 6, 7, 16, 17, 18, 19],
};

export function renderLiveHeroPower(h) {
    const stEl = $("heroPowerStatus");
    const txtEl = $("heroPowerText");
    const subEl = $("heroGpvSub");
    const yardEl = $("houseYardEllipse");
    const winPolys = document.querySelectorAll(".house-win-poly");
    if (!stEl || !txtEl) return;

    const lg = h.live_gpv;
    if (h.gpv_auto_sync && h.gpv_queue && lg) {
        const hasPower = lg.has_power_now !== false;
        stEl.classList.toggle("off-gpv", !hasPower);
        txtEl.textContent = hasPower
            ? `Світло є • Черга ${h.gpv_queue}`
            : `Відключення ГПВ • Черга ${h.gpv_queue}`;
        if (subEl) {
            subEl.style.display = "block";
            subEl.textContent = `${lg.next_change_text}${lg.updated_at ? ` • Оновлено: ${lg.updated_at}` : ""}`;
        }
        winPolys.forEach(p => p.setAttribute("fill", hasPower ? "url(#winWarm)" : "#162033"));
        if (yardEl) yardEl.setAttribute("opacity", hasPower ? "1" : "0.22");
    } else {
        stEl.classList.remove("off-gpv");
        txtEl.textContent = "Онлайн";
        if (subEl) subEl.style.display = "none";
        winPolys.forEach(p => p.setAttribute("fill", "url(#winWarm)"));
        if (yardEl) yardEl.setAttribute("opacity", "1");
    }
}

export function renderOutagePanel(h) {
    const sim = ctx.sim;
    const outArr = sim && sim.outage_hours ? sim.outage_hours : (h.outageHours || h.outage_hours || []);
    const outSet = new Set(outArr);

    const bannerEl = $("liveGpvBanner");
    if (bannerEl) {
        const lg = h.live_gpv;
        if (h.gpv_auto_sync && h.gpv_queue && lg) {
            const hasPower = lg.has_power_now !== false;
            const ivText = lg.intervals && lg.intervals.length
                ? `Фактичні відключення сьогодні: <b>${lg.intervals.join(", ")}</b>`
                : `На зараз підтверджених відключень на добу <b>немає</b>`;
            bannerEl.style.display = "flex";
            bannerEl.className = "gpv-live-banner" + (hasPower ? "" : " is-off");
            bannerEl.innerHTML = `
                <div>
                    <div><b>${hasPower ? "🟢 Світло за графіком є" : "🔴 Зараз відключення за графіком"}</b> • Черга <b>${esc(h.gpv_queue)}</b> (${esc(lg.provider || "Вінницяобленерго")})</div>
                    <div class="muted" style="margin:3px 0 0">${ivText} • ${esc(lg.next_change_text || "")}</div>
                    ${lg.updated_at ? `<div class="muted" style="margin:2px 0 0;font-size:.72rem">Останнє оновлення з e-svitlo / обленерго: ${esc(lg.updated_at)} (автоперевірка щохвилини)</div>` : ""}
                </div>
                <div style="display:flex;gap:6px;flex-wrap:wrap">
                    <button type="button" class="btn-g sm-btn btn-ic" onclick="forceSyncHouseGpv()">${lucideTag("refresh-cw")}<span>Перевірити зараз</span></button>
                    <button type="button" class="btn-g sm-btn btn-ic" onclick="openLiveGpvModal()">${lucideTag("map-pin")}<span>Змінити адресу</span></button>
                </div>`;
            renderIcons(bannerEl);
        } else {
            bannerEl.style.display = "none";
        }
    }

    $("outageGrid").innerHTML = L24.map((l, hr) =>
        `<button type="button" class="outage-hour ${outSet.has(hr) ? "off" : ""}" onclick="toggleOutageHour(${hr})" title="Година ${l}">${String(hr).padStart(2, "0")}:00</button>`
    ).join("");

    const bk = sim && sim.outage_backup;
    if (!bk || outSet.size === 0) {
        $("outageStats").innerHTML = `<small class="muted" style="margin:6px 0 0">Оберіть години відключення вище або підключіть адресу обленерго, щоб розрахувати необхідну зарядну станцію (EcoFlow / інвертор).</small>`;
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
                <div class="ecoflow-model inline-ic">${lucideTag("battery-charging")} <span>${esc(bk.recommended_model)}</span></div>
            </div>
            <button class="btn-y sm btn-ic" onclick="previewOptimization(true)">${lucideTag("sparkles")}<span>Перенести прилади з годин відключень</span></button>
        </div>
        ${affectedHtml}`;
    renderIcons($("outageStats"));
}

export async function saveOutageHours(hoursArr) {
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
        toast(e.message);
    }
    if (ctx.onRefresh) await ctx.onRefresh();
}

export async function toggleOutageHour(hr) {
    const h = activeHouse();
    const cur = new Set(h.outageHours || h.outage_hours || []);
    cur.has(hr) ? cur.delete(hr) : cur.add(hr);
    await saveOutageHours([...cur]);
}

export async function setOutageQueue(qKey) {
    const hrs = OUTAGE_QUEUES[qKey] || [];
    addEvent("info", "Змінено графік ГПВ", hrs.length ? `Обрано ${hrs.length} год відключень` : "Відключення вимкнено");
    await saveOutageHours(hrs);
    toast(hrs.length ? `Застосовано графік ГПВ (${hrs.length} год)` : "Відключення прибрано");
}

// ---------- Модалка вибору Область -> Район -> Село/Місто -> Вулиця та автопідбору черги ----------
const _gpvModalState = {
    oblasts: [],
    raions: [],
    settlements: [],
    oblast: "Вінницька",
    raion: "Вінницький",
    settlement: "м. Вінниця",
    street: "",
    houseNum: "",
    hasPowerNow: null,
    lookup: null,
    selectedQueue: "1.1",
};
let _streetDebounce = null;

export async function openLiveGpvModal() {
    const h = activeHouse();
    _gpvModalState.oblast = h.oblast || "Вінницька";
    _gpvModalState.raion = h.raion || "";
    _gpvModalState.settlement = h.settlement || "";
    _gpvModalState.street = h.street || "";
    _gpvModalState.houseNum = h.house_num || "";
    _gpvModalState.selectedQueue = h.gpv_queue || "1.1";
    _gpvModalState.hasPowerNow = null;

    ctx.openForm({
        title: "Живий графік обленерго за адресою",
        saveText: "Підключити автографік",
        fields: [{
            type: "html",
            key: "gpv_ui",
            html: `
                <div class="muted" style="margin:4px 0 10px">
                    Оберіть область, район, своє місто або село та вулицю. Система підтягне <b>100% фактичний графік (e-svitlo / обленерго)</b> і сама зніматиме відключення, якщо обленерго їх скасує.
                </div>
                <div class="gpv-grid2">
                    <div>
                        <label class="f-l" for="gpvOblast">Область</label>
                        <select class="f-i" id="gpvOblast" onchange="onGpvOblastChange(this.value)"></select>
                    </div>
                    <div>
                        <label class="f-l" for="gpvRaion">Район</label>
                        <select class="f-i" id="gpvRaion" onchange="onGpvRaionChange(this.value)"></select>
                    </div>
                </div>

                <label class="f-l" for="gpvSetSearch">Населений пункт (місто, селище або село)</label>
                <input class="f-i" id="gpvSetSearch" placeholder="Почніть вводити назву села чи міста для швидкого пошуку..." oninput="onGpvSettlementFilter(this.value)" style="margin-bottom:6px">
                <select class="f-i" id="gpvSettlement" onchange="onGpvSettlementChange(this.value)"></select>

                <div class="gpv-grid2" style="margin-top:4px">
                    <div>
                        <label class="f-l" for="gpvStreet">Вулиця</label>
                        <input class="f-i" id="gpvStreet" list="gpvStreetList" placeholder="Напр. вул. Центральна" value="${esc(_gpvModalState.street)}" oninput="onGpvStreetInput(this.value)">
                        <datalist id="gpvStreetList"></datalist>
                    </div>
                    <div>
                        <label class="f-l" for="gpvHouseNum">№ будинку</label>
                        <input class="f-i" id="gpvHouseNum" placeholder="Напр. 12" value="${esc(_gpvModalState.houseNum)}">
                    </div>
                </div>

                <div style="margin-top:12px;padding-top:10px;border-top:1px solid var(--bd)">
                    <div class="f-l" style="margin-top:0">Перевірка у реальному часі: за вашою адресою зараз є світло?</div>
                    <div class="gpv-light-btns">
                        <button type="button" class="gpv-lbtn" id="gpvBtnNo" onclick="setGpvPowerFilter(false)">🔴 Ні, зараз немає</button>
                        <button type="button" class="gpv-lbtn" id="gpvBtnYes" onclick="setGpvPowerFilter(true)">🟢 Так, світло є</button>
                        <button type="button" class="gpv-lbtn active-all" id="gpvBtnAll" onclick="setGpvPowerFilter(null)">Усі черги</button>
                    </div>
                    <div id="gpvMemoryHint" class="muted" style="margin:8px 0 4px;font-size:.76rem">Завантаження актуальних даних обленерго...</div>
                    <div id="gpvQueueChips" class="gpv-q-chips"></div>
                    <div id="gpvPreviewBox" class="gpv-preview-box">Очікування графіка...</div>
                </div>
            `,
        }],
        onSave: async () => {
            const settlement = $("gpvSettlement")?.value || "";
            if (!settlement) return "Оберіть населений пункт";
            if (!_gpvModalState.selectedQueue) return "Оберіть підчергу ГПВ";
            const street = ($("gpvStreet")?.value || "").trim();
            const houseNum = ($("gpvHouseNum")?.value || "").trim();

            const updated = await apiFetch(`/api/houses/${h.id}/gpv-bind`, {
                method: "POST",
                body: JSON.stringify({
                    oblast: _gpvModalState.oblast,
                    raion: _gpvModalState.raion,
                    settlement,
                    street,
                    house_num: houseNum,
                    gpv_queue: _gpvModalState.selectedQueue,
                    gpv_auto_sync: true,
                    remember_for_location: true,
                }),
            });
            Object.assign(h, updated);
            addEvent("ok", "Підключено живий графік ГПВ", `${h.address} • Черга ${h.gpv_queue}`);
            if (ctx.onRefresh) await ctx.onRefresh();
            toast(`Підключено чергу ${h.gpv_queue}! Графік оновлюється автоматично`);
        },
    });

    try {
        _gpvModalState.oblasts = await apiFetch("/api/geo/oblasts");
        const obSel = $("gpvOblast");
        if (!obSel) return;
        obSel.innerHTML = _gpvModalState.oblasts.map(o =>
            `<option value="${esc(o.name)}" ${o.name === _gpvModalState.oblast ? "selected" : ""}>${esc(o.name)}${o.name === "м. Київ" ? "" : " область"}${o.has_live_gpv ? " ⚡" : ""}</option>`
        ).join("");
        await onGpvOblastChange(obSel.value, true);
    } catch (e) {
        if ($("gpvMemoryHint")) $("gpvMemoryHint").textContent = "Помилка завантаження довідника: " + e.message;
    }
}

export async function onGpvOblastChange(oblast, keepInitial = false) {
    _gpvModalState.oblast = oblast;
    _gpvModalState.raions = await apiFetch(`/api/geo/raions?oblast=${encodeURIComponent(oblast)}`);
    const raSel = $("gpvRaion");
    if (!raSel) return;
    if (!keepInitial || !_gpvModalState.raions.includes(_gpvModalState.raion)) {
        _gpvModalState.raion = _gpvModalState.raions[0] || "";
    }
    raSel.innerHTML = _gpvModalState.raions.map(r =>
        `<option value="${esc(r)}" ${r === _gpvModalState.raion ? "selected" : ""}>${esc(r)}${r === "м. Київ" ? "" : " район"}</option>`
    ).join("");
    await onGpvRaionChange(_gpvModalState.raion, keepInitial);
}

export async function onGpvRaionChange(raion, keepInitial = false) {
    _gpvModalState.raion = raion;
    if ($("gpvSetSearch")) $("gpvSetSearch").value = "";
    _gpvModalState.settlements = await apiFetch(
        `/api/geo/settlements?oblast=${encodeURIComponent(_gpvModalState.oblast)}&raion=${encodeURIComponent(raion)}`
    );
    populateSettlementSelect(_gpvModalState.settlements, keepInitial);
    await onGpvSettlementChange($("gpvSettlement")?.value || "");
}

function populateSettlementSelect(list, keepInitial = false) {
    const sel = $("gpvSettlement");
    if (!sel) return;
    if (!list.length) {
        sel.innerHTML = `<option value="">— Нічого не знайдено —</option>`;
        return;
    }
    let chosen = _gpvModalState.settlement;
    if (!keepInitial || !list.includes(chosen)) {
        chosen = list.includes("м. Вінниця") ? "м. Вінниця" : list[0];
        _gpvModalState.settlement = chosen;
    }
    sel.innerHTML = list.map(s =>
        `<option value="${esc(s)}" ${s === chosen ? "selected" : ""}>${esc(s)}</option>`
    ).join("");
}

export function onGpvSettlementFilter(query) {
    const q = query.trim().toLowerCase();
    const filtered = q
        ? _gpvModalState.settlements.filter(s => s.toLowerCase().includes(q))
        : _gpvModalState.settlements;
    populateSettlementSelect(filtered, false);
    const val = $("gpvSettlement")?.value || "";
    if (val) onGpvSettlementChange(val);
}

export async function onGpvSettlementChange(settlement) {
    _gpvModalState.settlement = settlement;
    loadStreetSuggestions("");
    await refreshGpvModalLookup();
}

export function onGpvStreetInput(val) {
    _gpvModalState.street = val;
    clearTimeout(_streetDebounce);
    _streetDebounce = setTimeout(() => {
        loadStreetSuggestions(val);
        refreshGpvModalLookup();
    }, 320);
}

async function loadStreetSuggestions(q) {
    if (!_gpvModalState.settlement) return;
    try {
        const streets = await apiFetch(
            `/api/geo/streets?oblast=${encodeURIComponent(_gpvModalState.oblast)}&raion=${encodeURIComponent(_gpvModalState.raion)}&settlement=${encodeURIComponent(_gpvModalState.settlement)}&q=${encodeURIComponent(q || "")}`
        );
        const dl = $("gpvStreetList");
        if (dl) {
            dl.innerHTML = streets.map(st => `<option value="${esc(st)}"></option>`).join("");
        }
    } catch (e) {}
}

export async function setGpvPowerFilter(val) {
    _gpvModalState.hasPowerNow = val;
    $("gpvBtnNo")?.classList.toggle("active-no", val === false);
    $("gpvBtnYes")?.classList.toggle("active-yes", val === true);
    $("gpvBtnAll")?.classList.toggle("active-all", val === null);
    await refreshGpvModalLookup(true);
}

async function refreshGpvModalLookup(autoSwitchQueue = false) {
    const hintEl = $("gpvMemoryHint");
    const chipsEl = $("gpvQueueChips");
    if (!hintEl || !chipsEl) return;

    try {
        const res = await apiFetch("/api/gpv/lookup", {
            method: "POST",
            body: JSON.stringify({
                oblast: _gpvModalState.oblast,
                raion: _gpvModalState.raion,
                settlement: _gpvModalState.settlement,
                street: $("gpvStreet")?.value || "",
                has_power_now: _gpvModalState.hasPowerNow,
            }),
        });
        _gpvModalState.lookup = res;

        const visibleQueues = _gpvModalState.hasPowerNow === null
            ? res.all_queues
            : res.matching_queues;

        if (res.remembered_queue && (visibleQueues.includes(res.remembered_queue) || !autoSwitchQueue)) {
            _gpvModalState.selectedQueue = res.remembered_queue;
        } else if (autoSwitchQueue || !visibleQueues.includes(_gpvModalState.selectedQueue)) {
            if (res.recommended_queue) _gpvModalState.selectedQueue = res.recommended_queue;
        }

        const parts = [];
        if (res.remembered_queue) {
            parts.push(`<b style="color:var(--g)">✓ Збережена черга для цієї адреси: ${esc(res.remembered_queue)}</b>`);
        } else if (res.hint_label && res.hint_queues?.length) {
            parts.push(`Орієнтир (${esc(res.hint_label)}): черги <b>${res.hint_queues.join(", ")}</b>`);
        }
        if (_gpvModalState.hasPowerNow === false) {
            parts.push(visibleQueues.length
                ? `Прямо зараз (${res.kyiv_time}) вимкнені черги: <b>${visibleQueues.join(", ")}</b>`
                : `О ${res.kyiv_time} за планом обленерго всі черги зі світлом (можливе аварійне відключення — оберіть свою чергу нижче)`);
        } else if (_gpvModalState.hasPowerNow === true) {
            parts.push(`Відсіяно вимкнені черги станом на ${res.kyiv_time}`);
        } else {
            parts.push(`Джерело: ${esc(res.provider)} (оновлено: ${esc(res.updated_at || "щойно")})`);
        }
        hintEl.innerHTML = parts.join(" • ");

        const listToRender = visibleQueues.length ? visibleQueues : res.all_queues;
        chipsEl.innerHTML = listToRender.map(q => {
            const det = res.queues_detail?.[q];
            const dot = det?.has_power_now ? "🟢" : "🔴";
            const isSel = q === _gpvModalState.selectedQueue;
            return `<button type="button" class="gpv-q-chip ${isSel ? "sel" : ""}" onclick="selectGpvModalQueue('${esc(q)}')">${dot} ${esc(q)}</button>`;
        }).join("");

        renderGpvModalPreview();
    } catch (e) {
        hintEl.textContent = "Не вдалося отримати живий графік: " + e.message;
    }
}

export function selectGpvModalQueue(q) {
    _gpvModalState.selectedQueue = q;
    document.querySelectorAll("#gpvQueueChips .gpv-q-chip").forEach(btn => {
        btn.classList.toggle("sel", btn.textContent.trim().endsWith(" " + q));
    });
    renderGpvModalPreview();
}

function renderGpvModalPreview() {
    const box = $("gpvPreviewBox");
    const res = _gpvModalState.lookup;
    if (!box || !res) return;
    const q = _gpvModalState.selectedQueue;
    const det = res.queues_detail?.[q];
    if (!det) {
        box.textContent = "Оберіть чергу вище";
        return;
    }
    const statusBadge = det.has_power_now
        ? `<b style="color:var(--g)">🟢 Зараз світло Є</b>`
        : `<b style="color:var(--r)">🔴 Зараз ВІДКЛЮЧЕННЯ</b>`;
    const ivs = det.intervals?.length
        ? `Підтверджені відключення сьогодні: <b>${det.intervals.join(", ")}</b>`
        : `На сьогодні підтверджених відключень <b>немає</b>`;
    box.innerHTML = `
        <div>Черга <b>${esc(q)}</b>: ${statusBadge} • ${esc(det.next_change_text || "")}</div>
        <div style="margin-top:4px">${ivs}</div>`;
}

export async function forceSyncHouseGpv(silent = false) {
    const h = activeHouse();
    if (!h || !h.gpv_auto_sync || !h.gpv_queue) return;
    try {
        const updated = await apiFetch(`/api/houses/${h.id}/gpv-sync?force=${silent ? "false" : "true"}`);
        const changed = Boolean(updated.live_gpv?.schedule_changed);
        Object.assign(h, updated);
        if (changed) {
            addEvent("info", "Оновлено графік обленерго", `Черга ${h.gpv_queue}: графік відключень скориговано автоматично`);
            if (ctx.onRefresh) await ctx.onRefresh();
            toast(`⚡ Обленерго оновило фактичний графік (Черга ${h.gpv_queue})!`);
        } else {
            if (ctx.onRenderShell) ctx.onRenderShell();
            renderOutagePanel(h);
            if (!silent) toast(`Перевірено: графік Черги ${h.gpv_queue} актуальний`);
        }
    } catch (e) {
        if (!silent) toast("Помилка синхронізації: " + e.message);
    }
}

export function startGpvAutoSyncTimer() {
    setInterval(() => {
        if (document.visibilityState === "visible" && ctx.state.auth?.token) {
            const h = activeHouse();
            if (h?.gpv_auto_sync && h?.gpv_queue) {
                forceSyncHouseGpv(true);
            }
        }
    }, 90_000);
}
