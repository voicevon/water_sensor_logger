/**
 * app.js - 主入口模块与核心状态协调器
 */

import {
    openSettingsModal,
    closeSettingsModal,
    onBackdropClick,
    switchSettingsTab,
    openPhotoModal,
    closePhotoModal,
    showPhotoHover,
    hidePhotoHover,
    initModalListeners
} from './modal.js';

import {
    todayLocal,
    fetchDates,
    fetchPhotos as apiFetchPhotos,
    fetchStationConfig,
    saveStationConfig as apiSaveStationConfig,
    doCapture,
    loadAutoCaptureState,
    setAutoCaptureState,
    fetchHistoryData,
    createEventSourceStream
} from './api.js';

import {
    initChartDom,
    renderChart as chartRender,
    appendToChart as chartAppend,
    autoScrollDataZoom,
    applyYScaleToChart,
    lockCurrentYBounds,
    refreshPhotoMarks,
    updateMetrics
} from './chart.js';

// ============================================================
//  全局响应状态
// ============================================================
let currentChannel = 1;
let myChart = null;
let cachedData = null;

// 实时模式状态
let isLiveMode = false;
let eventSource = null;
let liveAppendCount = 0;
let liveData = null;

// 照片记录状态
let allPhotos = [];
let currentPhotos = [];
let currentPhotosTotal = 0;
let photoPollTimer = null;

// X 轴与 Y 轴显示状态
let autoScrollX = false;
let isYScaleLocked = false;
let lockedYMin = null;
let lockedYMax = null;
let showChartPhotos = true;

// ============================================================
//  UI 状态同步辅助
// ============================================================
function getChartRenderState() {
    return {
        currentChannel,
        algorithm: document.getElementById('algo-select') ? document.getElementById('algo-select').value : 'dynamic',
        isYScaleLocked,
        lockedYMin,
        lockedYMax,
        isLiveMode,
        autoScrollX,
        showChartPhotos,
        allPhotos,
        envUpperOffset: parseFloat(document.getElementById('env-upper-offset-slider')?.value || 5000),
        envLowerOffset: parseFloat(document.getElementById('env-lower-offset-slider')?.value || 3000)
    };
}

export function setAutoScroll(enabled) {
    autoScrollX = !!enabled;
    updateScrollUI();
    saveConfig();
    
    if (myChart) {
        const option = myChart.getOption();
        const totalLen = (option.xAxis && option.xAxis[0].data) ? option.xAxis[0].data.length : 0;
        autoScrollDataZoom(myChart, totalLen, autoScrollX);
    }
}

export function toggleAutoScroll() {
    setAutoScroll(!autoScrollX);
}

export function updateScrollUI() {
    const btn = document.getElementById('btn-x-scroll');
    const text = document.getElementById('btn-x-scroll-text');
    const toggle = document.getElementById('sidebar-x-scroll-toggle');
    const desc = document.getElementById('sidebar-x-desc');

    if (autoScrollX) {
        if (btn) {
            btn.classList.remove('active');
            btn.classList.add('scrolling');
        }
        if (text) text.innerText = 'X轴自动滚屏(300点)';
        if (toggle) toggle.checked = true;
        if (desc) desc.innerText = '开: 追踪最新300点';
    } else {
        if (btn) {
            btn.classList.add('active');
            btn.classList.remove('scrolling');
        }
        if (text) text.innerText = '左侧固定(全景压缩)';
        if (toggle) toggle.checked = false;
        if (desc) desc.innerText = '关: 左侧固定全景压缩';
    }
}

export function setYScaleLock(locked) {
    isYScaleLocked = !!locked;
    if (isYScaleLocked) {
        const bounds = lockCurrentYBounds(isLiveMode && liveData ? liveData : cachedData, currentChannel);
        lockedYMin = bounds.min;
        lockedYMax = bounds.max;
    } else {
        lockedYMin = null;
        lockedYMax = null;
    }
    updateYScaleUI();
    saveConfig();
    applyYScaleToChart(myChart, isYScaleLocked, lockedYMin, lockedYMax);
}

export function toggleYScaleLock() {
    setYScaleLock(!isYScaleLocked);
}

export function updateYScaleUI() {
    const btn = document.getElementById('btn-y-scale');
    const icon = document.getElementById('btn-y-scale-icon');
    const text = document.getElementById('btn-y-scale-text');
    const toggle = document.getElementById('sidebar-y-lock-toggle');
    const desc = document.getElementById('sidebar-y-desc');

    if (isYScaleLocked) {
        if (btn) btn.classList.add('locked');
        if (icon) icon.innerText = '🔒';
        if (text) text.innerText = lockedYMin !== null ? `Y轴已锁定 (${lockedYMin.toLocaleString()} ~ ${lockedYMax.toLocaleString()})` : 'Y轴已锁定';
        if (toggle) toggle.checked = true;
        if (desc) desc.innerText = lockedYMin !== null ? `开: [${lockedYMin.toLocaleString()} ~ ${lockedYMax.toLocaleString()}]` : '开: 锁定当前刻度';
    } else {
        if (btn) btn.classList.remove('locked');
        if (icon) icon.innerText = '🔓';
        if (text) text.innerText = 'Y轴自适应';
        if (toggle) toggle.checked = false;
        if (desc) desc.innerText = '关: 随数据自适应';
    }
}

export function setShowChartPhotos(enabled) {
    showChartPhotos = !!enabled;
    updatePhotoToggleUI();
    saveConfig();
    refreshPhotoMarks(myChart, isLiveMode ? liveData : cachedData, currentChannel, showChartPhotos, allPhotos);
}

export function toggleChartPhotos() {
    setShowChartPhotos(!showChartPhotos);
}

export function updatePhotoToggleUI() {
    const btn = document.getElementById('btn-toggle-photos');
    const icon = document.getElementById('btn-toggle-photos-icon');
    const text = document.getElementById('btn-toggle-photos-text');
    const toggle = document.getElementById('sidebar-photo-toggle');
    const desc = document.getElementById('sidebar-photo-desc');

    if (showChartPhotos) {
        if (btn) {
            btn.classList.add('active');
            btn.classList.remove('off');
        }
        if (icon) icon.innerText = '📷';
        if (text) text.innerText = '图表照片: 显示';
        if (toggle) toggle.checked = true;
        if (desc) desc.innerText = '开: 曲线上标记现场抓拍照片';
    } else {
        if (btn) {
            btn.classList.remove('active');
            btn.classList.add('off');
        }
        if (icon) icon.innerText = '🚫';
        if (text) text.innerText = '图表照片: 隐藏';
        if (toggle) toggle.checked = false;
        if (desc) desc.innerText = '关: 纯曲线无照片干扰';
    }
}

// ============================================================
//  配置记忆管理 (localStorage)
// ============================================================
export function saveConfig() {
    const config = {
        channel: currentChannel,
        date: document.getElementById('date-select')?.value,
        algo: document.getElementById('algo-select')?.value,
        downsample: document.getElementById('downsample-select')?.value,
        auto_scroll_x: autoScrollX,
        y_scale_locked: isYScaleLocked,
        locked_y_min: lockedYMin,
        locked_y_max: lockedYMax,
        show_chart_photos: showChartPhotos,
        params: {
            offset: document.getElementById('offset-slider')?.value,
            ma: document.getElementById('ma-slider')?.value,
            base: document.getElementById('base-slider')?.value,
            var_base: document.getElementById('var-base-slider')?.value,
            var_ma: document.getElementById('var-ma-slider')?.value,
            var_thresh: document.getElementById('var-thresh-slider')?.value,
            env_window: document.getElementById('env-window-slider')?.value,
            env_dry_up: document.getElementById('env-dry-window-up-slider')?.value,
            env_dry_down: document.getElementById('env-dry-window-down-slider')?.value,
            env_upper: document.getElementById('env-upper-offset-slider')?.value,
            env_lower: document.getElementById('env-lower-offset-slider')?.value
        }
    };
    localStorage.setItem('water_logger_config', JSON.stringify(config));
}

export function loadConfig() {
    try {
        updateScrollUI();
        updateYScaleUI();
        updatePhotoToggleUI();
        const saved = localStorage.getItem('water_logger_config');
        if (!saved) return;
        const config = JSON.parse(saved);

        if (config.channel) {
            currentChannel = config.channel;
            const btns = document.querySelectorAll('.tab-btn');
            btns.forEach((btn, idx) => {
                btn.classList.toggle('active', idx === currentChannel - 1);
            });
        }

        if (config.date) {
            const dateSelect = document.getElementById('date-select');
            if (dateSelect) {
                for (let i = 0; i < dateSelect.options.length; i++) {
                    if (dateSelect.options[i].value === config.date) {
                        dateSelect.value = config.date;
                        break;
                    }
                }
            }
        }

        if (config.algo) {
            const algoSelect = document.getElementById('algo-select');
            if (algoSelect) {
                algoSelect.value = config.algo;
                document.querySelectorAll('.param-group').forEach(el => el.style.display = 'none');
                const targetParamGroup = document.getElementById('param-group-' + config.algo);
                if (targetParamGroup) targetParamGroup.style.display = 'block';
            }
        }

        if (config.downsample && document.getElementById('downsample-select')) {
            document.getElementById('downsample-select').value = config.downsample;
        }

        if (config.auto_scroll_x !== undefined) autoScrollX = !!config.auto_scroll_x;
        if (config.y_scale_locked !== undefined) {
            isYScaleLocked = !!config.y_scale_locked;
            lockedYMin = config.locked_y_min !== undefined ? config.locked_y_min : null;
            lockedYMax = config.locked_y_max !== undefined ? config.locked_y_max : null;
        }
        if (config.show_chart_photos !== undefined) showChartPhotos = !!config.show_chart_photos;

        updateScrollUI();
        updateYScaleUI();
        updatePhotoToggleUI();

        if (config.params) {
            const p = config.params;
            const setSlider = (id, val, type) => {
                const el = document.getElementById(id);
                if (val !== undefined && el) {
                    el.value = val;
                    updateSliderLabel(type, val);
                }
            };
            setSlider('offset-slider', p.offset, 'offset');
            setSlider('ma-slider', p.ma, 'ma');
            setSlider('base-slider', p.base, 'base');
            setSlider('var-base-slider', p.var_base, 'var-base');
            setSlider('var-ma-slider', p.var_ma, 'var-ma');
            setSlider('var-thresh-slider', p.var_thresh, 'var-thresh');
            setSlider('env-window-slider', p.env_window, 'env-window');
            setSlider('env-dry-window-up-slider', p.env_dry_up, 'env-dry-window-up');
            setSlider('env-dry-window-down-slider', p.env_dry_down, 'env-dry-window-down');
            setSlider('env-upper-offset-slider', p.env_upper, 'env-upper-offset');
            setSlider('env-lower-offset-slider', p.env_lower, 'env-lower-offset');
        }
    } catch (e) {
        console.error("加载本地配置失败", e);
    }
}

// ============================================================
//  交互事件处理
// ============================================================
export function syncDownWindow(val) {
    const downSlider = document.getElementById('env-dry-window-down-slider');
    if (downSlider) {
        downSlider.value = val;
        updateSliderLabel('env-dry-window-down', val);
    }
}

export function updateSliderLabel(type, val) {
    const num = Number(val);
    const labelMap = {
        'offset': ['offset-val', `${num.toLocaleString()}`],
        'ma': ['ma-val', `${val} 秒`],
        'base': ['base-val', `${val} 秒`],
        'var-base': ['var-base-val', `${val} 秒`],
        'var-ma': ['var-ma-val', `${val} 秒`],
        'var-thresh': ['var-thresh-val', `${num.toLocaleString()}`],
        'env-window': ['env-window-val', `${val} 秒`],
        'env-dry-window-up': ['env-dry-window-up-val', `${val} 秒`],
        'env-dry-window-down': ['env-dry-window-down-val', `${val} 秒`],
        'env-upper-offset': ['env-upper-offset-val', `${num.toLocaleString()}`],
        'env-lower-offset': ['env-lower-offset-val', `${num.toLocaleString()}`]
    };
    if (labelMap[type]) {
        const [id, text] = labelMap[type];
        const el = document.getElementById(id);
        if (el) el.innerText = text;
    }
}

export function switchChannel(ch) {
    currentChannel = ch;
    const btns = document.querySelectorAll('.tab-btn');
    btns.forEach((btn, idx) => {
        btn.classList.toggle('active', idx === ch - 1);
    });
    saveConfig();
    if (isYScaleLocked) {
        const bounds = lockCurrentYBounds(isLiveMode && liveData ? liveData : cachedData, currentChannel);
        lockedYMin = bounds.min;
        lockedYMax = bounds.max;
        updateYScaleUI();
    }
    chartRender(myChart, isLiveMode && liveData ? liveData : cachedData, getChartRenderState());
}

export async function handleSaveStationConfig() {
    const stInput = document.getElementById('station-name-input');
    const btn = document.getElementById('btn-save-station');
    const val = stInput ? stInput.value.trim() : '';
    if (!val) {
        alert("站点名称不能为空");
        return;
    }
    if (btn) {
        btn.disabled = true;
        btn.innerText = "下发中...";
    }
    try {
        const data = await apiSaveStationConfig(val);
        if (data.ok) {
            const stBadge = document.getElementById('current-station-name');
            if (stBadge) stBadge.innerText = data.station_name;
            alert(`✅ 站点已更新为 [${data.station_name}]，并已向 water/sensor/start 下发激活指令！`);
        } else {
            alert(`❌ 更新失败: ${data.error || '未知错误'}`);
        }
    } catch (err) {
        alert(`❌ 网络请求失败: ${err.message}`);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerText = "更新激活";
        }
    }
}

export async function triggerCapture() {
    const btn = document.getElementById('capture-btn');
    if (btn && btn.disabled) return;
    if (btn) {
        btn.disabled = true;
        btn.textContent = '拍摄中...';
    }

    try {
        await doCapture();
        const targetDate = document.getElementById('date-select')?.value || todayLocal();
        setTimeout(async () => {
            if (btn) {
                btn.textContent = '📷 拍摄';
                btn.disabled = false;
            }
            await loadPhotos(targetDate);
        }, 3000);
        return;
    } catch (err) {
        console.error("拍摄请求异常:", err);
        if (btn) btn.textContent = '拍摄失败';
    }

    setTimeout(() => {
        if (btn) {
            btn.textContent = '📷 拍摄';
            btn.disabled = false;
        }
    }, 1500);
}

export async function toggleAutoCapture() {
    const checkbox = document.getElementById('auto-capture-checkbox');
    const toggle = document.getElementById('auto-capture-toggle');
    const enabled = !checkbox.checked;

    try {
        const data = await setAutoCaptureState(enabled);
        if (checkbox) checkbox.checked = data.auto_capture;
        if (toggle) toggle.classList.toggle('active', data.auto_capture);
        if (data.auto_capture) {
            const targetDate = document.getElementById('date-select')?.value || todayLocal();
            setTimeout(() => loadPhotos(targetDate), 3000);
        }
    } catch (err) {
        console.error("切换定时拍摄失败:", err);
    }
}

// ============================================================
//  实时直播与流管理
// ============================================================
export function setStatus(state) {
    const dot = document.getElementById('status-dot');
    const txt = document.getElementById('status-text');
    if (!dot || !txt) return;
    dot.className = 'status-dot';
    if (state === 'live') {
        dot.classList.add('live');
        txt.textContent = '实时接收中';
    } else if (state === 'connecting') {
        dot.classList.add('connecting');
        txt.textContent = '连接中...';
    } else {
        txt.textContent = '就绪';
    }
}

export function toggleLiveMode() {
    isLiveMode = !isLiveMode;
    const checkbox = document.getElementById('live-checkbox');
    const toggle = document.getElementById('live-toggle');
    if (checkbox) checkbox.checked = isLiveMode;
    if (toggle) toggle.classList.toggle('active', isLiveMode);

    if (isLiveMode) {
        startLiveStream();
    } else {
        stopLiveStream();
    }
}

export function startLiveStream() {
    stopLiveStream();
    liveAppendCount = 0;
    liveData = null;

    setStatus('connecting');

    const params = {
        algorithm: document.getElementById('algo-select')?.value || 'dynamic',
        threshold_offset: document.getElementById('offset-slider')?.value || 3200,
        ma_window: document.getElementById('ma-slider')?.value || 50,
        baseline_window: document.getElementById('base-slider')?.value || 200,
        var_baseline_ma: document.getElementById('var-base-slider')?.value || 200,
        var_variance_ma: document.getElementById('var-ma-slider')?.value || 30,
        var_threshold: parseInt(document.getElementById('var-thresh-slider')?.value || 500000),
        env_window: document.getElementById('env-window-slider')?.value || 30,
        env_dry_window_up: document.getElementById('env-dry-window-up-slider')?.value || 1000,
        env_dry_window_down: document.getElementById('env-dry-window-down-slider')?.value || 1000,
        env_upper_offset: parseInt(document.getElementById('env-upper-offset-slider')?.value || 5000),
        env_lower_offset: parseInt(document.getElementById('env-lower-offset-slider')?.value || 3000),
        poll_interval: '1.0'
    };

    eventSource = createEventSourceStream(params);

    eventSource.onmessage = (evt) => {
        const payload = JSON.parse(evt.data);

        if (payload.type === 'snapshot') {
            liveData = payload;
            liveAppendCount = 0;
            setStatus('live');
            showLiveBadge(true);
            document.getElementById('chart-card')?.classList.add('live-border');
            chartRender(myChart, liveData, getChartRenderState());

        } else if (payload.type === 'delta') {
            if (!liveData) return;

            const newCount = payload.timestamps.length;
            if (newCount === 0) return;

            liveData.timestamps.push(...payload.timestamps);
            for (const chKey of ['sensor1', 'sensor2', 'sensor3']) {
                for (const metric of ['raw', 'filtered', 'baseline', 'threshold', 'state']) {
                    liveData[chKey][metric].push(...payload[chKey][metric]);
                }
            }

            liveAppendCount += newCount;
            updateLiveBadge(liveAppendCount);

            const envParams = {
                upperOffset: parseFloat(document.getElementById('env-upper-offset-slider')?.value || 5000),
                lowerOffset: parseFloat(document.getElementById('env-lower-offset-slider')?.value || 3000)
            };
            chartAppend(myChart, payload, currentChannel, params.algorithm, envParams, autoScrollX);
            updateMetrics(liveData[`sensor${currentChannel}`], true);
        }
    };

    eventSource.onerror = () => {
        setStatus('idle');
        showLiveBadge(false);
    };

    loadPhotos(todayLocal());
    clearInterval(photoPollTimer);
    photoPollTimer = setInterval(() => loadPhotos(todayLocal()), 10000);
}

export function stopLiveStream() {
    if (eventSource) {
        eventSource.close();
        eventSource = null;
    }
    clearInterval(photoPollTimer);
    photoPollTimer = null;
    setStatus('idle');
    showLiveBadge(false);
    document.getElementById('chart-card')?.classList.remove('live-border');
    liveData = null;
}

function showLiveBadge(show = true) {
    const badge = document.getElementById('live-badge');
    if (badge) badge.classList.toggle('visible', show);
}

function updateLiveBadge(count) {
    const el = document.getElementById('live-count-text');
    if (el) el.textContent = `LIVE · +${count} pts`;
}

// ============================================================
//  数据拉取与历史渲染
// ============================================================
async function loadPhotos(date) {
    allPhotos = await apiFetchPhotos(date);
    currentPhotos = allPhotos.slice(-10);
    currentPhotosTotal = allPhotos.length;
    renderPhotoStrip();
    refreshPhotoMarks(myChart, isLiveMode ? liveData : cachedData, currentChannel, showChartPhotos, allPhotos);
}

function renderPhotoStrip() {
    const card = document.getElementById('photo-card');
    const strip = document.getElementById('photo-strip');
    if (!card || !strip) return;
    if (!currentPhotos.length) {
        card.style.display = 'none';
        strip.innerHTML = '';
        return;
    }
    card.style.display = 'block';
    const countEl = document.getElementById('photo-count');
    if (countEl) {
        countEl.textContent = currentPhotosTotal > 10
            ? `最新 10 张 / 共 ${currentPhotosTotal} 张`
            : `共 ${currentPhotos.length} 张`;
    }
    strip.innerHTML = currentPhotos.map(p => `
        <div class="photo-item" onclick="openPhotoModal('${p.url}', '${p.timestamp}')">
            <img class="photo-thumb" src="${p.url}" loading="lazy">
            <div class="photo-time">${p.time}</div>
        </div>
    `).join('');
}

export async function fetchData() {
    saveConfig();
    
    if (isLiveMode) {
        startLiveStream();
        return;
    }

    if (myChart) myChart.showLoading();
    
    const date = document.getElementById('date-select')?.value || todayLocal();
    const params = {
        date,
        algorithm: document.getElementById('algo-select')?.value || 'dynamic',
        threshold_offset: document.getElementById('offset-slider')?.value || 3200,
        ma_window: document.getElementById('ma-slider')?.value || 50,
        baseline_window: document.getElementById('base-slider')?.value || 200,
        var_baseline_ma: document.getElementById('var-base-slider')?.value || 200,
        var_variance_ma: document.getElementById('var-ma-slider')?.value || 30,
        var_threshold: parseInt(document.getElementById('var-thresh-slider')?.value || 500000),
        env_window: document.getElementById('env-window-slider')?.value || 30,
        env_dry_window_up: document.getElementById('env-dry-window-up-slider')?.value || 1000,
        env_dry_window_down: document.getElementById('env-dry-window-down-slider')?.value || 1000,
        env_upper_offset: parseInt(document.getElementById('env-upper-offset-slider')?.value || 5000),
        env_lower_offset: parseInt(document.getElementById('env-lower-offset-slider')?.value || 3000),
        downsample: document.getElementById('downsample-select')?.value || 10
    };

    try {
        cachedData = await fetchHistoryData(params);
        chartRender(myChart, cachedData, getChartRenderState());
        await loadPhotos(date);
    } catch (err) {
        console.error("加载曲线数据失败:", err);
    } finally {
        if (myChart) myChart.hideLoading();
    }
}

// ============================================================
//  启动初始化生命周期
// ============================================================
window.addEventListener('DOMContentLoaded', async () => {
    initModalListeners();
    myChart = initChartDom('chart', 
        (params) => showPhotoHover(params, allPhotos),
        () => hidePhotoHover()
    );

    await fetchDates();
    const stConfig = await fetchStationConfig();
    if (stConfig && stConfig.station_name) {
        const stInput = document.getElementById('station-name-input');
        const stBadge = document.getElementById('current-station-name');
        if (stInput) stInput.value = stConfig.station_name;
        if (stBadge) stBadge.innerText = stConfig.station_name;
    }

    loadConfig();
    await fetchData();

    const autoCap = await loadAutoCaptureState();
    const autoCapCb = document.getElementById('auto-capture-checkbox');
    const autoCapToggle = document.getElementById('auto-capture-toggle');
    if (autoCapCb) autoCapCb.checked = autoCap.auto_capture;
    if (autoCapToggle) autoCapToggle.classList.toggle('active', autoCap.auto_capture);

    document.getElementById('algo-select')?.addEventListener('change', function() {
        const algo = this.value;
        document.querySelectorAll('.param-group').forEach(el => el.style.display = 'none');
        const tg = document.getElementById('param-group-' + algo);
        if (tg) tg.style.display = 'block';
        fetchData();
    });
    
    window.addEventListener('resize', () => {
        if (myChart) myChart.resize();
    });
});

// ============================================================
//  向 window 全局暴露方法，保证 HTML inline 事件 100% 连贯响应
// ============================================================
Object.assign(window, {
    openSettingsModal,
    closeSettingsModal,
    onBackdropClick,
    switchSettingsTab,
    openPhotoModal,
    closePhotoModal,
    switchChannel,
    triggerCapture,
    toggleAutoCapture,
    toggleLiveMode,
    setAutoScroll,
    toggleAutoScroll,
    setYScaleLock,
    toggleYScaleLock,
    setShowChartPhotos,
    toggleChartPhotos,
    syncDownWindow,
    updateSliderLabel,
    saveStationConfig: handleSaveStationConfig,
    fetchData
});
