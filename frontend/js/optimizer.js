/**
 * js/optimizer.js — Розумний оптимізатор розкладу, пресети, окупність лічильника, СЕС 1.5 кВт, PDF-паспорт та експорт.
 */

import { $, activeHouse, addEvent, apiFetch, ctx, defaultState, esc, fmt, limitOf, lucideTag, save, shortHours, toast } from "./api.js";

function overloadLabel(n) {
    if (n === 0) return "0 перевантажень";
    if (n === 1) return "1 перевантаження";
    if (n >= 2 && n <= 4) return `${n} перевантаження`;
    return `${n} перевантажень`;
}

export async function fetchOptimization() {
    const h = activeHouse();
    const on = h.appliances.filter(a => a.enabled);
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
        : `<div class="opt-change-row"><span style="color:#86EFAC">Розклад уже максимально оптимізовано! Усі гнучкі потужні прилади працюють у нічній зоні (23:00–07:00) без перевантажень.</span></div>`;
    return { headline, rowsHtml, hasChanges: changed.length > 0 };
}

export async function previewOptimization(userClicked = false) {
    try {
        const opt = await fetchOptimization();
        if (!opt) {
            ctx.lastOptimization = null;
            if ($("optResult")) $("optResult").style.display = "none";
            if ($("btnApplyOpt")) $("btnApplyOpt").style.display = "none";
            if (userClicked) toast("Спочатку додайте та увімкніть прилади");
            return;
        }
        ctx.lastOptimization = opt;
        const { headline, rowsHtml, hasChanges } = buildOptSummaryHtml(opt);
        $("optHeadline").innerHTML = headline;
        $("optChanges").innerHTML = rowsHtml;
        $("optResult").style.display = "block";
        $("btnApplyOpt").style.display = hasChanges ? "" : "none";
        if (userClicked) {
            if (!$("view-home").classList.contains("active") && ctx.onSetView) ctx.onSetView("home");
            $("optCard").scrollIntoView({ behavior: "smooth", block: "center" });
            if (!hasChanges) toast("Розклад уже оптимальний!");
        }
    } catch (e) {
        if (userClicked) toast("Помилка оптимізації: " + e.message);
    }
}

export async function openOptimizeModal() {
    try {
        const opt = await fetchOptimization();
        if (!opt) {
            toast("Додайте та увімкніть прилади");
            return;
        }
        ctx.lastOptimization = opt;
        const { headline, rowsHtml, hasChanges } = buildOptSummaryHtml(opt);
        ctx.openForm({
            title: "Розумний оптимізатор розкладу",
            saveText: "Застосувати",
            hideSave: !hasChanges,
            fields: [{
                type: "html",
                key: "opt",
                html: `<div class="opt-headline" style="margin-top:10px">${headline}</div><div class="opt-changes" style="margin-top:12px">${rowsHtml}</div>`,
            }],
            onSave: async () => { await applyOptimization(); },
        });
    } catch (e) {
        toast(e.message);
    }
}

export async function applyOptimization() {
    const h = activeHouse();
    try {
        const res = await apiFetch(`/api/houses/${h.id}/optimize?apply=true`, { method: "POST" });
        if (res.house) {
            Object.assign(h, res.house);
        } else if (ctx.lastOptimization) {
            const byId = new Map(ctx.lastOptimization.changes.map(c => [c.id, c.new_schedule]));
            h.appliances.forEach(a => { if (byId.has(a.id)) a.schedule = byId.get(a.id); });
        }
        addEvent("ok", "Розклад оптимізовано", `Після: ${fmt(res.after.total_cost)} грн/доба (економія ${fmt(res.saved_monthly_uah, 0)} грн/міс)`);
        if (ctx.onRefresh) await ctx.onRefresh();
        toast(`Застосовано! Економія ${fmt(res.saved_monthly_uah, 0)} грн/міс`);
    } catch (e) {
        toast("Не вдалося застосувати: " + e.message);
    }
}

export async function applyPreset(presetKey) {
    const h = activeHouse();
    const names = { workday: "Робочий день", weekend: "Вихідний", winter: "Зима (Обігрів)", summer: "Літо (Кондиціонер)", eco: "Еко-ніч" };
    try {
        const updated = await apiFetch(`/api/houses/${h.id}/preset`, {
            method: "POST",
            body: JSON.stringify({ preset: presetKey }),
        });
        Object.assign(h, updated);
        addEvent("ok", "Завантажено сценарій", names[presetKey] || presetKey);
        if (ctx.onRefresh) await ctx.onRefresh();
        toast(`Застосовано сценарій: ${names[presetKey] || presetKey}`);
    } catch (e) {
        toast("Не вдалося завантажити сценарій: " + e.message);
    }
}

export function renderMeterAndSolar() {
    const sim = ctx.sim;
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
        <div class="tip" style="margin-top:10px;border-top:1px solid var(--bd);border-bottom:none">${lucideTag("gauge", "ic-p")}<span>${pb}</span></div>`;

    const yrSave = tc.solar_1_5kw_monthly_save_uah * 12;
    $("aSolarCard").innerHTML = `
        <small class="muted">Оцінка покриття денного навантаження (08:00–17:00) сонячною панеллю 1.5 кВт.</small>
        <div class="stats">
            <div class="stat"><b style="color:var(--y)">${fmt(tc.solar_1_5kw_daily_covered_kwh, 2)} кВт·год</b><small>покриває СЕС за добу</small></div>
            <div class="stat"><b style="color:var(--g)">${fmt(tc.solar_1_5kw_monthly_save_uah, 0)} грн</b><small>економія за місяць</small></div>
            <div class="stat"><b style="color:var(--b)">${fmt(yrSave, 0)} грн</b><small>економія за рік</small></div>
        </div>`;
}

export function downloadPdfReport() {
    const sim = ctx.sim;
    if (!sim) {
        toast("Немає даних для звіту");
        return;
    }
    const h = activeHouse();
    const tc = sim.tariff_comparison || {};
    const bk = sim.outage_backup || {};
    const nowStr = new Date().toLocaleString("uk-UA");
    const rows = (ctx.rows || []).map(r =>
        `<tr><td>${esc(r.name)}</td><td>${fmt(r.power, 0)} Вт</td><td>${fmt(r.kwh, 2)} кВт·год</td><td>${fmt(r.cost, 2)} грн</td><td>${fmt(r.cost * 30, 0)} грн</td></tr>`
    ).join("");

    $("printReport").innerHTML = `
        <h1>Паспорт енергоспоживання будинку — «${esc(h.name)}»</h1>
        <div class="pr-meta">Адреса: ${esc(h.address || "—")} • Власник: ${esc(ctx.state.profile.name)} • Сформовано: ${nowStr}</div>
        <div class="pr-kpis">
            <div class="pr-kpi"><b>${fmt(sim.total_kwh)} кВт·год</b><span>Споживання за добу</span></div>
            <div class="pr-kpi"><b>${fmt(sim.total_cost)} грн</b><span>Вартість за добу</span></div>
            <div class="pr-kpi"><b>${fmt(sim.peak_watt, 0)} / ${limitOf(h)} Вт</b><span>Пік (о ${sim.peak_hour}:00) / Ліміт</span></div>
            <div class="pr-kpi"><b>${fmt(tc.monthly_2zone ?? sim.total_cost * 30, 0)} грн/міс</b><span>Прогноз (2-зонний тариф)</span></div>
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

export function exportData() {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify(ctx.state, null, 2)], { type: "application/json" }));
    a.download = "energydim-backup.json";
    a.click();
    URL.revokeObjectURL(a.href);
    toast("Файл збережено");
}

export function initBackupImportListener() {
    const inp = $("importFile");
    if (!inp) return;
    inp.addEventListener("change", e => {
        const f = e.target.files[0];
        if (!f) return;
        const r = new FileReader();
        r.onload = () => {
            try {
                const s = JSON.parse(r.result);
                if (!Array.isArray(s.houses) || !s.houses.length) throw new Error("bad");
                ctx.state = { ...defaultState(), ...s };
                save();
                if (ctx.onRefresh) ctx.onRefresh();
                toast("Імпортовано");
            } catch (err) {
                toast("Некоректний файл");
            }
        };
        r.readAsText(f);
        e.target.value = "";
    });
}
