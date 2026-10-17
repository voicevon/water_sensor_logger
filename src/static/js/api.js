/**
 * api.js - 后端 REST API 请求与 SSE 实时流交互模块
 */

export function todayLocal() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export function photoToMs(ts) {
    return new Date(ts.replace(' ', 'T')).getTime();
}

export async function fetchDates() {
    try {
        const res = await fetch('/api/available_dates');
        const dates = await res.json();
        const select = document.getElementById('date-select');
        if (!select) return [];
        select.innerHTML = '';
        
        if (dates.length === 0) {
            const today = new Date().toISOString().split('T')[0];
            select.innerHTML = `<option value="${today}">${today} (无数据)</option>`;
            return [];
        }
        
        dates.forEach(date => {
            select.innerHTML += `<option value="${date}">${date}</option>`;
        });
        return dates;
    } catch (err) {
        console.error("加载日期失败:", err);
        return [];
    }
}

export async function fetchPhotos(date) {
    try {
        const res = await fetch(`/api/photos?date=${date}`);
        return await res.json();
    } catch (err) {
        console.error("加载照片失败:", err);
        return [];
    }
}

export async function fetchStationConfig() {
    try {
        const res = await fetch('/api/station_config');
        return await res.json();
    } catch (err) {
        console.error("加载站点配置失败:", err);
        return null;
    }
}

export async function saveStationConfig(stationName) {
    const res = await fetch('/api/station_config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ station_name: stationName, sample_interval: 1 })
    });
    return await res.json();
}

export async function requestImmediateCapture() {
    const res = await fetch('/api/capture', { method: 'POST' });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || 'capture failed');
    return data;
}

export async function loadAutoCaptureState() {
    try {
        const res = await fetch('/api/auto_capture');
        return await res.json();
    } catch (err) {
        console.error("加载定时拍摄状态失败:", err);
        return { auto_capture: false };
    }
}

export async function setAutoCaptureState(enabled) {
    const res = await fetch(`/api/auto_capture?enabled=${enabled ? 'on' : 'off'}`, { method: 'POST' });
    return await res.json();
}

export async function fetchHistoryData(queryParams) {
    const query = new URLSearchParams(queryParams).toString();
    const res = await fetch(`/api/history?${query}`);
    return await res.json();
}

export function createEventSourceStream(queryParams) {
    const query = new URLSearchParams(queryParams).toString();
    return new EventSource(`/api/stream?${query}`);
}
