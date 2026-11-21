"""通过专用 VirtWifi 网卡在 Android 来宾和宿主回环服务间收发数据。"""
from concurrent.futures import ThreadPoolExecutor
import re
import secrets
import socket
import time

from parity import require

SSID = "VirtWifi"


def wait_for(read, message, timeout=25):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        result = read()
        if result:
            return result
        time.sleep(0.4)
    raise RuntimeError(message)


def exchange(media):
    request = secrets.token_hex(32)
    reply = secrets.token_hex(32)
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        server.settimeout(15)

        def serve():
            conn, _ = server.accept()
            with conn, conn.makefile("rb") as stream:
                conn.settimeout(6)
                require(stream.read(len(request)) == request.encode("ascii"), "宿主收到的 Wi-Fi 请求不符")
                conn.sendall(reply.encode("ascii"))

        with ThreadPoolExecutor(max_workers=1) as worker:
            received = worker.submit(serve)
            ip = media("wifi-data", "-e", "port", str(server.getsockname()[1]), "-e", "request", request,
                       "-e", "reply", reply)
            received.result()
    return {"guest_ipv4": ip, "interface": "wlan0", "gateway": "10.0.2.2",
            "request_bytes": len(request), "reply_bytes": len(reply)}


def verify_wifi(shell, media):
    def wifi(*args):
        return shell("cmd", "wifi", *args)

    def networks():
        return set(line.strip() for line in wifi("list-networks").splitlines() if re.match(r"^\s*\d+\s+", line))

    def connected(status):
        match = re.search(r'^Wifi is connected to "([^"]+)"', status, re.M)
        return match[1] if match else None

    def is_enabled(status):
        match = re.search(r"^Wifi is (enabled|disabled)$", status, re.M)
        require(match is not None, "无法读取 Wi-Fi 开关状态")
        return match[1] == "enabled"

    initial = wifi("status")
    enabled = is_enabled(initial)
    previous = connected(initial)
    require(previous in (None, SSID), "实例已连接其他网络，请使用专用测试实例")
    saved = networks()
    row = re.compile(r"^(\d+)\s+VirtWifi\s+")
    if previous is None:
        require(not any(row.match(line) for line in saved), "实例已有未连接的 VirtWifi 配置")
    attempted = False
    try:
        if not enabled:
            wifi("set-wifi-enabled", "enabled")
        wait_for(lambda: is_enabled(wifi("status")), "Wi-Fi 未启用")
        if previous is None:
            wifi("start-scan")
            wait_for(lambda: re.search(r"\sVirtWifi\s+\[ESS\]\s*$", wifi("list-scan-results"), re.M),
                     "未发现开放的 VirtWifi，请核对来宾的 virt_wifi 后端")
            attempted = True
            wifi("connect-network", SSID, "open")
        wait_for(lambda: connected(wifi("status")) == SSID, "来宾未关联 VirtWifi")
        address = wait_for(lambda: re.search(r"\binet (\d+(?:\.\d+){3})/", shell("ip", "-4", "addr", "show", "wlan0")),
                           "wlan0 未取得 IPv4 地址")
        result = exchange(media)
        require(result["guest_ipv4"] == address[1], "TCP 源地址与 wlan0 地址不一致")
        result.update(ssid=SSID, initial_enabled=enabled)
    finally:
        try:
            if attempted:
                created = {row.match(line)[1] for line in networks() - saved if row.match(line)}
                for network_id in created:
                    wifi("forget-network", network_id)
        finally:
            if not enabled:
                wifi("set-wifi-enabled", "disabled")
        wait_for(lambda: is_enabled(wifi("status")) == enabled and connected(wifi("status")) == previous,
                 "Wi-Fi 开关或关联状态未恢复")
        require(networks() == saved, "已保存网络列表与测试前不一致")
    return dict(result, restored=True)
