"""在专用 netsimd 的 gRPC 端点上提供带加密要求的 GATT 回显特征。"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import secrets
import subprocess


SERVICE = "31ec2624-6bd9-48e4-af07-69570abe8c80"
ECHO = "31ec2624-6bd9-48e4-af07-69570abe8c81"


def instance_port(directory):
    cfg = json.loads((directory / "cuttlefish/assembly/cuttlefish_config.json").read_text())
    item = cfg["instances"]["1"]
    log = (Path(item["instance_dir"]) / "logs/launcher.log").read_text(errors="replace")
    connectors = re.findall(r"\] --connector_instance_num=(\d+)\s*$", log, re.M)
    index = int(connectors[-1]) if connectors else 1
    ini = "netsim.ini" if index == 1 else f"netsim_{index}.ini"
    data = (Path(os.environ.get("TMPDIR") or "/tmp") / ini).read_text()
    match = re.search(r"^grpc\.port=(\d+)\s*$", data, re.M)
    if not match:
        raise ValueError(f"{ini} 缺少 gRPC 端口")
    port = int(match[1])
    devices = subprocess.check_output([directory / "bin/netsim", "-p", str(port), "devices"],
                                      text=True, timeout=8)
    if not re.search(rf"^\s*(?:0\.0\.0\.0|127\.0\.0\.1):{int(item['adb_host_port'])}\s*$", devices, re.M):
        raise RuntimeError(f"{ini} 指向的 netsimd 未列出目标来宾")
    return port


async def run(port):
    from bumble.core import AdvertisingData
    from bumble.device import Device
    from bumble.gatt import Characteristic, CharacteristicValue, Service
    from bumble.hci import Address
    from bumble.keys import MemoryKeyStore
    from bumble.pairing import PairingConfig, PairingDelegate
    from bumble.transport import open_transport

    address = bytearray(secrets.token_bytes(6))
    address[0] |= 0xc0
    name = "CF-" + address.hex().upper()
    async with await open_transport(f"android-netsim:localhost:{port},name={name}") as hci:
        device = Device.with_hci(name, Address(address.hex(":")), hci.source, hci.sink)
        device.keystore = MemoryKeyStore()
        device.pairing_config_factory = lambda _: PairingConfig(
            sc=True, mitm=False, bonding=True, identity_address_type=PairingConfig.AddressType.RANDOM,
            delegate=PairingDelegate())
        values = {}
        def write(connection, value):
            values[connection.handle] = value
        char = Characteristic(ECHO, Characteristic.READ | Characteristic.WRITE,
                              Characteristic.READABLE | Characteristic.WRITEABLE |
                              Characteristic.READ_REQUIRES_ENCRYPTION | Characteristic.WRITE_REQUIRES_ENCRYPTION,
                              CharacteristicValue(read=lambda connection: values.get(connection.handle, b""), write=write))
        device.add_service(Service(SERVICE, [char]))
        await device.power_on()
        ad = AdvertisingData([(AdvertisingData.FLAGS, b"\x06"),
                              (AdvertisingData.COMPLETE_LOCAL_NAME, name.encode("ascii"))])
        await device.start_advertising(advertising_data=bytes(ad), auto_restart=True)
        print(address.hex(":").upper(), flush=True)
        await asyncio.Event().wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--port", type=int, help="明确指定的本机 gRPC 端口或 SSH 转发端口")
    source.add_argument("--run", type=Path, help="从本机运行目录核对 netsimd 与实例 1")
    args = parser.parse_args()
    asyncio.run(run(instance_port(args.run.resolve()) if args.run else args.port))
