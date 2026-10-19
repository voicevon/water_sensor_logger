import os
import time
import json
import csv
import base64
from datetime import datetime
import paho.mqtt.client as mqtt

# ==========================================
#  MQTT 采集与持久化配置
# ==========================================
MQTT_BROKER       = "voicevon.vicp.io"       # MQTT Broker 地址
MQTT_PORT         = 1883                     # MQTT 端口
MQTT_USERNAME     = "von"                    # MQTT 用户名
MQTT_PASSWORD     = "von123456"              # MQTT 密码
MQTT_CLIENT_ID    = "water_logger_daemon_pc" # 采集端 Client ID
MQTT_TOPIC        = "water/sensor/status"    # 数据订阅主题
MQTT_CAMERA_TOPIC = "water/photo/status/dongzhan"  # 照片订阅主题
ROOT_DIR          = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR          = os.path.join(ROOT_DIR, "data")
PHOTOS_DIR        = os.path.join(DATA_DIR, "photos")
CONFIG_FILE       = os.path.join(DATA_DIR, "config.json")

def load_config():
    """从 data/config.json 读取站点名称与业务配置"""
    cfg = {"station_name": "dongzhan", "sample_interval": 1}
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
    except Exception as e:
        print(f"[Config] 读取配置失败: {e}")
    return cfg

_init_cfg = load_config()
STATION_NAME    = _init_cfg.get("station_name", "dongzhan")
SAMPLE_INTERVAL = int(_init_cfg.get("sample_interval", 1))

print(f"==================================================")
print(f"  Water Logger 采集服务已启动")
print(f"  配置站点名称: {STATION_NAME} (可通过 data/config.json 配置)")
print(f"  传感器采样间隔: {SAMPLE_INTERVAL} 秒")
print(f"  订阅主题: {MQTT_TOPIC}")
print(f"  数据存储路径: {DATA_DIR}")
print(f"  照片存储路径: {PHOTOS_DIR}")
print(f"==================================================")

# 确保数据保存目录存在
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(PHOTOS_DIR, exist_ok=True)

def on_connect(client, userdata, flags, reason_code, properties):
    """
    连接建立回调 (paho-mqtt v2 CallbackAPIVersion.VERSION2)
    reason_code == 0 表示连接成功
    """
    if reason_code == 0:
        cfg = load_config()
        station = cfg.get("station_name", "dongzhan")
        interval = int(cfg.get("sample_interval", 1))

        print("[MQTT] 成功连接至 MQTT Broker!")
        client.subscribe(MQTT_TOPIC)
        print(f"[MQTT] 已成功订阅主题: {MQTT_TOPIC}")

        # 订阅指定站点照片及通配主题
        camera_topic = f"water/photo/status/{station}"
        client.subscribe(camera_topic)
        client.subscribe("water/photo/status/+")
        print(f"[MQTT] 已成功订阅照片主题: {camera_topic} 与 water/photo/status/+")
        
        # 自动发布启动指令，触发传感器上报数据
        trigger_payload = json.dumps({"name": station, "interval": interval})
        client.publish("water/sensor/start", trigger_payload, qos=2)
        print(f"[MQTT] 已自动发送触发指令至 water/sensor/start: {trigger_payload}")
    else:
        print(f"[MQTT] 连接失败，错误码 (reason_code): {reason_code}")

def on_disconnect(client, userdata, *args, **kwargs):
    """
    连接断开回调 (兼容 paho-mqtt v1 与 v2)
    """
    reason_code = None
    if len(args) >= 2:
        reason_code = args[1]
    elif len(args) >= 1:
        reason_code = args[0]
    elif "reason_code" in kwargs:
        reason_code = kwargs["reason_code"]
    elif "rc" in kwargs:
        reason_code = kwargs["rc"]

    rc_str = str(reason_code) if reason_code is not None else ""
    rc_val = getattr(reason_code, "value", reason_code)

    if rc_val == 0 or rc_str == "Success":
        print("[MQTT] 连接已正常关闭。")
    elif "Unspecified error" in rc_str or rc_val in (7, 128) or "128" in rc_str:
        print("----------------------------------------------------------------------")
        print(f"[MQTT] ⚠️ 警告：连接已被 MQTT Broker 强制切断！")
        print(f"       原因分析: 检测到其它相同 Client ID ('{MQTT_CLIENT_ID}') 的实例正在运行。")
        print(f"       根据 MQTT 协议规则，两个相同实例正在『互相踢下线』抢占连接。")
        print(f"       解决建议: 若您在其它窗口也运行了 logger.py，请将其关闭保留唯一实例。")
        print(f"       当前实例将在 3 秒后尝试重新连接...")
        print("----------------------------------------------------------------------")
        time.sleep(3)
    else:
        print(f"[MQTT] ⚠️ 连接断开 (状态码: {reason_code})，尝试自动重新连接...")

def on_message(client, userdata, msg):
    """
    消息分发：water/photo 主题保存照片，其余按传感器数据写 CSV
    """
    if msg.topic.startswith("water/photo/"):
        _on_camera_message(msg)
        return
    _on_sensor_message(msg)

def _detect_image_ext(payload: bytes):
    """根据魔数识别图片格式"""
    if payload[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if payload[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if payload[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    return None

def _parse_photo_timestamp(data: dict):
    """
    从 JSON 载荷中提取照片拍摄时间戳（可选字段）。
    支持秒级 epoch 数值或 "YYYY-MM-DD HH:MM:SS" 字符串，失败返回本地当前时间。
    """
    ts_field = data.get("timestamp", data.get("ts"))
    if ts_field is not None:
        try:
            if isinstance(ts_field, (int, float)):
                return datetime.fromtimestamp(ts_field)
            return datetime.strptime(str(ts_field), "%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    return datetime.now()

def _on_camera_message(msg):
    """
    接收照片并保存到 photos 目录，文件名内嵌时间戳。
    支持两种载荷：原始二进制图片（魔数识别）、JSON 内 base64 编码图片。
    """
    try:
        payload = msg.payload
        img = None
        ts = datetime.now()

        ext = _detect_image_ext(payload)
        if ext:
            img = payload
        else:
            # 尝试 JSON + base64 载荷
            data = json.loads(payload.decode("utf-8"))
            ts = _parse_photo_timestamp(data)
            for key in ("image", "data", "photo", "jpg", "jpeg", "base64", "frame"):
                val = data.get(key)
                if isinstance(val, str) and val:
                    try:
                        decoded = base64.b64decode(val)
                    except Exception:
                        continue
                    ext = _detect_image_ext(decoded)
                    if ext:
                        img = decoded
                        break

        if not img:
            print(f"[Photo] 无法识别的照片载荷 ({len(payload)} 字节) topic={msg.topic}")
            return

        # 按日期子目录归档: photos/YYYYMMDD/photo_YYYYMMDD_HHMMSS.ext
        date_dir = os.path.join(PHOTOS_DIR, ts.strftime("%Y%m%d"))
        os.makedirs(date_dir, exist_ok=True)

        filename = f"photo_{ts.strftime('%Y%m%d_%H%M%S')}{ext}"
        path = os.path.join(date_dir, filename)
        # 同一秒多张照片时追加序号，避免覆盖
        seq = 1
        while os.path.exists(path):
            filename = f"photo_{ts.strftime('%Y%m%d_%H%M%S')}_{seq}{ext}"
            path = os.path.join(date_dir, filename)
            seq += 1

        with open(path, "wb") as f:
            f.write(img)
            f.flush()
            os.fsync(f.fileno())  # 强制照片数据落盘，防止断电丢失
        print(f"[Photo] {ts.strftime('%Y-%m-%d %H:%M:%S')} 已保存 {filename} ({len(img)} 字节) topic={msg.topic}")

    except json.JSONDecodeError:
        print(f"[ERROR] 照片载荷不是合法 JSON 也不是二进制图片: {msg.payload[:32]}")
    except Exception as e:
        print(f"[ERROR] 照片保存过程中发生异常: {str(e)}")

def _on_sensor_message(msg):
    try:
        # 1. 解析 JSON 载荷
        payload = msg.payload.decode("utf-8")
        data = json.loads(payload)
        
        # 2. 提取传感器数值（24 位有符号原生 AD 计数值）
        # 协议规定：sensor1 到 sensor3，以及 state (开关水状态字节)
        # 注意：原始数据如实入库，不做任何修改；黑名单覆盖在算法读取 CSV 时进行
        sensor1 = data.get("sensor1", 0)
        sensor2 = data.get("sensor2", 0)
        sensor3 = data.get("sensor3", 0)
        state_byte = data.get("state", data.get("stateByte", 0)) # 兼顾可能存在的字段变体
        
        # 3. 获取当前本地时间
        now = datetime.now()
        timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S")
        date_str = now.strftime("%Y%m%d")
        
        # 4. 定位 CSV 文件并进行追加写
        csv_filename = f"data_{date_str}.csv"
        csv_path = os.path.join(DATA_DIR, csv_filename)
        
        # 5. 打开文件追加写入，并设置 newline='' 保证 Windows 换行符正常
        #    Fix #3: 改用 f.tell() == 0 替代 os.path.exists() 判断是否需要写表头。
        #    os.path.exists() 与 open() 之间存在 TOCTOU 竞争窗口；
        #    f.tell() == 0 在 open() 后立即调用，是原子性的。
        with open(csv_path, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            # f.tell() == 0 表示文件为空（新文件），需要写入表头
            if f.tell() == 0:
                writer.writerow(["timestamp", "sensor1", "sensor2", "sensor3", "mqtt_state"])
            
            # 写入一行数据
            writer.writerow([timestamp_str, sensor1, sensor2, sensor3, state_byte])

            # 强制刷新缓冲区到 OS 页缓存，并 fsync 强制写入物理磁盘。
            # Fix: 仅 flush() 数据仍在操作系统页缓存中，突然断电/关机会导致
            # 页缓存中未回写的数据全部丢失（Web 刷新时能看到，重启后消失）。
            f.flush()
            os.fsync(f.fileno())
            
        print(f"[Log] {timestamp_str} -> S1: {sensor1}, S2: {sensor2}, S3: {sensor3}, State: {state_byte}")
        
    except json.JSONDecodeError:
        print(f"[ERROR] 接收到非法的 JSON 数据包: {msg.payload}")
    except Exception as e:
        print(f"[ERROR] 数据写入过程中发生异常: {str(e)}")

def main():
    # 初始化 Paho MQTT 客户端 (兼容 paho-mqtt v1 与 v2)
    # client_id 必须全网唯一：与远端部署的同名客户端会互相踢下线，后缀 _pc 区分本机实例
    try:
        # paho-mqtt v2.0+: 使用 VERSION2 消除 DeprecationWarning
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=MQTT_CLIENT_ID, clean_session=True)
    except AttributeError:
        # paho-mqtt v1.x 降级兼容
        client = mqtt.Client(client_id=MQTT_CLIENT_ID, clean_session=True)
    
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    
    try:
        # 连接 Broker
        client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    except Exception as e:
        print(f"[MQTT] 无法连接到 MQTT Broker ({MQTT_BROKER}:{MQTT_PORT}): {str(e)}")
        print("[MQTT] 将依靠 paho-mqtt 自动重连机制启动循环...")

    # 启动死循环，并在后台处理重连和心跳
    client.loop_forever()

if __name__ == "__main__":
    main()
