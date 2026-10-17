/**
 * chart.js - ECharts 曲线推演、动态追加与坐标轴控制模块
 */

import { photoToMs } from './api.js';

export const CHANNEL_NAMES = {
    1: '次氯酸钠',
    2: '碳源',
    3: '铁盐'
};

export function initChartDom(domId, onMouseOverMarkPoint, onMouseOutMarkPoint, onUserZoom = null) {
    const chartDom = document.getElementById(domId);
    if (!chartDom) return null;
    const chartInstance = echarts.init(chartDom, 'dark');
    chartInstance.showLoading({
        text: '正在推演状态机...',
        color: '#06b6d4',
        textColor: '#f3f4f6',
        maskColor: 'rgba(11, 15, 25, 0.8)'
    });

    if (onMouseOverMarkPoint) {
        chartInstance.on('mouseover', params => {
            if (params.componentType === 'markPoint') onMouseOverMarkPoint(params);
        });
    }
    if (onMouseOutMarkPoint) {
        chartInstance.on('mouseout', params => {
            if (params.componentType === 'markPoint') onMouseOutMarkPoint(params);
        });
        chartInstance.on('globalout', onMouseOutMarkPoint);
    }

    // 监听用户手动缩放与拖动交互（锁定用户调节的左侧时间位置）
    chartInstance.on('dataZoom', params => {
        if (isProgrammaticZoom) return;

        const opt = chartInstance.getOption();
        if (!opt || !opt.dataZoom || !opt.dataZoom[0]) return;
        const dz = opt.dataZoom[0];
        const timestamps = opt.xAxis && opt.xAxis[0] ? opt.xAxis[0].data : [];
        if (!timestamps || !timestamps.length) return;

        let leftVal = null;
        if (dz.startValue !== undefined && dz.startValue !== null) {
            if (typeof dz.startValue === 'number') {
                const idx = Math.max(0, Math.min(timestamps.length - 1, Math.round(dz.startValue)));
                leftVal = timestamps[idx];
            } else {
                leftVal = dz.startValue;
            }
        } else if (dz.start !== undefined && dz.start !== null) {
            const idx = Math.max(0, Math.min(timestamps.length - 1, Math.round((dz.start / 100) * (timestamps.length - 1))));
            leftVal = timestamps[idx];
        }

        if (leftVal) {
            manualLeftValue = leftVal;
            if (onUserZoom) onUserZoom(manualLeftValue);
        }
    });

    return chartInstance;
}

// 记录用户手动控制的 X 轴左侧起始点（时间戳字符串）
let manualLeftValue = null;
let isProgrammaticZoom = false;

export function setManualLeftValue(val) {
    manualLeftValue = val;
}

export function getManualLeftValue() {
    return manualLeftValue;
}

export function autoScrollDataZoom(chartInstance, totalLen, autoScrollX, timestamps = null) {
    if (!chartInstance) return;

    if (!autoScrollX) {
        // 左侧锁定模式：左边锁定在用户拖动/设置的位置，右边自动填充（end: 100）
        let zoomOption;
        if (manualLeftValue && timestamps && timestamps.length) {
            const startIdx = timestamps.indexOf(manualLeftValue);
            if (startIdx >= 0) {
                zoomOption = {
                    start: null,
                    startValue: startIdx,
                    end: 100,
                    endValue: null
                };
            } else {
                zoomOption = { start: 0, startValue: null, end: 100, endValue: null };
            }
        } else {
            zoomOption = { start: 0, startValue: null, end: 100, endValue: null };
        }

        isProgrammaticZoom = true;
        chartInstance.setOption({
            dataZoom: [
                { type: 'inside', ...zoomOption },
                { type: 'slider', ...zoomOption }
            ]
        }, false);
        isProgrammaticZoom = false;
        return;
    }

    // 自动滚屏模式（追踪最后 300 点）
    const windowSize = 300;
    if (totalLen <= windowSize) {
        isProgrammaticZoom = true;
        chartInstance.setOption({
            dataZoom: [
                { type: 'inside', start: 0, startValue: null, end: 100, endValue: null },
                { type: 'slider', start: 0, startValue: null, end: 100, endValue: null }
            ]
        }, false);
        isProgrammaticZoom = false;
        return;
    }

    const endPct = 100;
    const startPct = Math.max(0, Math.floor((1 - windowSize / totalLen) * 100));
    isProgrammaticZoom = true;
    chartInstance.setOption({
        dataZoom: [
            { type: 'inside', start: startPct, startValue: null, end: endPct, endValue: null },
            { type: 'slider', start: startPct, startValue: null, end: endPct, endValue: null }
        ]
    }, false);
    isProgrammaticZoom = false;
}

export function applyYScaleToChart(chartInstance, isYScaleLocked, lockedYMin, lockedYMax) {
    if (!chartInstance) return;
    if (isYScaleLocked && lockedYMin !== null && lockedYMax !== null) {
        chartInstance.setOption({
            yAxis: [
                {
                    scale: false,
                    min: lockedYMin,
                    max: lockedYMax
                }
            ]
        }, false);
    } else {
        chartInstance.setOption({
            yAxis: [
                {
                    scale: true,
                    min: null,
                    max: null
                }
            ]
        }, false);
    }
}

export function lockCurrentYBounds(data, currentChannel) {
    if (!data) return { min: null, max: null };
    const sd = data[`sensor${currentChannel}`];
    if (!sd) return { min: null, max: null };
    
    const all = [];
    if (sd.raw && sd.raw.length) all.push(...sd.raw);
    if (sd.filtered && sd.filtered.length) all.push(...sd.filtered);
    if (sd.baseline && sd.baseline.length) all.push(...sd.baseline);
    
    if (all.length === 0) return { min: null, max: null };
    const minVal = all.reduce((a, b) => Math.min(a, b), Infinity);
    const maxVal = all.reduce((a, b) => Math.max(a, b), -Infinity);
    const span = Math.max(maxVal - minVal, 1000);
    return {
        min: Math.floor(minVal - span * 0.05),
        max: Math.ceil(maxVal + span * 0.05)
    };
}

export function buildPhotoMarks(sensorData, timestamps, showChartPhotos, allPhotos) {
    if (!showChartPhotos || !allPhotos || !allPhotos.length || !sensorData || !timestamps || !timestamps.length) return [];
    const marks = [];
    for (const p of allPhotos) {
        const t = photoToMs(p.timestamp);
        let lo = 0, hi = timestamps.length - 1;
        while (hi - lo > 1) {
            const mid = (lo + hi) >> 1;
            if (photoToMs(timestamps[mid]) < t) lo = mid; else hi = mid;
        }
        const idx = Math.abs(photoToMs(timestamps[lo]) - t) <= Math.abs(photoToMs(timestamps[hi]) - t) ? lo : hi;
        if (Math.abs(photoToMs(timestamps[idx]) - t) > 60000) continue;
        marks.push({
            name: p.timestamp,
            coord: [timestamps[idx], sensorData.raw[idx]],
            symbol: 'image://' + p.url,
            symbolSize: [46, 34],
            symbolOffset: [0, -20]
        });
    }
    return marks;
}

export function refreshPhotoMarks(chartInstance, data, currentChannel, showChartPhotos, allPhotos) {
    if (!chartInstance) return;
    chartInstance.setOption({ series: [{ markPoint: { data: [] } }] }, false);
    if (!showChartPhotos || !data) return;

    const sensorData = data[`sensor${currentChannel}`];
    const marks = buildPhotoMarks(sensorData, data.timestamps, showChartPhotos, allPhotos);
    if (marks.length) {
        chartInstance.setOption({
            series: [{
                markPoint: {
                    data: marks,
                    animation: false
                }
            }]
        }, false);
    }
}

export function findNearbyPhoto(tsStr, allPhotos) {
    if (!allPhotos || !allPhotos.length || !tsStr) return null;
    const t = photoToMs(tsStr);
    for (const p of allPhotos) {
        if (Math.abs(photoToMs(p.timestamp) - t) < 1500) return p;
    }
    return null;
}

export function generateMarkArea(timestamps, states) {
    const areaData = [];
    let start = null;
    for (let i = 0; i < states.length; i++) {
        if (states[i] === 1 && start === null) {
            start = timestamps[i];
        } else if (states[i] === 0 && start !== null) {
            areaData.push([
                { name: '有水', xAxis: start },
                { xAxis: timestamps[i - 1] }
            ]);
            start = null;
        }
    }
    if (start !== null) {
        areaData.push([
            { name: '有水', xAxis: start },
            { xAxis: timestamps[timestamps.length - 1] }
        ]);
    }
    return {
        itemStyle: { color: 'rgba(16, 185, 129, 0.08)' },
        label: {
            color: '#10b981',
            position: 'insideTopLeft',
            fontSize: 10,
            fontFamily: 'Inter',
            fontWeight: 'bold'
        },
        data: areaData
    };
}

export function updateMetrics(sensorData, flash = false) {
    const stateTag = document.getElementById('current-state-tag');
    const rawRange = document.getElementById('metric-raw-range');
    const filteredMax = document.getElementById('metric-filtered-max');
    const ptsCount = document.getElementById('metric-points-count');

    if (!sensorData) {
        if (stateTag) {
            stateTag.className = "state-tag dry";
            stateTag.innerText = "无数据";
        }
        if (rawRange) rawRange.innerText = "--";
        if (filteredMax) filteredMax.innerText = "--";
        if (ptsCount) ptsCount.innerText = "--";
        return;
    }

    const lastState = sensorData.state[sensorData.state.length - 1];
    if (stateTag) {
        if (lastState === 1) {
            stateTag.className = "state-tag wet";
            stateTag.innerText = "有水 (WET)";
        } else {
            stateTag.className = "state-tag dry";
            stateTag.innerText = "无水 (DRY)";
        }
    }

    const maxRaw = sensorData.raw.reduce((a, b) => Math.max(a, b), -Infinity);
    const minRaw = sensorData.raw.reduce((a, b) => Math.min(a, b), Infinity);
    if (rawRange) {
        rawRange.innerText = (minRaw === Infinity || maxRaw === -Infinity) ? "--" : `${minRaw.toLocaleString()} ~ ${maxRaw.toLocaleString()}`;
    }

    const maxFiltered = sensorData.filtered.reduce((a, b) => Math.max(a, b), -Infinity);
    if (filteredMax) {
        filteredMax.innerText = (maxFiltered === -Infinity) ? "--" : `${maxFiltered.toLocaleString()}`;
    }

    if (ptsCount) {
        ptsCount.innerText = `${sensorData.raw.length} 秒`;
    }

    if (flash) {
        ['mc-state', 'mc-raw', 'mc-filtered', 'mc-pts'].forEach(id => {
            const el = document.getElementById(id);
            if (el) {
                el.classList.remove('flash');
                void el.offsetWidth;
                el.classList.add('flash');
            }
        });
    }
}

export function appendToChart(chartInstance, delta, currentChannel, algorithm, envParams, autoScrollX) {
    if (!chartInstance || !delta) return;

    const chKey = `sensor${currentChannel}`;
    const sd = delta[chKey];

    const option = chartInstance.getOption();
    const existingX = option.xAxis[0].data || [];
    const newX = [...existingX, ...delta.timestamps];

    let seriesUpdates = [];

    if (algorithm === "dynamic") {
        seriesUpdates = [
            { data: [...(option.series[0].data || []), ...sd.raw] },
            { data: [...(option.series[1].data || []), ...sd.filtered] },
            { data: [...(option.series[2].data || []), ...sd.baseline] },
            { data: [...(option.series[3].data || []), ...sd.threshold] },
        ];
    } else if (algorithm === "discrete") {
        seriesUpdates = [
            { data: [...(option.series[0].data || []), ...sd.raw] },
            { data: [...(option.series[1].data || []), ...sd.baseline] },
            { data: [...(option.series[2].data || []), ...sd.filtered] },
            { data: [...(option.series[3].data || []), ...sd.threshold] },
        ];
    } else if (algorithm === "envelope") {
        const newDiffs = sd.filtered.map((val, idx) => +(val - sd.baseline[idx]).toFixed(2));
        const env_upper_val = envParams.upperOffset;
        const env_lower_val = envParams.lowerOffset;
        const newDrys = sd.threshold;
        const newUpper = newDrys.map(val => +(val + env_upper_val).toFixed(2));
        const newLower = newDrys.map(val => +(val + env_lower_val).toFixed(2));

        seriesUpdates = [
            { data: [...(option.series[0].data || []), ...sd.raw] },
            { data: [...(option.series[1].data || []), ...sd.filtered] },
            { data: [...(option.series[2].data || []), ...sd.baseline] },
            { data: [...(option.series[3].data || []), ...newDiffs] },
            { data: [...(option.series[4].data || []), ...newDrys] },
            { data: [...(option.series[5].data || []), ...newUpper] },
            { data: [...(option.series[6].data || []), ...newLower] },
        ];
    }

    chartInstance.setOption({
        xAxis: [{ data: newX }],
        series: seriesUpdates
    }, false);

    autoScrollDataZoom(chartInstance, newX.length, autoScrollX, newX);
}

export function renderChart(chartInstance, data, state) {
    if (!data || !chartInstance) return;

    const timestamps = data.timestamps;
    const chKey = `sensor${state.currentChannel}`;
    const sensorData = data[chKey];

    if (!sensorData || timestamps.length === 0) {
        chartInstance.setOption({
            title: {
                text: '该日期暂无数据上报记录',
                left: 'center',
                top: 'middle',
                textStyle: { color: '#9ca3af', fontSize: 16 }
            }
        }, true);
        updateMetrics(null);
        return;
    }

    const markAreaData = generateMarkArea(timestamps, sensorData.state);
    const algorithm = state.algorithm;
    
    let legendData = [];
    let seriesConfigs = [];
    let yAxisConfigs = [
        {
            type: 'value',
            name: '24-bit AD 计数值',
            scale: !state.isYScaleLocked,
            min: state.isYScaleLocked && state.lockedYMin !== null ? state.lockedYMin : undefined,
            max: state.isYScaleLocked && state.lockedYMax !== null ? state.lockedYMax : undefined,
            axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
            axisLabel: {
                color: '#9ca3af',
                formatter: function (value) {
                    return value.toLocaleString();
                }
            },
            splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } }
        }
    ];

    if (algorithm === "dynamic") {
        legendData = ['原始值 (Raw)', '滤波信号 (Filtered)', '环境基准线 (Baseline)', '自适应门限 (Threshold)'];
        seriesConfigs = [
            {
                name: '原始值 (Raw)',
                type: 'line',
                data: sensorData.raw,
                lineStyle: { width: 1, color: 'rgba(255,255,255,0.25)' },
                showSymbol: false,
                large: true,
                sampling: 'lttb'
            },
            {
                name: '滤波信号 (Filtered)',
                type: 'line',
                data: sensorData.filtered,
                lineStyle: { width: 2.5, color: '#10b981' },
                showSymbol: false,
                large: true,
                sampling: 'lttb',
                markArea: markAreaData
            },
            {
                name: '环境基准线 (Baseline)',
                type: 'line',
                data: sensorData.baseline,
                lineStyle: { width: 2, color: '#ec4899', type: 'solid' },
                showSymbol: false,
                large: true,
                sampling: 'lttb'
            },
            {
                name: '自适应门限 (Threshold)',
                type: 'line',
                data: sensorData.threshold,
                lineStyle: { width: 1.5, color: '#ef4444', type: 'dashed' },
                showSymbol: false,
                large: true,
                sampling: 'lttb'
            }
        ];
    } else if (algorithm === "discrete") {
        legendData = ['原始值 (Raw)', '长效基线 (Baseline)', '平滑方差 (Variance)'];
        yAxisConfigs.push({
            type: 'value',
            name: '方差幅度',
            scale: true,
            position: 'right',
            axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
            axisLabel: {
                color: '#fbbf24',
                formatter: function (value) {
                    return value.toLocaleString();
                }
            },
            splitLine: { show: false }
        });
        seriesConfigs = [
            {
                name: '原始值 (Raw)',
                type: 'line',
                data: sensorData.raw,
                lineStyle: { width: 1, color: 'rgba(255,255,255,0.25)' },
                showSymbol: false,
                large: true,
                sampling: 'lttb'
            },
            {
                name: '长效基线 (Baseline)',
                type: 'line',
                data: sensorData.baseline,
                lineStyle: { width: 2, color: '#ec4899', type: 'solid' },
                showSymbol: false,
                large: true,
                sampling: 'lttb'
            },
            {
                name: '平滑方差 (Variance)',
                type: 'line',
                yAxisIndex: 1,
                data: sensorData.filtered,
                lineStyle: { width: 2, color: '#fbbf24' },
                showSymbol: false,
                large: true,
                sampling: 'lttb',
                markArea: markAreaData
            }
        ];
    } else if (algorithm === "envelope") {
        const diffData = sensorData.filtered.map((val, idx) => +(val - sensorData.baseline[idx]).toFixed(2));
        const env_upper_val = state.envUpperOffset;
        const env_lower_val = state.envLowerOffset;
        const dryBaseline = sensorData.threshold;
        const upperLimitData = dryBaseline.map(val => +(val + env_upper_val).toFixed(2));
        const lowerLimitData = dryBaseline.map(val => +(val + env_lower_val).toFixed(2));
        
        legendData = ['原始值 (Raw)', '包络上线 (Upper)', '包络下线 (Lower)', '极差曲线 (Diff)', '无水基准线 (Dry Baseline)', '上触发线 (Upper Trigger)', '下触发线 (Lower Trigger)'];
        yAxisConfigs.push({
            type: 'value',
            name: '极差值',
            scale: true,
            position: 'right',
            axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
            axisLabel: {
                color: '#f59e0b',
                formatter: function (value) {
                    return value.toLocaleString();
                }
            },
            splitLine: { show: false }
        });
        seriesConfigs = [
            {
                name: '原始值 (Raw)',
                type: 'line',
                data: sensorData.raw,
                lineStyle: { width: 1, color: 'rgba(255,255,255,0.25)' },
                showSymbol: false,
                large: true,
                sampling: 'lttb',
                markArea: markAreaData
            },
            {
                name: '包络上线 (Upper)',
                type: 'line',
                data: sensorData.filtered,
                lineStyle: { width: 1.5, color: 'rgba(255,255,255,0.25)', type: 'dashed' },
                showSymbol: false,
                large: true,
                sampling: 'lttb'
            },
            {
                name: '包络下线 (Lower)',
                type: 'line',
                data: sensorData.baseline,
                lineStyle: { width: 1.5, color: 'rgba(255,255,255,0.25)', type: 'dashed' },
                showSymbol: false,
                large: true,
                sampling: 'lttb'
            },
            {
                name: '极差曲线 (Diff)',
                type: 'line',
                yAxisIndex: 1,
                data: diffData,
                itemStyle: { color: '#f59e0b' },
                lineStyle: { width: 2, color: '#f59e0b' },
                showSymbol: false,
                large: true,
                sampling: 'lttb'
            },
            {
                name: '无水基准线 (Dry Baseline)',
                type: 'line',
                yAxisIndex: 1,
                data: dryBaseline,
                itemStyle: { color: '#ec4899' },
                lineStyle: { width: 2, color: '#ec4899' },
                showSymbol: false,
                large: true,
                sampling: 'lttb'
            },
            {
                name: '上触发线 (Upper Trigger)',
                type: 'line',
                yAxisIndex: 1,
                data: upperLimitData,
                itemStyle: { color: '#ef4444' },
                lineStyle: { width: 1.5, color: '#ef4444', type: 'dashed' },
                showSymbol: false,
                large: true,
                sampling: 'lttb'
            },
            {
                name: '下触发线 (Lower Trigger)',
                type: 'line',
                yAxisIndex: 1,
                data: lowerLimitData,
                itemStyle: { color: '#10b981' },
                lineStyle: { width: 1.5, color: '#10b981', type: 'dashed' },
                showSymbol: false,
                large: true,
                sampling: 'lttb'
            }
        ];
    }

    const option = {
        backgroundColor: 'transparent',
        title: {
            text: `通道 [${CHANNEL_NAMES[state.currentChannel]}] 运行曲线推演`,
            left: 'center',
            textStyle: {
                color: '#f3f4f6',
                fontFamily: 'Outfit',
                fontSize: 16,
                fontWeight: 'bold'
            },
            top: 10
        },
        tooltip: {
            trigger: 'axis',
            backgroundColor: '#1f2937',
            borderColor: 'rgba(255,255,255,0.1)',
            textStyle: { color: '#f3f4f6' },
            axisPointer: {
                type: 'cross',
                label: { backgroundColor: '#374151' }
            },
            formatter: function (params) {
                let res = `<div style="font-weight:bold;margin-bottom:5px;">${params[0].name}</div>`;
                params.forEach(item => {
                    let unit = '';
                    const valStr = typeof item.value === 'number' ? item.value.toLocaleString() : item.value;
                    res += `<div style="display:flex;justify-content:space-between;gap:20px;">
                                <span>${item.marker} ${item.seriesName}:</span>
                                <span style="font-weight:bold;">${valStr}${unit}</span>
                            </div>`;
                });
                const dataIndex = params[0].dataIndex;
                const stateVal = sensorData.state[dataIndex];
                const stateStr = stateVal === 1
                    ? '<span style="color:#10b981;font-weight:bold;">有水 (WET)</span>'
                    : '<span style="color:#9ca3af;">无水 (DRY)</span>';
                res += `<div style="margin-top:5px;border-top:1px solid rgba(255,255,255,0.1);padding-top:5px;display:flex;justify-content:space-between;">
                            <span>状态判定:</span>
                            <span>${stateStr}</span>
                        </div>`;
                if (state.showChartPhotos) {
                    const photo = findNearbyPhoto(params[0].name, state.allPhotos);
                    if (photo) {
                        res += `<div style="margin-top:6px;">
                                    <img src="${photo.url}" style="max-width:220px;max-height:160px;border-radius:8px;display:block;border:1px solid rgba(255,255,255,0.2);">
                                    <div style="font-size:0.72rem;color:#9ca3af;margin-top:3px;">📷 ${photo.timestamp}</div>
                                </div>`;
                    }
                }
                return res;
            }
        },
        legend: {
            data: legendData,
            bottom: 40,
            textStyle: { color: '#9ca3af' }
        },
        grid: {
            left: '5%',
            right: '5%',
            top: '12%',
            bottom: '15%',
            containLabel: true
        },
        xAxis: {
            type: 'category',
            data: timestamps,
            axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
            axisLabel: { color: '#9ca3af' }
        },
        yAxis: yAxisConfigs,
        dataZoom: [
            {
                type: 'inside',
                start: (state.isLiveMode && state.autoScrollX && timestamps.length > 300)
                    ? Math.max(0, Math.floor((1 - 300 / timestamps.length) * 100))
                    : (!state.autoScrollX && manualLeftValue && timestamps.indexOf(manualLeftValue) >= 0 ? null : 0),
                startValue: (!state.autoScrollX && manualLeftValue && timestamps.indexOf(manualLeftValue) >= 0)
                    ? timestamps.indexOf(manualLeftValue)
                    : null,
                end: 100
            },
            {
                type: 'slider',
                start: (state.isLiveMode && state.autoScrollX && timestamps.length > 300)
                    ? Math.max(0, Math.floor((1 - 300 / timestamps.length) * 100))
                    : (!state.autoScrollX && manualLeftValue && timestamps.indexOf(manualLeftValue) >= 0 ? null : 0),
                startValue: (!state.autoScrollX && manualLeftValue && timestamps.indexOf(manualLeftValue) >= 0)
                    ? timestamps.indexOf(manualLeftValue)
                    : null,
                end: 100,
                textStyle: { color: '#9ca3af' },
                borderColor: 'rgba(255,255,255,0.1)',
                bottom: 0
            }
        ],
        series: seriesConfigs
    };

    isProgrammaticZoom = true;
    chartInstance.setOption(option, true);
    isProgrammaticZoom = false;
    updateMetrics(sensorData);
    refreshPhotoMarks(chartInstance, data, state.currentChannel, state.showChartPhotos, state.allPhotos);

    if (state.isLiveMode) {
        autoScrollDataZoom(chartInstance, timestamps.length, state.autoScrollX, timestamps);
    }
}
