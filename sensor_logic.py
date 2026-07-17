from datetime import datetime

class SensorAlgorithm:
    """
    水位传感器通道核心算法 (Python 版)
    复刻 C++ 端滑动平均滤波、环境基准线自适应追踪以及双向施密特迟滞触发状态机。
    """
    def __init__(self, sensor_id: int, threshold_offset: int = 50, ma_window: int = 50, baseline_window: int = 200):
        self.sensor_id = sensor_id
        self.threshold_offset = threshold_offset
        self.ma_window = ma_window
        self.baseline_window = baseline_window
        
        self.reset()

    def reset(self):
        self.raw_value = 0
        self.filtered_value = 0
        self.baseline_value = 0
        self.state = 0  # 0: 无水 (NO_WATER), 1: 有水 (HAS_WATER)
        
        # 滑动平均滤波器环形队列缓存
        self.ma_buf = []
        self.base_buf = []
        
        # 有水状态的起始时间戳（类型为 datetime，用于看门狗）
        self.has_water_start_time = None

    def push_filter(self, value: int) -> int:
        """
        滑窗平滑滤波器 (Moving Average)
        """
        self.ma_buf.append(value)
        if len(self.ma_buf) > self.ma_window:
            self.ma_buf.pop(0)
        return int(sum(self.ma_buf) / len(self.ma_buf))

    def push_baseline(self, value: int) -> int:
        """
        自适应基准线追踪滑窗 (以 Filtered 后的值为输入)
        """
        self.base_buf.append(value)
        if len(self.base_buf) > self.baseline_window:
            self.base_buf.pop(0)
        return int(sum(self.base_buf) / len(self.base_buf))

    def get_threshold(self) -> int:
        """
        施密特双向触发迟滞门限计算：
        - 当前无水时，有水触发门限 = 基准线 + 门限偏移量
        - 当前有水时，无水恢复门限 = 基准线 - 门限偏移量
        """
        if self.state == 0:  # 无水状态
            return self.baseline_value + self.threshold_offset
        else:                # 有水状态
            return self.baseline_value - self.threshold_offset

    def process_point(self, value: int, timestamp: datetime) -> dict:
        """
        处理时间序列上的单点数据。
        value: 原始电容值 (uint16_t, pf_val * 100)
        timestamp: datetime 对象
        """
        self.raw_value = value
        self.filtered_value = self.push_filter(value)
        self.baseline_value = self.push_baseline(self.filtered_value)

        # 1. 5小时持续有水看门狗逻辑 (防挂死)
        if self.state == 1:
            if self.has_water_start_time is None:
                self.has_water_start_time = timestamp
            
            elapsed_sec = (timestamp - self.has_water_start_time).total_seconds()
            if elapsed_sec >= 5 * 3600:  # 5 小时
                # 触发 WDT 看门狗，强行复位为 0 (无水状态)
                self.state = 0
                self.has_water_start_time = None
        else:
            self.has_water_start_time = None

        # 2. 状态机评估
        threshold = self.get_threshold()
        current_state = self.state
        next_state = current_state

        if current_state == 0:  # 当前无水
            if self.filtered_value > threshold:
                next_state = 1
            else:
                next_state = 0
        else:                   # 当前有水
            if self.filtered_value < threshold:
                next_state = 0
            else:
                next_state = 1

        # 状态变更与有水计时器维护
        if next_state != current_state:
            self.state = next_state
            if next_state == 1:
                self.has_water_start_time = timestamp
            else:
                self.has_water_start_time = None

        return {
            "raw": self.raw_value,
            "filtered": self.filtered_value,
            "baseline": self.baseline_value,
            "threshold": threshold,
            "state": self.state
        }

class DiscreteVarianceAlgorithm:
    """
    离散方差算法
    使用大均值窗口计算基准线，计算离散方差后使用小窗口平滑，超过阈值则触发有水状态。
    """
    def __init__(self, sensor_id: int, baseline_ma: int = 200, variance_ma: int = 30, var_threshold: int = 50):
        self.sensor_id = sensor_id
        self.baseline_window = baseline_ma
        self.variance_window = variance_ma
        self.var_threshold = var_threshold
        self.reset()

    def reset(self):
        self.raw_value = 0
        self.baseline_value = 0
        self.variance_smoothed = 0
        self.state = 0
        
        self.base_buf = []
        self.var_buf = []

    def process_point(self, value: int, timestamp: datetime) -> dict:
        self.raw_value = value
        
        # 1. 基础基线 (Baseline)
        self.base_buf.append(value)
        if len(self.base_buf) > self.baseline_window:
            self.base_buf.pop(0)
        self.baseline_value = int(sum(self.base_buf) / len(self.base_buf))
        
        # 2. 计算平方差 (Variance)
        diff = value - self.baseline_value
        squared_diff = diff * diff
        
        # 3. 方差平滑 (Smoothed Variance)
        self.var_buf.append(squared_diff)
        if len(self.var_buf) > self.variance_window:
            self.var_buf.pop(0)
            
        # 这里为了在副Y轴上展示不至于数值过大（原数据可能差值100，平方就是10000）
        # 我们可以直接输出平滑后的平方差。为了和原有结构一致，我们放在 variance_smoothed 里。
        # 取方差的平滑值。为了方便调参，可能需要除以一个系数或者就保留原样，用户可以直接设置较大阈值。
        self.variance_smoothed = int(sum(self.var_buf) / len(self.var_buf))
        
        # 4. 判定状态
        if self.variance_smoothed > self.var_threshold:
            self.state = 1
        else:
            self.state = 0
            
        return {
            "raw": self.raw_value,
            "filtered": self.variance_smoothed,  # 借用 filtered 字段传给前端以统一数据格式
            "baseline": self.baseline_value,
            "threshold": self.var_threshold,
            "state": self.state
        }

class EnvelopeRangeAlgorithm:
    """
    包络范围算法
    在包络窗口内计算最大值和最小值，如果差值超过阈值则判定为有水。
    """
    def __init__(self, sensor_id: int, env_window: int = 30, dry_baseline_window_up: int = 1000, dry_baseline_window_down: int = 1000, upper_offset: int = 500, lower_offset: int = 300):
        self.sensor_id = sensor_id
        self.env_window = env_window
        self.dry_baseline_window_up = dry_baseline_window_up
        self.dry_baseline_window_down = dry_baseline_window_down
        self.upper_offset = upper_offset
        self.lower_offset = lower_offset
        self.reset()

    def reset(self):
        self.raw_value = 0
        self.env_upper = 0
        self.env_lower = 0
        self.dry_baseline = 0.0
        self.state = 0
        self.buf = []

    def process_point(self, value: int, timestamp: datetime) -> dict:
        self.raw_value = value
        
        self.buf.append(value)
        if len(self.buf) > self.env_window:
            self.buf.pop(0)
            
        self.env_upper = max(self.buf)
        self.env_lower = min(self.buf)
        
        diff = self.env_upper - self.env_lower
        
        # 1. 无水基准线追踪 (仅在无水时更新，使用一阶低通滤波/EMA)
        if self.state == 0:
            if self.dry_baseline == 0.0:
                self.dry_baseline = float(diff) # 初始化
            else:
                if diff > self.dry_baseline:
                    alpha = 2.0 / (self.dry_baseline_window_up + 1.0)
                else:
                    alpha = 2.0 / (self.dry_baseline_window_down + 1.0)
                self.dry_baseline = alpha * diff + (1.0 - alpha) * self.dry_baseline
                
        # 2. 双施密特滞回触发判定
        if self.state == 0:
            if diff > self.dry_baseline + self.upper_offset:
                self.state = 1
        else: # state == 1
            if diff < self.dry_baseline + self.lower_offset:
                self.state = 0
            
        return {
            "raw": self.raw_value,
            "filtered": self.env_upper,   # 借用 filtered 字段传给前端当上线
            "baseline": self.env_lower,   # 借用 baseline 字段传给前端当下线
            "threshold": int(self.dry_baseline), # 用 threshold 传回 dry_baseline 供图表绘制
            "state": self.state
        }

