# water_logger 实现计划

本项目旨在新建一个 Python 应用程序，独立于原有的硬件和移动端，运行在本地，实现 1s 采样频率下水位传感器数据的 MQTT 接收、每日 CSV 持久化存储，并提供动态调参和历史曲线展示的 Web 页面。

## 用户审核要求
> [!IMPORTANT]
> 本项目采用**双进程架构**以保障数据记录的稳定性。运行本项目需要同时启动：
> 1. 数据采集后台进程 (`python logger.py`)
> 2. Web 服务进程 (`python server.py`)
> 我们将提供一个 `run.bat` 脚本以方便您在 Windows 环境下一键双开。

> [!TIP]
> 为保障前端性能并获得顺畅的数据缩放体验，ECharts 开启了 LTTB（最大三角形三桶）降采样渲染算法和大数量模式（`large: true`），可以秒级载入 86,400 个（一整天）数据点。

## 待解决问题
暂无。所有核心设计问题（UI方案、存储介质、本地重写算法的执行时机以及并发策略）已在前期的头脑风暴中对齐通过。

---

## 拟作出的修改

项目的所有新文件均放置于新创建的 [water_logger](file:///d:/Software/antigravity/water_logger) 目录中。

### [Component] 项目配置与启动脚本

#### [NEW] [requirements.txt](file:///d:/Software/antigravity/water_logger/requirements.txt)
* 声明 Python 依赖包：`fastapi`, `uvicorn`, `paho-mqtt`, `jinja2` 等。

#### [NEW] [run.bat](file:///d:/Software/antigravity/water_logger/run.bat)
* Windows 批处理一键启动脚本，同时拉起 `logger.py` 与 `server.py`。

---

### [Component] 数据采集模块

#### [NEW] [logger.py](file:///d:/Software/antigravity/water_logger/logger.py)
* 启动 MQTT 客户端，订阅主题 `water/sensor/status`。
* 接收 JSON 数据，自动获取本地系统时间，以追加（Append）形式记录到 `data/data_YYYYMMDD.csv` 中。

---

### [Component] 算法逻辑模块

#### [NEW] [sensor_logic.py](file:///d:/Software/antigravity/water_logger/sensor_logic.py)
* 在 Python 中完整复刻 C++ 中的 `Sensor` 类逻辑，包括：
  - 均值滤波器（滑动窗口，默认大小 50）。
  - 环境基准线追踪器（滑动窗口，默认大小 200）。
  - 迟滞双向状态机（根据当前状态动态选择加减门限偏移量 $\Delta$）。
  - 5小时持续有水看门狗逻辑。

---

### [Component] Web 服务与 API 模块

#### [NEW] [server.py](file:///d:/Software/antigravity/water_logger/server.py)
* 基于 FastAPI 提供 Web 服务器，包含以下路由：
  - `GET /`：渲染并返回前端展示主页面。
  - `GET /api/history`：接收 `date`, `threshold_offset`, `ma_window`, `baseline_window` 参数，读取对应日期的 CSV 文件，在内存中依次运行算法，向前端返回完整的曲线数值序列。
  - `GET /api/available_dates`：返回 `data/` 目录下现存的所有 CSV 日期列表，供前端下拉框使用。

---

### [Component] 前端展示与交互页面

#### [NEW] [index.html](file:///d:/Software/antigravity/water_logger/templates/index.html)
* 精美的**深色模式 (Sleek Dark Mode)** HTML5 页面。
* 引入 Google Fonts `Inter` 字体、ECharts 可视化库。
* 提供日期切换下拉框、通道切换选项卡，以及 3 个算法关键参数（Threshold Offset、MA Window、Baseline Window）的滑块控件。
* 渲染包含：原始值 (Raw)、滤波值 (Filtered)、基准线 (Baseline)、跳变门限线 (Threshold) 和有水覆盖背景区 (markArea) 的图表。

---

## 验证计划

### 自动化测试
* 运行 Python 脚本进行各核心模块的静态语法与导入测试：
  - `python -m py_compile logger.py server.py sensor_logic.py`

### 手动验证
1. **启动测试**：
   - 运行 `run.bat`，观察是否成功拉起两个控制台窗口。
2. **采集测试**：
   - 检查本地是否自动生成 `data/` 目录以及当天的 `data_YYYYMMDD.csv` 文件。
   - 观察控制台输出，确认是否能成功连接并收到网关/传感器的 MQTT 上报数据。
3. **Web 端调参及曲线渲染测试**：
   - 用浏览器访问 `http://127.0.0.1:8000`。
   - 确认图表可以正常绘制并呈现漂亮的深色风格。
   - 拖动调参滑块并点击“计算”，确认曲线形状和有水区域（绿色高亮背景）随之动态刷新，且页面加载无卡顿。
