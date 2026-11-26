/**
 * js/charts.js — Побудова всіх графіків Chart.js з ледачим рендером (лише для активної вкладки).
 */

import { $, L24, NIGHT, PALETTE, activeHouse, ctx, estimate, fmt, limitOf } from "./api.js";

const charts = {};

function mkChart(id, cfgFactory) {
    const c = $(id);
    if (!c || !c.offsetParent) return;
    if (charts[id]) {
        charts[id].destroy();
        delete charts[id];
    }
    charts[id] = new Chart(c, typeof cfgFactory === "function" ? cfgFactory() : cfgFactory);
}

const axes = (ytitle, max) => ({
    y: {
        beginAtZero: true,
        max: max || undefined,
        grid: { color: "rgba(255,255,255,.05)" },
        ticks: { color: "#64748B", font: { size: 10 } },
        title: { display: true, text: ytitle, color: "#64748B" },
    },
    x: {
        grid: { display: false },
        ticks: { color: "#64748B", font: { size: 9 }, maxRotation: 55 },
    },
});

function hourColors() {
    const over = new Set((ctx.sim?.overload_hours || []).map(o => o.hour));
    return L24.map((_, i) => (over.has(i) ? "#EF4444" : NIGHT.has(i) ? "#818CF8" : "#FACC15"));
}

function limitAnn(L) {
    if (!ctx.state.settings.showLimit) return {};
    return {
        limit: {
            type: "line",
            yMin: L,
            yMax: L,
            borderColor: "#EF4444",
            borderWidth: 2,
            borderDash: [6, 4],
            label: {
                display: true,
                content: `Ліміт ${L} Вт`,
                position: "start",
                backgroundColor: "rgba(239,68,68,.85)",
                color: "#fff",
                font: { size: 10 },
                padding: 3,
            },
        },
    };
}

function legendHtml() {
    return (
        `<span><i style="background:#FACC15"></i>День</span>` +
        `<span><i style="background:#818CF8"></i>Ніч</span>` +
        `<span><i style="background:#EF4444"></i>Перевантаження</span>` +
        (ctx.state.settings.showLimit ? `<span><i class="ln"></i>Ліміт автомата</span>` : "")
    );
}

export function drawAll() {
    const page = document.body.dataset.page || "home";
    const h = activeHouse();

    if (page === "home" || page === "charts") {
        document.querySelectorAll(".legend").forEach(e => { e.innerHTML = legendHtml(); });
        document.querySelectorAll(".legend2").forEach(e => {
            e.innerHTML = `<span><i style="background:#22C55E"></i>День (${h.dayRate} грн)</span><span><i style="background:#818CF8"></i>Ніч (${h.nightRate} грн)</span>`;
        });
    }

    // Вкладка «Мої будинки» не залежить від sim
    if (page === "houses") {
        const hs = ctx.state.houses;
        const ests = hs.map(estimate);
        mkChart("chartHouses", () => ({
            type: "bar",
            data: {
                labels: hs.map(x => x.name),
                datasets: [
                    { label: "кВт·год/доба", data: ests.map(e => +e.kwh.toFixed(2)), backgroundColor: "#FACC15", borderRadius: 4 },
                    { label: "грн/доба", data: ests.map(e => +e.cost.toFixed(2)), backgroundColor: "#38BDF8", borderRadius: 4 },
                ],
            },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { labels: { color: "#94A3B8", boxWidth: 10 } } }, scales: axes("") },
        }));
        return;
    }

    const sim = ctx.sim;
    if (!sim) return;

    const L = limitOf(h);
    const ymax = ctx.state.settings.chartMax;
    const outSet = new Set(sim.outage_hours || h.outageHours || []);
    const tip = {
        callbacks: {
            label: c => `${fmt(c.parsed.y, 0)} Вт`,
            afterLabel: c => (outSet.has(c.dataIndex) ? "Відключення світла (ГПВ)" : NIGHT.has(c.dataIndex) ? "Ніч" : "День"),
        },
    };
    const hourlyCfg = () => ({
        type: "bar",
        data: { labels: L24, datasets: [{ data: sim.hourly_watt, backgroundColor: hourColors(), borderRadius: 4, barPercentage: 0.75 }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, annotation: { annotations: limitAnn(L) }, tooltip: tip }, scales: axes("Вт", ymax) },
    });

    if (page === "home") {
        mkChart("chartHome", hourlyCfg);
        let dk = 0, nk = 0;
        sim.hourly_kwh.forEach((k, i) => (NIGHT.has(i) ? (nk += k) : (dk += k)));
        mkChart("chartDN", () => ({
            type: "doughnut",
            data: { labels: ["День", "Ніч"], datasets: [{ data: [+dk.toFixed(3), +nk.toFixed(3)], backgroundColor: ["#FACC15", "#818CF8"], borderColor: "#141F35", borderWidth: 2 }] },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "right", labels: { color: "#94A3B8", boxWidth: 10 } }, tooltip: { callbacks: { label: c => `${c.label}: ${fmt(c.parsed)} кВт·год` } } } },
        }));
    } else if (page === "charts") {
        const n = ctx.range === "week" ? 7 : 30;
        if ($("rangeNote")) {
            $("rangeNote").textContent = ctx.range === "day"
                ? "Споживання по годинах за добу."
                : `Прогноз за поточним розкладом: кожна доба = ${fmt(sim.total_kwh)} кВт·год.`;
        }
        if (ctx.range === "day") {
            mkChart("chartWatt", hourlyCfg);
        } else {
            mkChart("chartWatt", () => ({
                type: "bar",
                data: { labels: Array.from({ length: n }, (_, i) => `${i + 1}`), datasets: [{ data: Array(n).fill(sim.total_kwh), backgroundColor: "#FACC15", borderRadius: 4 }] },
                options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => `${fmt(c.parsed.y)} кВт·год` } } }, scales: axes("кВт·год") },
            }));
        }

        mkChart("chartCost", () => ({
            type: "bar",
            data: { labels: L24, datasets: [{ data: sim.hourly_cost, backgroundColor: L24.map((_, i) => (NIGHT.has(i) ? "#818CF8" : "#22C55E")), borderRadius: 4, barPercentage: 0.75 }] },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => `${fmt(c.parsed.y, 3)} грн` } } }, scales: axes("грн") },
        }));

        mkChart("chartStack", () => ({
            type: "bar",
            data: { labels: L24, datasets: sim.per_appliance.map((p, i) => ({ label: p.name, data: p.hourly_watt, backgroundColor: PALETTE[i % PALETTE.length], borderRadius: 2 })) },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { labels: { color: "#94A3B8", boxWidth: 10, font: { size: 10 } } }, annotation: { annotations: limitAnn(L) } },
                scales: { x: { ...axes("").x, stacked: true }, y: { ...axes("Вт", ymax).y, stacked: true } },
            },
        }));

        mkChart("chartLine", () => ({
            type: "line",
            data: { labels: L24, datasets: [{ data: sim.hourly_watt, borderColor: "#38BDF8", backgroundColor: "rgba(56,189,248,.18)", fill: true, tension: 0.3, pointRadius: 2 }] },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, annotation: { annotations: limitAnn(L) }, tooltip: { callbacks: { label: c => `${fmt(c.parsed.y, 0)} Вт` } } }, scales: axes("Вт", ymax) },
        }));

        let run = 0;
        const cum = sim.hourly_cost.map(v => +(run += v).toFixed(3));
        mkChart("chartCum", () => ({
            type: "line",
            data: { labels: L24, datasets: [{ data: cum, borderColor: "#22C55E", backgroundColor: "rgba(34,197,94,.18)", fill: true, tension: 0.25, pointRadius: 2 }] },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => `${fmt(c.parsed.y)} грн` } } }, scales: axes("грн") },
        }));
    } else if (page === "analytics") {
        const rows = ctx.rows || [];
        mkChart("chartShare", () => ({
            type: "doughnut",
            data: { labels: rows.map(r => r.name), datasets: [{ data: rows.map(r => +r.kwh.toFixed(3)), backgroundColor: rows.map((_, i) => PALETTE[i % PALETTE.length]), borderColor: "#141F35", borderWidth: 2 }] },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "right", labels: { color: "#94A3B8", boxWidth: 10, font: { size: 11 } } }, tooltip: { callbacks: { label: c => `${c.label}: ${fmt(c.parsed)} кВт·год` } } } },
        }));
    }
}
