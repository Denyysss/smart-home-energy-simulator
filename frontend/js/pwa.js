/**
 * js/pwa.js — Підтримка встановлення додатку на смартфон (PWA для iOS/Android + завантаження EnergyDim.apk з GitHub).
 */

import { $, ctx, isNativeMobileWrapper, lucideTag, toast } from "./api.js";

export const GITHUB_APK_RELEASE_URL = "https://github.com/Denyysss/smart-home-energy-simulator/releases/latest";

let deferredInstallPrompt = null;

export function initPwaAndMobileSupport() {
    if (isNativeMobileWrapper()) {
        // Усередині встановленого Android APK ховаємо кнопки встановлення
        const topBtn = $("installAppTopBtn");
        const authBtn = $("authMobileBtn");
        if (topBtn) topBtn.style.display = "none";
        if (authBtn) authBtn.style.display = "none";
        return;
    }

    if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost" || location.hostname === "127.0.0.1")) {
        navigator.serviceWorker.register("/sw.js").catch(() => {});
    }

    window.addEventListener("beforeinstallprompt", e => {
        e.preventDefault();
        deferredInstallPrompt = e;
    });

    window.addEventListener("appinstalled", () => {
        deferredInstallPrompt = null;
        toast("Додаток ЕнергіяДім встановлено на пристрій!");
    });
}

export async function openMobileAppModal() {
    const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);
    const hasNativePrompt = Boolean(deferredInstallPrompt);

    const pwaSection = hasNativePrompt
        ? `<button type="button" class="btn-y btn-ic" style="width:100%;justify-content:center;margin-top:8px" onclick="triggerPwaInstall()">${lucideTag("smartphone")}<span>Встановити веб-додаток зараз (1 клік)</span></button>`
        : isIOS
            ? `<div class="gpv-preview-box" style="margin-top:8px"><b>Для iPhone / iPad (Safari):</b><br>1. Натисніть кнопку <b>«Поділитися»</b> (квадрат зі стрілкою вгору внизу браузера).<br>2. Оберіть <b>«На початковий екран»</b> → <b>«Додати»</b>.</div>`
            : `<div class="gpv-preview-box" style="margin-top:8px"><b>Встановлення через браузер (PWA):</b><br>У меню браузера (⋮ у верхньому кутку Chrome / Opera) натисніть <b>«Встановити додаток»</b> або <b>«Додати на головний екран»</b>.</div>`;

    ctx.openForm({
        title: "Мобільний додаток ЕнергіяДім",
        hideSave: true,
        fields: [{
            type: "html",
            key: "mob",
            html: `
                <div class="muted" style="margin:4px 0 10px">
                    Користуйтесь <b>ЕнергіяДім</b> на смартфоні двома способами — як нативним Android-пакетом <code>.apk</code> з GitHub або як PWA-додатком (працює і на iPhone, і на Android).
                </div>
                <div class="card" style="padding:14px;background:var(--inp)">
                    <b class="inline-ic">${lucideTag("download", "ic-g")} Варіант 1: Скачати Android APK з GitHub</b>
                    <small class="muted" style="margin:6px 0 10px">Повноцінний інсталяційний файл <code>EnergyDim.apk</code> (автоматично зібраний через GitHub Actions і підключений до хмарної БД).</small>
                    <a href="${GITHUB_APK_RELEASE_URL}" target="_blank" rel="noopener" class="btn-y btn-ic" style="width:100%;justify-content:center;text-decoration:none">
                        ${lucideTag("github")}<span>Завантажити EnergyDim.apk (GitHub Releases)</span>
                    </a>
                </div>
                <div class="card" style="padding:14px;background:var(--inp);margin-top:10px">
                    <b class="inline-ic">${lucideTag("sparkles", "ic-b")} Варіант 2: Встановити PWA (iPhone + Android)</b>
                    <small class="muted" style="margin:6px 0 4px">Відкривається на весь екран без адресного рядка браузера, не потребує дозволу на невідомі джерела.</small>
                    ${pwaSection}
                </div>
            `,
        }],
    });
}

export async function triggerPwaInstall() {
    if (!deferredInstallPrompt) {
        toast("Скористайтесь меню браузера → «Встановити додаток»");
        return;
    }
    deferredInstallPrompt.prompt();
    const choice = await deferredInstallPrompt.userChoice;
    if (choice.outcome === "accepted") {
        deferredInstallPrompt = null;
        if (ctx.closeModal) ctx.closeModal();
    }
}
