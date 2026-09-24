import os
import csv
import io
import json
import time
import uuid
import asyncio
from datetime import datetime
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
import paho.mqtt.client as mqtt

from sensor_logic import SensorAlgorithm, DiscreteVarianceAlgorithm, EnvelopeRangeAlgorithm, BAD_VALUES

app = FastAPI(title="Water Logger Analysis Server")

def _sanitize_raw(r1: int, r2: int, r3: int, prev: list) -> tuple:
    """
    黑名单原始值覆盖（原始数据层，与算法无关）：
    命中 BAD_VALUES 的历史数据用前一行该通道的值代替。prev 为三个通道的上一次值。
    """
    vals = [r1, r2, r3]
    for i, v in enumerate(vals):
        if v in BAD_VALUES:
            vals[i] = prev[i]
        prev[i] = vals[i]
    return vals[0], vals[1], vals[2]

# Fix #9: 收紧 CORS 配置，仅允许本地开发环境访问，生产环境应进一步限制为实际部署域名
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
PHOTOS_DIR = os.path.join(DATA_DIR, "photos")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

# 确保模板文件夹存在
os.makedirs(TEMPLATES_DIR, exist_ok=True)
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# 照片静态文件服务（logger.py 保存照片到 data/photos/）
os.makedirs(PHOTOS_DIR, exist_ok=True)
app.mount("/photos", StaticFiles(directory=PHOTOS_DIR), name="photos")

# ==========================================
#  MQTT 拍摄触发配置（与 logger.py 保持一致）
# ==========================================
MQTT_BROKER          = "voicevon.vicp.io"
MQTT_PORT            = 1883
MQTT_USERNAME        = "von"
MQTT_PASSWORD        = "von123456"
CAMERA_TRIGGER_TOPIC = "water/photo/take"     # 相机拍摄触发主题（QoS 0）
STATION_NAME         = "dongzhan"             # 站点名称，部署到新站点时在此修改

CONFIG_FILE          = os.path.join(DATA_DIR, "config.json")  # 定时拍摄等配置持久化
AUTO_CAPTURE_INTERVAL = 10 * 60               # 定时拍摄间隔（秒）

_app_config = {"auto_capture": False, "last_capture_ts": ""}
_auto_capture_task = None

def _load_config():
    global _app_config
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                _app_config = {**_app_config, **json.load(f)}
    except Exception as e:
        print(f"[Config] 读取配置失败: {e}")

def _save_config():
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(_app_config, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Config] 保存配置失败: {e}")

def _publish_mqtt_trigger(motion_flag: str = "off"):
    """同步发布拍摄指令（短连接，在独立线程中调用）"""
    client_id = f"water_capture_req_{uuid.uuid4().hex[:8]}"
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, clean_session=True)
    except AttributeError:
        client = mqtt.Client(client_id=client_id, clean_session=True)
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=30)
    # 必须启动网络循环，消息才会真正发出
    client.loop_start()

    payload = json.dumps({
        "site_name": STATION_NAME,
        "action": "capture",
        "motion": motion_flag,
    })
    info = client.publish(CAMERA_TRIGGER_TOPIC, payload, qos=0)
    # 最多等 3 秒确认消息发出
    deadline = time.time() + 3
    while not info.is_published() and time.time() < deadline:
        time.sleep(0.05)
    published = info.is_published()
    client.loop_stop()
    client.disconnect()
    if not published:
        raise TimeoutError("MQTT 发布确认超时")
    return payload

async def trigger_capture(motion: str = Query("off", description="异物侵入检测开关: on/off")):
    """
    发送 MQTT 指令触发相机拍摄一张照片。
    协议：Topic=water/photo/take, QoS=0, JSON 载荷
      - site_name: 目标站点名称
      - action:    即时动作 capture
      - motion:    异物侵入检测开关 "on"/"off"
    使用短连接发布，用完即断，避免常驻连接。
    """
    motion_flag = "on" if motion == "on" else "off"

    try:
        payload = await asyncio.to_thread(_publish_mqtt_trigger, motion_flag)
        print(f"[Capture] 已发送拍摄指令: {payload}")
        return {"ok": True, "topic": CAMERA_TRIGGER_TOPIC, "payload": payload}
    except Exception as e:
        print(f"[Capture] 发送拍摄指令失败: {e}")
        return {"ok": False, "error": str(e)}

async def _auto_capture_loop():
    """
    定时拍摄后台循环：每 30 秒检查一次，到期（距上次拍摄 >= 10 分钟）自动触发拍摄。
    上次拍摄时间持久化在 config.json，服务重启后计时恢复。
    """
    print("[AutoCapture] 定时拍摄任务已启动")
    while True:
        try:
            if _app_config.get("auto_capture"):
                now = datetime.now()
                last = _app_config.get("last_capture_ts", "")
                due = True
                if last:
                    try:
                        due = (now - datetime.strptime(last, "%Y-%m-%d %H:%M:%S")).total_seconds() >= AUTO_CAPTURE_INTERVAL
                    except ValueError:
                        due = True
                if due:
                    try:
                        await asyncio.to_thread(_publish_mqtt_trigger)
                        _app_config["last_capture_ts"] = now.strftime("%Y-%m-%d %H:%M:%S")
                        _save_config()
                        print(f"[AutoCapture] {now.strftime('%H:%M:%S')} 已自动发送拍摄指令")
                    except Exception as e:
                        print(f"[AutoCapture] 自动拍摄失败: {e}")
        except Exception as e:
            print(f"[AutoCapture] 循环异常: {e}")
        await asyncio.sleep(30)

@app.post("/api/auto_capture")
async def set_auto_capture(enabled: str = Query(..., description="on/off")):
    """
    开关定时拍摄（每10分钟一张）。状态持久化到 data/config.json，服务重启后自动恢复。
    开启时立即触发一次拍摄。
    """
    global _auto_capture_task
    want_on = enabled == "on"
    _app_config["auto_capture"] = want_on
    _save_config()

    # 确保后台任务存在（服务启动时若配置为关则不创建）
    if _auto_capture_task is None or _auto_capture_task.done():
        _auto_capture_task = asyncio.create_task(_auto_capture_loop())

    if want_on:
        # 开启后立即拍一张
        try:
            await asyncio.to_thread(_publish_mqtt_trigger)
            _app_config["last_capture_ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _save_config()
        except Exception as e:
            print(f"[AutoCapture] 开启后首次拍摄失败: {e}")

    return {"ok": True, "auto_capture": want_on, "last_capture_ts": _app_config.get("last_capture_ts", "")}

@app.get("/api/auto_capture")
async def get_auto_capture():
    """查询定时拍摄状态"""
    return {"auto_capture": _app_config.get("auto_capture", False), "last_capture_ts": _app_config.get("last_capture_ts", "")}

@app.on_event("startup")
async def _startup_tasks():
    """服务启动：加载持久化配置；若定时拍摄为开，恢复后台循环"""
    _load_config()
    global _auto_capture_task
    if _app_config.get("auto_capture") and (_auto_capture_task is None or _auto_capture_task.done()):
        _auto_capture_task = asyncio.create_task(_auto_capture_loop())

# ==========================================
#  Fix #10: 提取算法实例工厂函数，消除三处重复的实例化代码
# ==========================================
def _create_algo_instances(
    algorithm: str,
    threshold_offset: int = 50,
    ma_window: int = 50,
    baseline_window: int = 200,
    var_baseline_ma: int = 200,
    var_variance_ma: int = 30,
    var_threshold: int = 5000,
    env_window: int = 30,
    env_dry_window_up: int = 1000,
    env_dry_window_down: int = 1000,
    env_upper_offset: int = 500,
    env_lower_offset: int = 300,
) -> tuple:
    """
    根据算法名称和参数创建三个传感器通道的算法实例。
    返回 (algo1, algo2, algo3) 元组。
    """
    if algorithm == "discrete":
        return (
            DiscreteVarianceAlgorithm(1, var_baseline_ma, var_variance_ma, var_threshold),
            DiscreteVarianceAlgorithm(2, var_baseline_ma, var_variance_ma, var_threshold),
            DiscreteVarianceAlgorithm(3, var_baseline_ma, var_variance_ma, var_threshold),
        )
    elif algorithm == "envelope":
        return (
            EnvelopeRangeAlgorithm(1, env_window, env_dry_window_up, env_dry_window_down, env_upper_offset, env_lower_offset),
            EnvelopeRangeAlgorithm(2, env_window, env_dry_window_up, env_dry_window_down, env_upper_offset, env_lower_offset),
            EnvelopeRangeAlgorithm(3, env_window, env_dry_window_up, env_dry_window_down, env_upper_offset, env_lower_offset),
        )
    else:  # "dynamic" 或其他，默认使用动态阈値算法
        return (
            SensorAlgorithm(1, threshold_offset, ma_window, baseline_window),
            SensorAlgorithm(2, threshold_offset, ma_window, baseline_window),
            SensorAlgorithm(3, threshold_offset, ma_window, baseline_window),
        )

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

@app.get("/api/photos")
async def get_photos(date: str = Query(..., description="查询日期，格式 YYYY-MM-DD")):
    """
    返回指定日期的照片列表。
    照片按日期子目录归档: photos/YYYYMMDD/photo_YYYYMMDD_HHMMSS[_n].ext
    """
    clean_date = date.replace("-", "")
    date_dir = os.path.join(PHOTOS_DIR, clean_date)
    if not os.path.isdir(date_dir):
        return []

    photos = []
    for fn in os.listdir(date_dir):
        if not fn.startswith(f"photo_{clean_date}_"):
            continue
        if not fn.lower().endswith((".jpg", ".jpeg", ".png", ".gif")):
            continue
        stem = os.path.splitext(fn)[0]
        try:
            dt = datetime.strptime(stem[:21], "photo_%Y%m%d_%H%M%S")
        except ValueError:
            continue
        photos.append({
            "filename": fn,
            "timestamp": dt.strftime("%Y-%m-%d %H:%M:%S"),
            "time": dt.strftime("%H:%M:%S"),
            "url": f"/photos/{clean_date}/{fn}",
        })

    photos.sort(key=lambda p: p["timestamp"])
    return photos

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
    
    # 2. 初始化 3 个通道的算法实例（Fix #10: 使用工厂函数替代重复实例化代码）
    algo1, algo2, algo3 = _create_algo_instances(
        algorithm,
        threshold_offset=threshold_offset, ma_window=ma_window, baseline_window=baseline_window,
        var_baseline_ma=var_baseline_ma, var_variance_ma=var_variance_ma, var_threshold=var_threshold,
        env_window=env_window, env_dry_window_up=env_dry_window_up, env_dry_window_down=env_dry_window_down,
        env_upper_offset=env_upper_offset, env_lower_offset=env_lower_offset,
    )
    
    timestamps = []
    s1_results = []
    s2_results = []
    s3_results = []
    total_rows = 0
    _prev_raw = [0, 0, 0]  # 黑名单覆盖用：上一行三个通道的原始值
    
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
                
                # Fix #11: 对 int() 转换加局部保护，避免 CSV 数据被截断或内容非法时抛出 ValueError 导致行静默跳过
                try:
                    raw1 = int(row["sensor1"])
                    raw2 = int(row["sensor2"])
                    raw3 = int(row["sensor3"])
                except (ValueError, KeyError):
                    print(f"[API Warning] 跳过格式错误行: {row}")
                    continue

                # 黑名单原始值覆盖（历史存量坏数据）
                raw1, raw2, raw3 = _sanitize_raw(raw1, raw2, raw3, _prev_raw)

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
        ch_dict["raw"] = [r["raw"] for r in results_list]
        ch_dict["filtered"] = [r["filtered"] for r in results_list]
        ch_dict["baseline"] = [r["baseline"] for r in results_list]
        ch_dict["threshold"] = [r["threshold"] for r in results_list]
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
    实时曲线接口：返回当天最新的 limit 条数据。

    Fix #7: 原实现调用 get_history() 导致重跑全天所有数据，随文件增长响应时间线性增大。
    现在改为直接读取文件末尾 N 行，不再重跑全天算法，响应时间 O(1)。
    """
    limit = 300
    today_str = datetime.now().strftime("%Y%m%d")
    csv_path = os.path.join(DATA_DIR, f"data_{today_str}.csv")

    response_data = {
        "timestamps": [],
        "total_rows": 0,
        "sensor1": {"raw": [], "filtered": [], "baseline": [], "threshold": [], "state": []},
        "sensor2": {"raw": [], "filtered": [], "baseline": [], "threshold": [], "state": []},
        "sensor3": {"raw": [], "filtered": [], "baseline": [], "threshold": [], "state": []},
    }

    if not os.path.exists(csv_path):
        return response_data

    # 读取文件所有行，截取末尾 limit 行进行算法推演
    # 注：未起始运行的算法仅处理末尾窗口，状态机初始状态不包含历史上下文，
    # 如需完整状态恒一性，应使用 /api/stream SSE 接口。
    try:
        with open(csv_path, mode="r", encoding="utf-8") as f:
            all_lines = f.readlines()

        # all_lines[0] 是表头，数据行从 [1:] 开始
        header = all_lines[0].strip() if all_lines else ""
        data_lines = all_lines[1:]
        total_rows = len(data_lines)
        tail_lines = data_lines[-limit:] if total_rows > limit else data_lines

        algo1, algo2, algo3 = _create_algo_instances(
            algorithm,
            threshold_offset=threshold_offset, ma_window=ma_window, baseline_window=baseline_window,
            var_baseline_ma=var_baseline_ma, var_variance_ma=var_variance_ma, var_threshold=var_threshold,
            env_window=env_window, env_dry_window_up=env_dry_window_up, env_dry_window_down=env_dry_window_down,
            env_upper_offset=env_upper_offset, env_lower_offset=env_lower_offset,
        )
        _prev_raw = [0, 0, 0]  # 黑名单覆盖用

        import io
        tail_csv = header + "\n" + "".join(tail_lines)
        reader = csv.DictReader(io.StringIO(tail_csv))
        for row in reader:
            if not all(k in row for k in ["timestamp", "sensor1", "sensor2", "sensor3"]):
                continue
            try:
                dt = datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S")
                raw1, raw2, raw3 = int(row["sensor1"]), int(row["sensor2"]), int(row["sensor3"])
            except (ValueError, KeyError):
                continue

            # 黑名单原始值覆盖（历史存量坏数据）
            raw1, raw2, raw3 = _sanitize_raw(raw1, raw2, raw3, _prev_raw)

            pt1 = algo1.process_point(raw1, dt)
            pt2 = algo2.process_point(raw2, dt)
            pt3 = algo3.process_point(raw3, dt)

            response_data["timestamps"].append(row["timestamp"])
            for ch_key, pt in [("sensor1", pt1), ("sensor2", pt2), ("sensor3", pt3)]:
                response_data[ch_key]["raw"].append(pt["raw"])
                response_data[ch_key]["filtered"].append(pt["filtered"])
                response_data[ch_key]["baseline"].append(pt["baseline"])
                response_data[ch_key]["threshold"].append(pt["threshold"])
                response_data[ch_key]["state"].append(pt["state"])

        response_data["total_rows"] = total_rows
    except Exception as e:
        print(f"[Realtime Error] 读取实时数据失败: {e}")

    return response_data


# ==========================================
#  SSE 实时流式推送接口
# ==========================================

def _read_csv_rows_from(csv_path: str, byte_offset: int, algo1, algo2, algo3, prev_raw: list):
    """
    从 CSV 文件的字节偏移量 byte_offset 处开始读取增量数据，经算法处理后返回结果列表。
    Fix #8: 原实现使用行号跳过（O(N) 逐行遍历），现改为字节偏移量定位，直接 f.seek() 到上次读取结束的位置。
    prev_raw: 黑名单覆盖用，三个通道上一行原始值（跨调用保持状态）
    返回: (new_byte_offset, new_rows_count, incremental_payload_dict)
    """
    incremental = {
        "timestamps": [],
        "sensor1": {"raw": [], "filtered": [], "baseline": [], "threshold": [], "state": []},
        "sensor2": {"raw": [], "filtered": [], "baseline": [], "threshold": [], "state": []},
        "sensor3": {"raw": [], "filtered": [], "baseline": [], "threshold": [], "state": []},
    }

    if not os.path.exists(csv_path):
        return byte_offset, 0, incremental

    new_count = 0
    new_offset = byte_offset
    try:
        with open(csv_path, mode="r", encoding="utf-8") as f:
            # Fix #8: 直接跳到上次读取结束的字节位置，避免 O(N) 行遍历
            if byte_offset == 0:
                # 首次读取：跳过表头行
                header_line = f.readline()
                new_offset = f.tell()
                byte_offset = new_offset
            else:
                f.seek(byte_offset)

            reader = csv.DictReader(
                f,
                fieldnames=["timestamp", "sensor1", "sensor2", "sensor3", "mqtt_state"]
            )
            for row in reader:
                if not all(k in row for k in ["timestamp", "sensor1", "sensor2", "sensor3"]):
                    continue
                timestamp_str = row["timestamp"]
                try:
                    dt = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                    raw1 = int(row["sensor1"])
                    raw2 = int(row["sensor2"])
                    raw3 = int(row["sensor3"])
                except (ValueError, KeyError):
                    continue

                # 黑名单原始值覆盖（历史存量坏数据）
                raw1, raw2, raw3 = _sanitize_raw(raw1, raw2, raw3, prev_raw)

                pt1 = algo1.process_point(raw1, dt)
                pt2 = algo2.process_point(raw2, dt)
                pt3 = algo3.process_point(raw3, dt)

                incremental["timestamps"].append(timestamp_str)
                for ch_key, pt in [("sensor1", pt1), ("sensor2", pt2), ("sensor3", pt3)]:
                    incremental[ch_key]["raw"].append(pt["raw"])
                    incremental[ch_key]["filtered"].append(pt["filtered"])
                    incremental[ch_key]["baseline"].append(pt["baseline"])
                    incremental[ch_key]["threshold"].append(pt["threshold"])
                    incremental[ch_key]["state"].append(pt["state"])
                new_count += 1

            new_offset = f.tell()
    except Exception as e:
        print(f"[SSE Error] 读取增量数据失败: {e}")

    return new_offset, new_count, incremental


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

        # 初始化算法实例（有状态，贯穿整个 SSE 连接生命周期）（Fix #10: 改用工厂函数）
        algo1, algo2, algo3 = _create_algo_instances(
            algorithm,
            threshold_offset=threshold_offset, ma_window=ma_window, baseline_window=baseline_window,
            var_baseline_ma=var_baseline_ma, var_variance_ma=var_variance_ma, var_threshold=var_threshold,
            env_window=env_window, env_dry_window_up=env_dry_window_up, env_dry_window_down=env_dry_window_down,
            env_upper_offset=env_upper_offset, env_lower_offset=env_lower_offset,
        )

        sent_byte_offset = 0  # Fix #8: 用字节偏移量替代行号游标
        _prev_raw = [0, 0, 0]  # 黑名单覆盖用（贯穿整个 SSE 连接）

        # --- 阶段1：发送全量历史快照（snapshot 事件）---
        sent_byte_offset, total_count, snapshot = _read_csv_rows_from(csv_path, 0, algo1, algo2, algo3, _prev_raw)
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
            new_csv_path = os.path.join(DATA_DIR, f"data_{today_str}.csv")

            # 如果日期发生变化，重置偏移量从新文件头开始
            if new_csv_path != csv_path:
                csv_path = new_csv_path
                sent_byte_offset = 0

            sent_byte_offset, new_count, delta = _read_csv_rows_from(csv_path, sent_byte_offset, algo1, algo2, algo3, _prev_raw)

            if new_count > 0:
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
