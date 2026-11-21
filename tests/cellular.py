"""用 Cuttlefish modem_simulator 的本地控制套接字发起模拟来电。"""
import platform
import re
import socket

from parity import require
from wifi_data import wait_for


def incoming_call(shell, config):
    require(config["enable_modem_simulator"] and config["modem_simulator_instance_number"] == 1,
            "需要单 SIM 模拟调制解调器")
    def state():
        match = re.search(r"^\s*mCallState=(\d+)\s*$", shell("dumpsys", "telephony.registry"), re.M)
        require(match is not None, "无法读取通话状态")
        return int(match[1])

    require(state() == 0, "测试实例已有通话")
    modem = "modem_simulator" + str(int(config["modem_simulator_host_id"]))
    endpoint = "/tmp/" + modem if platform.system() == "Darwin" else "\0" + modem
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
        peer.settimeout(10)
        peer.connect(endpoint)
        peer.sendall(b'REM0AT+REMOTECALL=4,0,0,"2025550142",129\r')
        try:
            wait_for(lambda: state() == 1, "未收到模拟来电")
            shell("input", "keyevent", "KEYCODE_CALL")
            wait_for(lambda: state() == 2, "来宾未接听")
            with peer.makefile("rb") as stream:
                data = bytearray()
                while b"AT+REMOTECALL=0," not in data:
                    chunk = stream.read1(256)
                    require(chunk and len(data) < 4096, "模拟对端未收到接听响应")
                    data.extend(chunk)
        finally:
            peer.sendall(b'AT+REMOTECALL=6,0,0,"2025550142",129\r')
            wait_for(lambda: state() == 0, "挂断后未恢复空闲")
    return {"states": ["idle", "ringing", "offhook", "idle"], "peer_confirmed_answer": True}
