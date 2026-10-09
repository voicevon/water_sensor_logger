# Water Sensor Logger (水质传感器高频数据记录与动态曲线分析系统)

`water_sensor_logger` 是水质自动采样系统中运行于上位机/服务器端的 **Python 高频数据记录与可视化动态分析工具**。系统专为 ESP32 传感器节点（HX711 称重/电容应变及水位传感器）设计，支持 1Hz 高频遥测数据的长效不间断采集、每日数据持久化归档、算法离线仿真回放，以及基于 Web ECharts 的实时动态调参交互分析。

---

## 🌟 核心特性

- **双进程物理隔离架构**：
  - **采集进程 (`logger.py`)**：轻量级后台常驻，专职 MQTT 报文监听与每日 CSV 追加写入，彻底与 Web 服务解耦，保障数周至数月长期连续采集的绝对稳定性。
  - **分析服务 (`server.py`)**：基于 FastAPI 的高性能异步 Web 服务，只读流式解析 CSV 数据，提供历史回溯、调参重算与照片查看。
- **C++ 算法 1:1 纯 Python 复刻与仿真 (`sensor_logic.py`)**：
  - **DYNAMIC（动态施密特迟滞）**：滑动均值滤波 ($W_{ma}=50$) + 环境自适应基准线追踪 ($W_{base}=200$) + 双向门限迟滞比较器 + 5 小时有水看门狗 (WDT)。
  - **DISCRETE（离散方差算法）**：基准线平滑 + 方差平滑 ($W_{var}=30$) + 方差门限触发。
  - **ENVELOPE（包络范围算法）**：窗口内极值差统计 + 自适应干态基准线追踪。
  - **$O(1)$ 增量计算引擎**：基于 `collections.deque(maxlen=N)` 与增量和缓存，算法复杂度从 $O(N)$ 降至 $O(1)$，轻松承载全天 86,400+ 点高频实时演算。
  - **黑名单异常值过滤**：自动识别并平滑 24 位 ADC 饱和钳位值（`8388607`、`-8388608`）。
- **专业级可视化分析工作台 (`templates/index.html`)**：
  - **深色科技风设计** (Sleek Dark Mode)：采用毛玻璃卡片（Glassmorphism）与响应式布局。
  - **Apache ECharts 极致渲染**：开启 `large: true` 与 `LTTB`（Largest-Triangle-Three-Buckets）降采样，支持数万至数十万点全天曲线毫秒级缩放与平移。
  - **五合一状态可视化**：同屏对比原始曲线 (Raw)、滤波曲线 (Filtered)、基准线 (Baseline)、触发阈值 (Threshold) 与有水状态背景标记区 (MarkArea)。
  - **即时动态调参演算**：在浏览器端直接微调窗口大小与偏置阈值，毫秒级即时重算呈现，指导现场标定与阈值优化。
  - **照片同步与相机联动**：支持自动/手动触发现场摄像头抓拍照片，图像自动按时间戳归档并在 Web 界面预览对比。

---

## 🏗️ 系统架构图

```mermaid
graph TD
    Sensor[ESP32 传感器节点] -->|1Hz MQTT water/sensor/status| Broker[MQTT Broker: voicevon.vicp.io]
    Camera[ESP32-CAM 摄像头] -->|MQTT JPEG 照片流| Broker
    
    subgraph 采集守护进程 (logger.py)
        Broker -->|订阅遥测与照片| Logger[数据采集器 logger.py]
        Logger -->|追加写入| CSV[(每日 CSV 文件: data/data_YYYYMMDD.csv)]
        Logger -->|保存快照| Photos[(照片目录: data/photos/)]
    end

    subgraph 分析与展示服务 (server.py)
        CSV -.->|流式只读读取| FastAPI[FastAPI Web Server]
        FastAPI -->|算法引擎演算| Engine[sensor_logic.py]
        Engine -->|重算曲线数据| FastAPI
        FastAPI -->|REST API & 静态服务| WebUI[浏览器 ECharts 分析面板: :8000]
        WebUI -->|触发抓拍 water/photo/take| Broker
    end
```

---

## 📁 目录结构

```text
water_sensor_logger/
├── data/                       # 数据归档目录（自动生成）
│   ├── data_YYYYMMDD.csv       # 每日传感器高频采样 CSV 文件
│   ├── config.json             # 服务端持久化配置（如定时拍照开关）
│   └── photos/                 # 现场抓拍照片存储目录 (IMG_*.jpg)
├── doc/                        # 项目设计与架构文档
│   ├── 2026-07-10-water-logger-design.md
│   └── implementation_plan.md
├── templates/                  # 前端模板
│   └── index.html              # ECharts 数据分析与动态调参控制台
├── logger.py                   # 后台数据采集与图片接收守护进程
├── server.py                   # FastAPI 分析与可视化 Web 服务端
├── sensor_logic.py             # 核心滤波、基准线追踪与状态机算法引擎
├── requirements.txt            # Python 依赖清单
├── run.bat                     # Windows 一键并发启动脚本
└── README.md                   # 项目工程说明文档
```

---

## 🚀 快速上手

### 1. 环境准备

确保本机已安装 Python 3.8 或更高版本：

```bash
python --version
```

进入项目目录并安装必要依赖：

```bash
cd water_sensor_logger
pip install -r requirements.txt
```

> **依赖说明**：
> - `fastapi`：现代化异步 Web 框架
> - `uvicorn`：高性能 ASGI Web 服务器
> - `paho-mqtt`：MQTT 客户端库
> - `jinja2`：HTML 模板引擎

### 2. 启动系统

#### 方式 A：Windows 一键启动（推荐）
双击运行 `run.bat`，脚本将自动打开两个独立的控制台窗口分别运行采集器与 Web 服务：
```cmd
run.bat
```

#### 方式 B：手动分进程启动

**步骤 1：启动后台采集器**
```bash
python logger.py
```
*控制台将打印连接成功信息，自动向 `water/sensor/start` 发送采样激活指令，并开始将接收到的数据写入 `data/data_YYYYMMDD.csv`。*

**步骤 2：启动分析 Web 服务**
```bash
python server.py
```
*服务默认启动于 `http://127.0.0.1:8000`。*

---

## 📊 数据格式与通信协议

### 1. 每日数据 CSV 格式 (`data/data_YYYYMMDD.csv`)
文件按天切分，首行为表头，每秒追加一条记录：

```csv
timestamp,sensor1,sensor2,sensor3,mqtt_state
2026-10-09 12:00:01,8388120,8388450,8387900,0
2026-10-09 12:00:02,8388125,8388448,8387910,0
...
```

- `timestamp`：本地标准格式化时间戳 (`YYYY-MM-DD HH:MM:SS`)
- `sensor1` ~ `sensor3`：3 路通道的 24 位有符号原生 AD 计数值
- `mqtt_state`：固件端判定的有水/无水状态位（Bit0: 通道1，Bit1: 通道2，Bit2: 通道3）

### 2. MQTT 通信主题一览

| 主题 (Topic) | 方向 | QoS | 说明 |
| :--- | :---: | :---: | :--- |
| `water/sensor/status` | Sub | 0 | 接收 ESP32 传感器节点上报的实时高频数据 JSON |
| `water/sensor/start` | Pub | 2 | 启动时下发采样激活指令 `{"name": "...", "interval": 1}` |
| `water/photo/status/+` | Sub | 0 | 接收 ESP32-CAM 摄像头回传的 JPEG 抓拍图片 Base64 载荷 |
| `water/photo/take` | Pub | 0 | 向摄像头节点发送远程拍照触发指令 |

---

## 🛠️ Web 分析工作台操作指南

打开浏览器访问 [http://127.0.0.1:8000](http://127.0.0.1:8000)：

1. **日期切换**：在顶部日期选择器中选择任意历史日期，即可流式加载当天的完整采样数据。
2. **多通道查看**：在顶部切换 `#1`、`#2`、`#3` 物理通道，各通道独立呈现曲线。
3. **交互式缩放**：
   - 鼠标滚轮或拖动底部 `DataZoom` 滑块可局部放大查看触水瞬间的毫秒级跳变波形；
   - 支持图例点击切换显示/隐藏原始值、滤波值、基准线与触发门限。
4. **实时调参重算 (Simulator)**：
   - 调整右侧/下方的算法控制面板参数（如将 `Threshold Offset` 从 3,200 调节到 20,000）；
   - 点击 **「重新计算」**，后端将在内存中利用优化算法快速对全天数据重新跑一遍状态机，直观对比误报率与响应延迟。
5. **抓拍照片管理**：
   - 点击 **「远程拍照」** 可即时下发指令抓拍一张照片；
   - 点击历史抓拍缩略图可全屏高保真对比当时管道的水位与药液状态。

---

## 🔒 生产环境与维护注意

1. **数据目录备份**：`data/` 目录存放所有物理历史数据，建议定期对 `data/` 进行异地压缩归档。
2. **端口占用排查**：若 8000 端口被占用，可在 `server.py` 末尾修改 `uvicorn.run(..., port=8000)` 配置。
3. **站点名称更换**：若部署到不同水文监测站，在 `logger.py` 与 `server.py` 中将 `STATION_NAME` 修改为目标英文标识（如 `dongzhan`）。
