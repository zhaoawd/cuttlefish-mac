"""在已准备的 Apple Silicon 测试宿主执行全部 unittest，跳过用例也返回失败。"""
import os
from pathlib import Path
import platform
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


def main():
    if (platform.system(), platform.machine()) != ("Darwin", "arm64"):
        raise SystemExit("需要 Apple Silicon macOS 宿主")
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("GUEST_ZIP", str(
        ROOT / ".local-cf/aosp-volume/guest/aosp_cf_arm64_only_phone-img-14781626.zip"))
    os.environ.setdefault("UNPACK_BOOTIMG", str(ROOT / ".local-cf/aosp-volume/probe/unpack_bootimg.py"))
    os.environ.setdefault("CF_TEST_STABILITY_SECONDS", "120")
    for name in ("CLI", "BATCH", "WS", "PHONE", "QMP", "DARWIN_CYCLE", "FASTBOOT", "COCOA_TRACE", "COCOA_WINDOW"):
        os.environ["CF_TEST_" + name] = "1"
    for key in ("GUEST_ZIP", "UNPACK_BOOTIMG"):
        if not Path(os.environ[key]).is_file():
            raise SystemExit(f"{key} 指定的文件不存在")
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.testsRun and result.wasSuccessful() and not result.skipped else 1


if __name__ == "__main__":
    sys.exit(main())
