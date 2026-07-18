from datetime import datetime
from collections import deque

class SensorAlgorithm:
    """
    水位传感器通道核心算法 (Python 版)
    复刻 C++ 端滑动平均滤波、环境基准线自适应追踪以及双向施密特迟滞触发状态机。

    Fix #4: 所有滑动窗口均改为 collections.deque(maxlen=N) + 增量 sum，
            时间复杂度从 O(N) 降至 O(1)，避免全天百万级运算的性能瓶颈。
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
        
        # Fix #4: 改用 deque(maxlen) 替代 list，自动淘汰旧元素，无需 pop(0)
        self.ma_buf   = deque(maxlen=self.ma_window)
        self.base_buf = deque(maxlen=self.baseline_window)
        # 增量 sum 缓存，避免每次 O(N) 的 sum()
        self._ma_sum   = 0
        self._base_sum = 0
        
        # 有水状态的起始时间戳（类型为 datetime，用于看门狗）
        self.has_water_start_time = None

    def push_filter(self, value: int) -> int:
        """
        滑窗平滑滤波器 (Moving Average) — O(1) 实现
        """
        if len(self.ma_buf) == self.ma_window:
            # 窗口已满，减去即将被淘汰的最旧元素
            self._ma_sum -= self.ma_buf[0]
        self.ma_buf.append(value)
        self._ma_sum += value
        return int(self._ma_sum / len(self.ma_buf))

    def push_baseline(self, value: int) -> int:
        """
        自适应基准线追踪滑窗 (以 Filtered 后的值为输入) — O(1) 实现
        """
        if len(self.base_buf) == self.baseline_window:
            self._base_sum -= self.base_buf[0]
        self.base_buf.append(value)
        self._base_sum += value
        return int(self._base_sum / len(self.base_buf))

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
                # Fix #5: WDT 触发后立即 early return，跳过后续状态机重评估。
                # 原代码在复位 state=0 后仍继续运行状态机，若信号仍高于阈值，
                # 状态会在同一帧内立即切回 1，导致 WDT 完全失效。
                self.state = 0
                self.has_water_start_time = None
                threshold = self.get_threshold()
                return {
                    "raw": self.raw_value,
                    "filtered": self.filtered_value,
                    "baseline": self.baseline_value,
                    "threshold": threshold,
                    "state": self.state
                }
        else:
            self.has_water_start_time = None

        # 2. 状态机评估
        threshold = self.get_threshold()
        current_state = self.state
        next_state = current_state

        # Q2=A：支持负偏移（threshold_offset < 0 时信号下降触发有水，对齐 C++ 端行为）
        if current_state == 0:  # 当前无水
            if self.threshold_offset >= 0:
                next_state = 1 if self.filtered_value > threshold else 0
            else:  # 负偏移：信号低于（更低的）阈值时触发有水
                next_state = 1 if self.filtered_value < threshold else 0
        else:                   # 当前有水
            if self.threshold_offset >= 0:
                next_state = 0 if self.filtered_value < threshold else 1
            else:  # 负偏移：信号高于（更高的）阈值时恢复无水
                next_state = 0 if self.filtered_value > threshold else 1

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
    使用大均値窗口计算基准线，计算离散方差后使用小窗口平滑，超过阈値则触发有水状态。

    Fix #4: base_buf / var_buf 同步改为 deque(maxlen) + 增量 sum。
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
        
        # Fix #4: deque + 增量 sum
        self.base_buf  = deque(maxlen=self.baseline_window)
        self.var_buf   = deque(maxlen=self.variance_window)
        self._base_sum = 0
        self._var_sum  = 0

    def process_point(self, value: int, timestamp: datetime) -> dict:
        self.raw_value = value
        
        # 1. 基础基线 (Baseline) — O(1)
        if len(self.base_buf) == self.baseline_window:
            self._base_sum -= self.base_buf[0]
        self.base_buf.append(value)
        self._base_sum += value
        self.baseline_value = int(self._base_sum / len(self.base_buf))
        
        # 2. 计算平方差 (Variance)
        diff = value - self.baseline_value
        squared_diff = diff * diff
        
        # 3. 方差平滑 (Smoothed Variance) — O(1)
        # 这里为了在副Y轴上展示不至于数値过大（原数据可能差値100，平方就是10000）
        # 我们可以直接输出平滑后的平方差。为了和原有结构一致，我们放在 variance_smoothed 里。
        # 取方差的平滑値。为了方便调参，可能需要除以一个系数或者就保留原样，用户可以直接设置较大阈値。
        if len(self.var_buf) == self.variance_window:
            self._var_sum -= self.var_buf[0]
        self.var_buf.append(squared_diff)
        self._var_sum += squared_diff
        self.variance_smoothed = int(self._var_sum / len(self.var_buf))
        
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
    在包络窗口内计算最大値和最小値，如果差値超过阈値则判定为有水。

    Fix #4: buf 改为 deque(maxlen)，自动管理窗口，无需 pop(0)。
    Fix #6: dry_baseline 初始标志从 0.0 改为 None，消除“diff”恰好为 0 时
            初始化不触发的歧义。
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
        # Fix #6: 使用 None 作为“未初始化”标志，语义明确，避免 diff==0 时的歧义
        self.dry_baseline = None
        self.state = 0
        # Fix #4: deque(maxlen) 自动管理窗口，无需手动 pop(0)
        self.buf = deque(maxlen=self.env_window)

    def process_point(self, value: int, timestamp: datetime) -> dict:
        self.raw_value = value
        
        self.buf.append(value)
            
        self.env_upper = max(self.buf)
        self.env_lower = min(self.buf)
        
        diff = self.env_upper - self.env_lower
        
        # 1. 无水基准线追踪 (仅在无水时更新，使用一阶低通滤波/EMA)
        if self.state == 0:
            if self.dry_baseline is None:
                # Fix #6: 用 None 判断初始化，而非 0.0
                self.dry_baseline = float(diff)
            else:
                if diff > self.dry_baseline:
                    alpha = 2.0 / (self.dry_baseline_window_up + 1.0)
                else:
                    alpha = 2.0 / (self.dry_baseline_window_down + 1.0)
                self.dry_baseline = alpha * diff + (1.0 - alpha) * self.dry_baseline
                
        # 2. 双施密特滞回触发判定（使用局部变量 dry 安全访问，防止 dry_baseline 仍为 None 时运算出错）
        dry = self.dry_baseline if self.dry_baseline is not None else 0.0
        if self.state == 0:
            if diff > dry + self.upper_offset:
                self.state = 1
        else:  # state == 1
            if diff < dry + self.lower_offset:
                self.state = 0

        return {
            "raw": self.raw_value,
            "filtered": self.env_upper,    # 借用 filtered 字段传给前端当上线
            "baseline": self.env_lower,    # 借用 baseline 字段传给前端当下线
            "threshold": int(dry),         # 用 threshold 传回 dry_baseline 供图表绘制
            "state": self.state
        }
