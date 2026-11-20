"""在本项目精简检出的 AOSP 上应用固定版本的 Meta 补丁。"""

from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import xml.etree.ElementTree as ET

base = Path(__file__).resolve().parent / ".local-cf"
meta = base / "src/cuttlefish-wearables"
aosp = base / "aosp-volume/aosp"
sys.path.insert(0, str(meta / "scripts"))
from apply_patches import apply_patch_series, check_project, find_patch_series, git_output

if __name__ == "__main__":
    series = find_patch_series(meta / "patches")
    revisions = {p.get("path", p.get("name")): p.get("revision")
                 for p in ET.parse(aosp / "aosp-base.xml").findall("project")}
    for name, patches in series:
        project = aosp / name
        if git_output(project, "rev-parse", "HEAD") != revisions[str(name)]:
            raise SystemExit(f"项目已偏离基线 {name}")
        if str(name) != "prebuilts/rust":
            check_project(project, aosp)
            continue

        # 此补丁只修改已完整检出的 Android.bp。对该文件检查索引、工作树和补丁。
        if len(patches) != 1 or git_output(project, "ls-files", "-v", "Android.bp") != "H Android.bp":
            raise SystemExit("Rust 补丁或 Android.bp 的检出状态与预期不符")
        if git_output(project, "status", "--porcelain", "--untracked-files=all"):
            raise SystemExit("Rust 工作目录存在修改")
        with TemporaryDirectory(dir=aosp.parent / "tmp") as tmp:
            copy = Path(tmp) / "Android.bp"
            copy.write_bytes((project / "Android.bp").read_bytes())
            subprocess.run(["git", "apply", "--no-index", "--check", patches[0]], cwd=tmp, check=True)
        subprocess.run(["git", "-C", project, "apply", "--check", "--index", patches[0]], check=True)

    for name, patches in series:
        apply_patch_series(aosp / name, patches, three_way=False)
    print(f"已应用 {sum(len(patches) for _, patches in series)} 个补丁，覆盖 {len(series)} 个项目。")
