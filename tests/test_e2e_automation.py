import os
import sys
import time
import json
import socket
import urllib.request
import urllib.error
import subprocess
import threading

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT_DIR, "src"))

PORT = 8000
BASE_URL = f"http://127.0.0.1:{PORT}"

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

def log(msg, status="INFO"):
    icons = {"INFO": "[INFO]", "PASS": "[PASS]", "FAIL": "[FAIL]", "WARN": "[WARN]"}
    print(f"{icons.get(status, '[INFO]')} {msg}")

def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0

def http_get(path, timeout=5):
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "AutoTester/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()

def http_post(path, data=None, headers=None, timeout=5):
    url = f"{BASE_URL}{path}"
    h = {"User-Agent": "AutoTester/1.0"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()

def run_all_tests():
    print("=" * 60)
    print("      Water Sensor Logger 自动化端到端全量自测套件")
    print("=" * 60)

    server_process = None
    # 1. 检查或启动服务
    if not is_port_in_use(PORT):
        log(f"端口 {PORT} 未监听，正在自动拉起测试 Web Server (src/server.py)...")
        server_process = subprocess.Popen(
            [sys.executable, os.path.join(ROOT_DIR, "src", "server.py")],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        time.sleep(2.5)

    if not is_port_in_use(PORT):
        log("无法连接到服务，启动失败！", "FAIL")
        if server_process:
            out, err = server_process.communicate(timeout=2)
            print(out.decode('utf-8', errors='ignore'))
            print(err.decode('utf-8', errors='ignore'))
        return False

    log(f"已成功建立与 Web 服务的通信链路 ({BASE_URL})", "PASS")

    total_tests = 0
    passed_tests = 0

    def assert_test(name, condition, extra_info=""):
        nonlocal total_tests, passed_tests
        total_tests += 1
        if condition:
            passed_tests += 1
            log(f"{name} {extra_info}", "PASS")
            return True
        else:
            log(f"{name} FAILED! {extra_info}", "FAIL")
            return False

    try:
        # Test 1: HTML 主页面加载与核心 DOM 节点验证
        status, content = http_get("/")
        html_str = content.decode("utf-8")
        assert_test("测试 1.1: GET / (HTML 状态码 200)", status == 200)
        assert_test("测试 1.2: DOM 中包含图表容器 #chart", 'id="chart"' in html_str)
        assert_test("测试 1.3: DOM 中包含日期选择器 #date-select", 'id="date-select"' in html_str)
        assert_test("测试 1.4: DOM 中包含算法选择器 #algo-select", 'id="algo-select"' in html_str)
        assert_test("测试 1.5: 正确引用本地 ECharts 与模块脚本", '/static/js/app.js' in html_str and 'echarts.min.js' in html_str)

        # Test 2: 所有静态资源文件完整性与可访问性
        static_files = [
            "/static/js/echarts.min.js",
            "/static/css/style.css",
            "/static/js/app.js",
            "/static/js/chart.js",
            "/static/js/api.js",
            "/static/js/modal.js"
        ]
        for s_url in static_files:
            st, data = http_get(s_url)
            assert_test(f"测试 2: 静态资源 {s_url} (200 OK)", st == 200 and len(data) > 0, f"大小: {len(data):,} 字节")

        # Test 3: 前端 JS 语法安全与严格模式静态检查 (确保无未声明变量/SyntaxError)
        js_dir = os.path.join(ROOT_DIR, "src", "static", "js")
        for jf in ["app.js", "chart.js", "api.js", "modal.js"]:
            with open(os.path.join(js_dir, jf), "r", encoding="utf-8") as f:
                code = f.read()
            # 验证未发生未定义变量裸赋值
            import re
            lines = code.split("\n")
            undeclared = []
            for idx, line in enumerate(lines):
                m = re.match(r'^\s*([a-zA-Z_$][a-zA-Z0-9_$]*)\s*=[^=]', line)
                if m:
                    varname = m.group(1)
                    if varname not in ["window", "export", "import"]:
                        if not re.search(r'\b(let|const|var|function|import)\s+' + re.escape(varname) + r'\b', code):
                            undeclared.append((idx + 1, varname))
            assert_test(f"测试 3: JS 严格模式静态检测 ({jf})", len(undeclared) == 0, f"未声明变量数: {len(undeclared)}")

        # Test 4: 可选日期查询接口 /api/available_dates
        st, data = http_get("/api/available_dates")
        dates = json.loads(data.decode("utf-8"))
        assert_test("测试 4.1: GET /api/available_dates", st == 200 and isinstance(dates, list) and len(dates) > 0, f"共返回 {len(dates)} 天数据: 最新 [{dates[0]}]")
        test_date = dates[0]

        # Test 5: 站点配置查询接口 /api/station_config
        st, data = http_get("/api/station_config")
        cfg = json.loads(data.decode("utf-8"))
        assert_test("测试 5: GET /api/station_config", st == 200 and "station_name" in cfg, f"站点名称: {cfg.get('station_name')}")

        # Test 6: 自动抓拍配置查询接口 /api/auto_capture
        st, data = http_get("/api/auto_capture")
        ac_cfg = json.loads(data.decode("utf-8"))
        assert_test("测试 6: GET /api/auto_capture", st == 200 and "auto_capture" in ac_cfg)

        # Test 7: 照片列表接口 /api/photos
        st, data = http_get(f"/api/photos?date={test_date}")
        photos = json.loads(data.decode("utf-8"))
        assert_test(f"测试 7: GET /api/photos?date={test_date}", st == 200 and isinstance(photos, list))

        # Test 8: 核心算法矩阵全量计算测试 (动态施密特迟滞 / 离散方差 / 包络范围)
        for algo in ["dynamic", "discrete", "envelope"]:
            t0 = time.time()
            st, data = http_get(f"/api/history?date={test_date}&algorithm={algo}&downsample=5")
            elapsed = time.time() - t0
            hist = json.loads(data.decode("utf-8"))
            valid = (
                st == 200
                and "timestamps" in hist
                and "sensor1" in hist
                and "sensor2" in hist
                and "sensor3" in hist
                and len(hist["timestamps"]) == len(hist["sensor1"]["raw"])
            )
            assert_test(
                f"测试 8 [{algo.upper()} 算法推演]: 数据结构完整性与时延",
                valid,
                f"点数: {len(hist.get('timestamps', [])):,}, 耗时: {elapsed:.3f}s"
            )

        # Test 9: 历史存量数据测试 (测试 2026-07-10)
        t0 = time.time()
        st, data = http_get("/api/history?date=2026-07-10&algorithm=dynamic&downsample=1")
        hist_0710 = json.loads(data.decode("utf-8"))
        assert_test(
            "测试 9 [历史数据 2026-07-10 回溯测试]:",
            st == 200 and len(hist_0710["timestamps"]) > 0,
            f"点数: {len(hist_0710['timestamps']):,}"
        )

        # Test 10: 实时数据轮询接口 /api/realtime
        st, data = http_get("/api/realtime?algorithm=dynamic")
        rt = json.loads(data.decode("utf-8"))
        assert_test("测试 10: GET /api/realtime 实时尾部点提取", st == 200 and "sensor1" in rt)

        # Test 11: SSE 实时推送链路测试 (验证 snapshot 快照正常产生)
        def test_sse_stream():
            url = f"{BASE_URL}/api/stream?algorithm=dynamic&poll_interval=0.5"
            req = urllib.request.Request(url, headers={"User-Agent": "AutoTester/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                first_chunk = resp.readline().decode("utf-8")
                # 寻找 data: 开头
                while first_chunk and not first_chunk.startswith("data: "):
                    first_chunk = resp.readline().decode("utf-8")
                if first_chunk.startswith("data: "):
                    payload = json.loads(first_chunk[6:])
                    return payload.get("type") == "snapshot"
            return False

        try:
            sse_ok = test_sse_stream()
            assert_test("测试 11: GET /api/stream (SSE 实时流快照推送验证)", sse_ok)
        except Exception as e:
            assert_test("测试 11: GET /api/stream", False, str(e))

    finally:
        if server_process:
            server_process.terminate()
            server_process.wait()

    print("=" * 60)
    print(f"  自动化测试完成: 共执行 {total_tests} 项测试，通过 {passed_tests} 项，失败 {total_tests - passed_tests} 项")
    print("=" * 60)
    return total_tests == passed_tests

if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
