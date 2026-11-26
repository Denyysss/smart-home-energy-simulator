/**
 * js/auth.js — Вхід, реєстрація, одноразовий пустий гостьовий кабінет,
 * автопауза фонового відео після входу та автовидалення гостя при закритті вкладки.
 */

import { $, apiFetch, apiUrl, ctx, defaultState, renderIcons, save, toast } from "./api.js";

let authMode = "login";
let _guestDestroySent = false;

function syncAuthVideoPlayback(visible) {
    const vid = $("auth-background");
    if (!vid) return;
    if (visible) {
        vid.play().catch(() => {});
    } else {
        // Зупиняємо декодування фонового відео в робочому кабінеті — 0% навантаження на GPU/CPU
        vid.pause();
    }
}

export function switchAuthTab(mode) {
    authMode = mode;
    $("authTabLogin").classList.toggle("active", mode === "login");
    $("authTabReg").classList.toggle("active", mode === "register");
    $("authNameWrap").style.display = mode === "register" ? "block" : "none";
    const btnSpan = $("authSubmitBtn").querySelector("span");
    if (btnSpan) btnSpan.textContent = mode === "register" ? "Створити акаунт" : "Увійти в систему";
    $("authErr").style.display = "none";
}

export function showAuthScreen() {
    $("authErr").style.display = "none";
    $("authScreen").classList.add("show");
    syncAuthVideoPlayback(true);
    renderIcons($("authScreen"));
}

export function hideAuthScreen() {
    $("authScreen").classList.remove("show");
    syncAuthVideoPlayback(false);
}

export async function submitAuth(e) {
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

    const submitBtn = $("authSubmitBtn");
    submitBtn.disabled = true;

    try {
        const endpoint = authMode === "register" ? "/api/auth/register" : "/api/auth/login";
        const body = authMode === "register" ? { name, username, password } : { username, password };
        const res = await apiFetch(endpoint, { method: "POST", body: JSON.stringify(body) });
        ctx.state.auth = { token: res.token, user: res.user };
        ctx.state.profile.name = res.user.name;
        ctx.state.events = [];
        ctx.lastOverKey = null;
        save();
        hideAuthScreen();
        await initFromDb();
        toast(`Вітаємо, ${res.user.name}!`);
    } catch (err) {
        $("authErr").textContent = err.message;
        $("authErr").style.display = "block";
    } finally {
        submitBtn.disabled = false;
    }
}

export async function quickDemoLogin() {
    $("authErr").style.display = "none";
    const demoBtn = $("authDemoBtn");
    demoBtn.disabled = true;

    try {
        const res = await apiFetch("/api/auth/guest", { method: "POST" });
        _guestDestroySent = false;
        ctx.state.auth = { token: res.token, user: res.user };
        ctx.state.profile.name = res.user.name;
        ctx.state.houses = defaultState().houses;
        ctx.state.events = [];
        ctx.lastOverKey = null;
        ctx.lastOptimization = null;
        if ($("optResult")) $("optResult").style.display = "none";
        save();
        hideAuthScreen();
        await initFromDb();
        toast(`Відкрито пустий гостьовий кабінет (${res.user.name})`);
    } catch (err) {
        $("authErr").textContent = err.message;
        $("authErr").style.display = "block";
    } finally {
        demoBtn.disabled = false;
    }
}

export async function logoutUser() {
    const token = ctx.state.auth?.token || null;
    const wasGuest = Boolean(ctx.state.auth?.user?.is_guest);
    _guestDestroySent = true;

    const savedSettings = ctx.state.settings;
    ctx.state = defaultState();
    ctx.state.settings = savedSettings;
    ctx.lastOverKey = null;
    ctx.lastOptimization = null;
    ctx.sim = null;
    if ($("optResult")) $("optResult").style.display = "none";
    save();
    if (ctx.onRenderShell) ctx.onRenderShell();
    showAuthScreen();

    if (token) {
        try {
            await fetch(apiUrl("/api/auth/logout"), {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    Authorization: "Bearer " + token,
                },
                body: JSON.stringify({ token }),
                keepalive: true,
            });
        } catch (e) {}
    }

    toast(wasGuest ? "Гостьовий акаунт видалено з бази даних" : "Ви вийшли з акаунта");
}

function _destroyGuestOnClose() {
    if (_guestDestroySent) return;
    if (!ctx.state.auth?.user?.is_guest || !ctx.state.auth?.token) return;
    _guestDestroySent = true;
    const token = ctx.state.auth.token;
    ctx.state.auth = null;
    save();
    const url = apiUrl("/api/auth/logout");
    const payload = JSON.stringify({ token });
    if (navigator.sendBeacon) {
        navigator.sendBeacon(url, payload);
    } else {
        fetch(url, { method: "POST", body: payload, keepalive: true }).catch(() => {});
    }
}

export function initGuestUnloadCleanup() {
    ctx.onShowAuth = showAuthScreen;
    window.addEventListener("pagehide", _destroyGuestOnClose);
}

export async function initFromDb() {
    if (!ctx.state.auth || !ctx.state.auth.token) {
        showAuthScreen();
        return;
    }
    try {
        const [meData, catData, housesData] = await Promise.all([
            apiFetch("/api/auth/me"),
            apiFetch("/api/catalog"),
            apiFetch("/api/houses"),
        ]);
        ctx.state.auth.user = meData;
        ctx.state.profile.name = meData.name;
        ctx.catalog = catData;
        if (Array.isArray(housesData) && housesData.length > 0) {
            ctx.state.houses = housesData;
            if (!ctx.state.houses.some(h => h.id === ctx.state.activeHouse)) {
                ctx.state.activeHouse = ctx.state.houses[0].id;
            }
        }
        hideAuthScreen();
        $("apiErr").style.display = "none";
        if (ctx.onRefresh) await ctx.onRefresh();
    } catch (e) {
        if (!ctx.state.auth || !ctx.state.auth.token) return;
        $("apiErr").style.display = "block";
    }
}
