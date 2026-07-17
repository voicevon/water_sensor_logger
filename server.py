import os
import csv
import json
import asyncio
from datetime import datetime
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

from sensor_logic import SensorAlgorithm, DiscreteVarianceAlgorithm, EnvelopeRangeAlgorithm

app = FastAPI(title="Water Logger Analysis Server")

# 允许跨域（方便本地开发调试）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

# 确保模板文件夹存在
os.makedirs(TEMPLATES_DIR, exist_ok=True)
templates = Jinja2Templates(directory=TEMPLATES_DIR)

@app.get("/", response_class=HTMLResponse)
async def read_index(request: Request):
    """
    渲染前端主页面
    """
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/api/available_dates")
async def get_available_dates():
    """
    扫描 data 文件夹，返回所有包含数据的日期列表 (格式: YYYY-MM-DD)
    """
    if not os.path.exists(DATA_DIR):
        return []
    
    dates = []
    for filename in os.listdir(DATA_DIR):
        # 匹配 data_YYYYMMDD.csv
        if filename.startswith("data_") and filename.endswith(".csv"):
            date_part = filename[5:13] # YYYYMMDD
            try:
                dt = datetime.strptime(date_part, "%Y%m%d")
                dates.append(dt.strftime("%Y-%m-%d"))
            except ValueError:
                continue
                
    # 降序排序，最新的日期放在最前面
    dates.sort(reverse=True)
    return dates

@app.get("/api/history")
async def get_history(
    date: str = Query(..., description="查询日期，格式 YYYY-MM-DD"),
    algorithm: str = Query("dynamic", description="算法选择"),
    threshold_offset: int = Query(50, description="门限偏移量"),
    ma_window: int = Query(50, description="均值滤波窗口大小"),
    baseline_window: int = Query(200, description="基准线窗口大小"),
    var_baseline_ma: int = Query(200, description="基线均值窗口"),
    var_variance_ma: int = Query(30, description="方差平滑窗口"),
    var_threshold: int = Query(5000, description="方差触发阈值"),
    env_window: int = Query(30, description="包络窗口大小"),
    env_dry_window_up: int = Query(1000, description="无水基准线上升窗口"),
    env_dry_window_down: int = Query(1000, description="无水基准线下降窗口"),
    env_upper_offset: int = Query(500, description="上触发相对偏置"),
    env_lower_offset: int = Query(300, description="下触发相对偏置"),
    downsample: int = Query(1, ge=1, description="数据降采样比例 (每N个点取一个)")
):
    """
    加载指定日期的 CSV，在内存中动态运行算法，返回所有 3 个通道的计算曲线
    """
    # 1. 规范化日期格式为 YYYYMMDD
    clean_date = date.replace("-", "")
    csv_path = os.path.join(DATA_DIR, f"data_{clean_date}.csv")
    
    response_data = {
        "timestamps": [],
        "total_rows": 0,
        "sensor1": {"raw": [], "filtered": [], "baseline": [], "threshold": [], "state": []},
        "sensor2": {"raw": [], "filtered": [], "baseline": [], "threshold": [], "state": []},
        "sensor3": {"raw": [], "filtered": [], "baseline": [], "threshold": [], "state": []}
    }
    
    if not os.path.exists(csv_path):
        return response_data
    
    # 2. 初始化 3 个通道的算法实例
    if algorithm == "discrete":
        algo1 = DiscreteVarianceAlgorithm(1, var_baseline_ma, var_variance_ma, var_threshold)
        algo2 = DiscreteVarianceAlgorithm(2, var_baseline_ma, var_variance_ma, var_threshold)
        algo3 = DiscreteVarianceAlgorithm(3, var_baseline_ma, var_variance_ma, var_threshold)
    elif algorithm == "envelope":
        algo1 = EnvelopeRangeAlgorithm(1, env_window, env_dry_window_up, env_dry_window_down, env_upper_offset, env_lower_offset)
        algo2 = EnvelopeRangeAlgorithm(2, env_window, env_dry_window_up, env_dry_window_down, env_upper_offset, env_lower_offset)
        algo3 = EnvelopeRangeAlgorithm(3, env_window, env_dry_window_up, env_dry_window_down, env_upper_offset, env_lower_offset)
    else:
        algo1 = SensorAlgorithm(1, threshold_offset, ma_window, baseline_window)
        algo2 = SensorAlgorithm(2, threshold_offset, ma_window, baseline_window)
        algo3 = SensorAlgorithm(3, threshold_offset, ma_window, baseline_window)
    
    timestamps = []
    s1_results = []
    s2_results = []
    s3_results = []
    total_rows = 0
    
    # 3. 流式解析 CSV 文件并串行喂入算法
    try:
        with open(csv_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                total_rows += 1
                # 检查必要字段是否存在
                if not all(k in row for k in ["timestamp", "sensor1", "sensor2", "sensor3"]):
                    continue
                    
                timestamp_str = row["timestamp"]
                try:
                    dt = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                
                # 提取原始电容值
                raw1 = int(row["sensor1"])
                raw2 = int(row["sensor2"])
                raw3 = int(row["sensor3"])
                
                # 运行算法
                pt1 = algo1.process_point(raw1, dt)
                pt2 = algo2.process_point(raw2, dt)
                pt3 = algo3.process_point(raw3, dt)
                
                # 保存全部计算结果
                timestamps.append(timestamp_str)
                s1_results.append(pt1)
                s2_results.append(pt2)
                s3_results.append(pt3)
    except Exception as e:
        print(f"[API Error] 读取或解析 CSV 失败: {str(e)}")
        return response_data
        
    # 4. 执行降采样 (Downsampling) 以降低网络传输和前端渲染开销
    if downsample > 1:
        sampled_timestamps = timestamps[::downsample]
        sampled_s1 = s1_results[::downsample]
        sampled_s2 = s2_results[::downsample]
        sampled_s3 = s3_results[::downsample]
    else:
        sampled_timestamps = timestamps
        sampled_s1 = s1_results
        sampled_s2 = s2_results
        sampled_s3 = s3_results
        
    # 5. 打包封装返回数据 (电容值除以 100 恢复为物理 pF)
    response_data["timestamps"] = sampled_timestamps
    response_data["total_rows"] = total_rows
    
    # 辅助转换函数，将算法整数值转为浮点数 pF
    def fill_channel_data(ch_dict, results_list):
        ch_dict["raw"] = [round(r["raw"] / 100.0, 2) for r in results_list]
        ch_dict["filtered"] = [round(r["filtered"] / 100.0, 2) for r in results_list]
        ch_dict["baseline"] = [round(r["baseline"] / 100.0, 2) for r in results_list]
        ch_dict["threshold"] = [round(r["threshold"] / 100.0, 2) for r in results_list]
        ch_dict["state"] = [r["state"] for r in results_list]
        
    fill_channel_data(response_data["sensor1"], sampled_s1)
    fill_channel_data(response_data["sensor2"], sampled_s2)
    fill_channel_data(response_data["sensor3"], sampled_s3)
    
    return response_data

@app.get("/api/realtime")
async def get_realtime(
    algorithm: str = Query("dynamic"),
    threshold_offset: int = Query(50),
    ma_window: int = Query(50),
    baseline_window: int = Query(200),
    var_baseline_ma: int = Query(200),
    var_variance_ma: int = Query(30),
    var_threshold: int = Query(5000),
    env_window: int = Query(30),
    env_dry_window_up: int = Query(1000),
    env_dry_window_down: int = Query(1000),
    env_upper_offset: int = Query(500),
    env_lower_offset: int = Query(300)
):
    """
    实时曲线接口：返回当天最新的 300 条数据，供前端实现实时心跳更新
    """
    today_str = datetime.now().strftime("%Y-%m-%d")
    # 直接运行 history 接口，不进行降采样，并截取最后 300 点
    full_day_data = await get_history(
        date=today_str,
        algorithm=algorithm,
        threshold_offset=threshold_offset,
        ma_window=ma_window,
        baseline_window=baseline_window,
        var_baseline_ma=var_baseline_ma,
        var_variance_ma=var_variance_ma,
        var_threshold=var_threshold,
        env_window=env_window,
        env_dry_window_up=env_dry_window_up,
        env_dry_window_down=env_dry_window_down,
        env_upper_offset=env_upper_offset,
        env_lower_offset=env_lower_offset,
        downsample=1
    )
    
    total_len = len(full_day_data["timestamps"])
    limit = 300
    if total_len > limit:
        full_day_data["timestamps"] = full_day_data["timestamps"][-limit:]
        for sensor_key in ["sensor1", "sensor2", "sensor3"]:
            for metric in ["raw", "filtered", "baseline", "threshold", "state"]:
                full_day_data[sensor_key][metric] = full_day_data[sensor_key][metric][-limit:]
                
    return full_day_data

# ==========================================
#  SSE 实时流式推送接口
# ==========================================

def _read_csv_rows_from(csv_path: str, start_row: int, algo1, algo2, algo3):
    """
    从 CSV 第 start_row 行（不含表头）开始读取增量数据，经算法处理后返回结果列表。
    返回: (new_rows_count, incremental_payload_dict)
    """
    incremental = {
        "timestamps": [],
        "sensor1": {"raw": [], "filtered": [], "baseline": [], "threshold": [], "state": []},
        "sensor2": {"raw": [], "filtered": [], "baseline": [], "threshold": [], "state": []},
        "sensor3": {"raw": [], "filtered": [], "baseline": [], "threshold": [], "state": []},
    }

    if not os.path.exists(csv_path):
        return 0, incremental

    new_count = 0
    try:
        with open(csv_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for idx, row in enumerate(reader):
                if idx < start_row:
                    continue  # 跳过已发送的行
                if not all(k in row for k in ["timestamp", "sensor1", "sensor2", "sensor3"]):
                    continue
                timestamp_str = row["timestamp"]
                try:
                    dt = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue

                raw1 = int(row["sensor1"])
                raw2 = int(row["sensor2"])
                raw3 = int(row["sensor3"])

                pt1 = algo1.process_point(raw1, dt)
                pt2 = algo2.process_point(raw2, dt)
                pt3 = algo3.process_point(raw3, dt)

                incremental["timestamps"].append(timestamp_str)
                for ch_key, pt in [("sensor1", pt1), ("sensor2", pt2), ("sensor3", pt3)]:
                    incremental[ch_key]["raw"].append(round(pt["raw"] / 100.0, 2))
                    incremental[ch_key]["filtered"].append(round(pt["filtered"] / 100.0, 2))
                    incremental[ch_key]["baseline"].append(round(pt["baseline"] / 100.0, 2))
                    incremental[ch_key]["threshold"].append(round(pt["threshold"] / 100.0, 2))
                    incremental[ch_key]["state"].append(pt["state"])

                new_count += 1
    except Exception as e:
        print(f"[SSE Error] 读取增量数据失败: {e}")

    return new_count, incremental


@app.get("/api/stream")
async def stream_realtime(
    request: Request,
    algorithm: str = Query("dynamic"),
    threshold_offset: int = Query(50),
    ma_window: int = Query(50),
    baseline_window: int = Query(200),
    var_baseline_ma: int = Query(200),
    var_variance_ma: int = Query(30),
    var_threshold: int = Query(5000),
    env_window: int = Query(30),
    env_dry_window_up: int = Query(1000),
    env_dry_window_down: int = Query(1000),
    env_upper_offset: int = Query(500),
    env_lower_offset: int = Query(300),
    poll_interval: float = Query(1.0, description="推送间隔秒数，默认 1 秒")
):
    """
    SSE 实时流式推送：
    - 首次连接发送当天全量历史数据（快照）
    - 之后每隔 poll_interval 秒检查 CSV 增量，有新数据则立刻推送
    """
    async def event_generator():
        today_str = datetime.now().strftime("%Y%m%d")
        csv_path = os.path.join(DATA_DIR, f"data_{today_str}.csv")

        # 初始化算法实例（有状态，贯穿整个 SSE 连接生命周期）
        if algorithm == "discrete":
            algo1 = DiscreteVarianceAlgorithm(1, var_baseline_ma, var_variance_ma, var_threshold)
            algo2 = DiscreteVarianceAlgorithm(2, var_baseline_ma, var_variance_ma, var_threshold)
            algo3 = DiscreteVarianceAlgorithm(3, var_baseline_ma, var_variance_ma, var_threshold)
        elif algorithm == "envelope":
            algo1 = EnvelopeRangeAlgorithm(1, env_window, env_dry_window_up, env_dry_window_down, env_upper_offset, env_lower_offset)
            algo2 = EnvelopeRangeAlgorithm(2, env_window, env_dry_window_up, env_dry_window_down, env_upper_offset, env_lower_offset)
            algo3 = EnvelopeRangeAlgorithm(3, env_window, env_dry_window_up, env_dry_window_down, env_upper_offset, env_lower_offset)
        else:
            algo1 = SensorAlgorithm(1, threshold_offset, ma_window, baseline_window)
            algo2 = SensorAlgorithm(2, threshold_offset, ma_window, baseline_window)
            algo3 = SensorAlgorithm(3, threshold_offset, ma_window, baseline_window)

        sent_rows = 0  # 已推送行数游标

        # --- 阶段1：发送全量历史快照（snapshot 事件）---
        total_count, snapshot = _read_csv_rows_from(csv_path, 0, algo1, algo2, algo3)
        sent_rows = total_count
        snapshot["type"] = "snapshot"
        yield f"data: {json.dumps(snapshot, ensure_ascii=False)}\n\n"

        # --- 阶段2：增量轮询推送（delta 事件）---
        while True:
            # 检查客户端是否已断开
            if await request.is_disconnected():
                print("[SSE] 客户端已断开连接")
                break

            await asyncio.sleep(poll_interval)

            # 重建当天 CSV 路径（防跨日）
            today_str = datetime.now().strftime("%Y%m%d")
            csv_path = os.path.join(DATA_DIR, f"data_{today_str}.csv")

            new_count, delta = _read_csv_rows_from(csv_path, sent_rows, algo1, algo2, algo3)

            if new_count > 0:
                sent_rows += new_count
                delta["type"] = "delta"
                yield f"data: {json.dumps(delta, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
    )

if __name__ == "__main__":
    import uvicorn
    print("[Server] 启动 Web 服务，监听端口: 8000")
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)
