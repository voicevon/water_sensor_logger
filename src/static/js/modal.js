/**
 * modal.js - 控制中心弹窗与照片弹窗管理模块
 */

export function openSettingsModal(targetTab = 'all') {
    const modal = document.getElementById('settings-modal');
    if (!modal) return;
    modal.style.display = 'flex';
    requestAnimationFrame(() => {
        modal.classList.add('open');
    });
    switchSettingsTab(targetTab);
}

export function closeSettingsModal() {
    const modal = document.getElementById('settings-modal');
    if (!modal) return;
    modal.classList.remove('open');
    setTimeout(() => {
        modal.style.display = 'none';
    }, 250);
}

export function onBackdropClick(event) {
    if (event.target && event.target.id === 'settings-modal') {
        closeSettingsModal();
    }
}

export function switchSettingsTab(tab) {
    const tabs = ['all', 'station', 'camera', 'chart'];
    tabs.forEach(t => {
        const btn = document.getElementById(`tab-btn-${t}`);
        if (btn) btn.classList.toggle('active', t === tab);
    });

    const sections = {
        station: document.getElementById('sec-station'),
        camera: document.getElementById('sec-camera'),
        live: document.getElementById('sec-live'),
        chart: document.getElementById('sec-chart')
    };

    if (tab === 'all') {
        Object.values(sections).forEach(sec => { if (sec) sec.style.display = 'block'; });
    } else if (tab === 'station') {
        if (sections.station) sections.station.style.display = 'block';
        if (sections.camera) sections.camera.style.display = 'none';
        if (sections.live) sections.live.style.display = 'none';
        if (sections.chart) sections.chart.style.display = 'none';
    } else if (tab === 'camera') {
        if (sections.station) sections.station.style.display = 'none';
        if (sections.camera) sections.camera.style.display = 'block';
        if (sections.live) sections.live.style.display = 'block';
        if (sections.chart) sections.chart.style.display = 'none';
    } else if (tab === 'chart') {
        if (sections.station) sections.station.style.display = 'none';
        if (sections.camera) sections.camera.style.display = 'none';
        if (sections.live) sections.live.style.display = 'none';
        if (sections.chart) sections.chart.style.display = 'block';
    }
}

export function openPhotoModal(url, ts) {
    const img = document.getElementById('photo-modal-img');
    const timeEl = document.getElementById('photo-modal-time');
    const modal = document.getElementById('photo-modal');
    if (img) img.src = url;
    if (timeEl) timeEl.textContent = ts;
    if (modal) modal.classList.add('open');
}

export function closePhotoModal() {
    const modal = document.getElementById('photo-modal');
    if (modal) modal.classList.remove('open');
}

export function showPhotoHover(params, allPhotos = []) {
    const photo = allPhotos.find(p => p.timestamp === params.name);
    if (!photo) return;
    const el = document.getElementById('photo-hover');
    if (!el) return;
    const img = document.getElementById('photo-hover-img');
    const timeEl = document.getElementById('photo-hover-time');
    if (img) img.src = photo.url;
    if (timeEl) timeEl.textContent = photo.timestamp;
    el.classList.add('open');

    // 定位到鼠标附近，超出视口时翻转方向
    const domEvt = params.event && params.event.event;
    const x = domEvt ? domEvt.clientX : 100;
    const y = domEvt ? domEvt.clientY : 100;
    const rect = el.getBoundingClientRect();
    let left = x + 16;
    let top = y + 16;
    if (left + rect.width > window.innerWidth - 8) left = x - rect.width - 16;
    if (top + rect.height > window.innerHeight - 8) top = y - rect.height - 16;
    el.style.left = Math.max(8, left) + 'px';
    el.style.top = Math.max(8, top) + 'px';
}

export function hidePhotoHover() {
    const el = document.getElementById('photo-hover');
    if (el) el.classList.remove('open');
}

export function initModalListeners() {
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            closeSettingsModal();
            closePhotoModal();
        }
    });
}
