"""本机 HTTP API，命令由 cfctl 执行。"""
from contextlib import ExitStack, suppress
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import xml.etree.ElementTree as ET

from cfctl import LOCAL, execute, failure, parser

FILE_ROUTE = "/v1/files/"
GROUPS = {"device", "snapshot", "doctor", "app", "ui", "input", "screen", "logs", "shell"}


class RequestError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def filename(value):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", value):
        raise RequestError(400, "需要不含目录的文件名")
    return value


def command(data, root):
    if not isinstance(data, dict) or set(data) - {"device", "args", "timeout"}:
        raise RequestError(400, "需要包含 args 和可选 device、timeout 的对象")
    argv = data.get("args")
    timeout = data.get("timeout")
    if (not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv)
            or argv[0] not in GROUPS or any(a in ("--help", "-h") for a in argv[:2])):
        raise RequestError(400, "args 需要以命令组开头的字符串数组")
    if "timeout" in data and (type(timeout) not in (int, float) or
                              not 0 < timeout <= 900 or not math.isfinite(timeout)):
        raise RequestError(400, "timeout 须大于 0 且不超过 900 秒")
    prefix = ["--timeout", str(timeout)] if timeout is not None else []
    if "device" in data:
        if not isinstance(data["device"], str):
            raise RequestError(400, "device 须为实例名")
        prefix.extend(["--device", data["device"]])
    try:
        args = parser().parse_args(prefix + argv)
    except SystemExit:
        raise RequestError(400, "无效的命令参数")
    if args.group == "app" and args.action == "install":
        args.apks = [root / filename(str(p)) for p in args.apks]
        if any(p.suffix.lower() != ".apk" for p in args.apks):
            raise RequestError(400, "需要已上传的 APK 文件名")
    if args.group == "screen":
        args.output = root / filename(str(args.output))
        if args.output.suffix.lower() != ".png":
            raise RequestError(400, "截图文件须使用 .png 扩展名")
    try:
        result = execute(args, artifacts=root / "diagnostics")
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError, ET.ParseError) as exc:
        error = failure(exc)
        if "artifacts" in error:
            error["artifacts"] = {k: FILE_ROUTE + str(Path(p).relative_to(root))
                                  for k, p in error["artifacts"].items()}
        return 422, error
    return 200, {"ok": True, "result": args.output.name if args.group == "screen" else result}


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def json_response(self, code, value):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorize(self):
        if self.headers.get("Origin") is not None:
            raise RequestError(403, "不接受浏览器跨源请求")
        auth = self.headers.get("Authorization", "").encode()
        if not hmac.compare_digest(auth, b"Bearer " + self.server.token):
            raise RequestError(401, "需要有效的 Bearer token")

    def length(self, limit):
        if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) != 1:
            raise RequestError(411, "需要 Content-Length")
        try:
            length = int(self.headers["Content-Length"])
        except ValueError:
            raise RequestError(400, "无效的 Content-Length")
        if not 0 < length <= limit:
            raise RequestError(413, "请求大小超出范围")
        return length

    def file_request(self):
        path = self.path[len(FILE_ROUTE):]
        root = self.server.files
        if self.command == "PUT":
            dest = root / filename(path)
            if dest.suffix.lower() != ".apk":
                raise RequestError(400, "上传文件须使用 .apk 扩展名")
            remaining = self.length(512 * 1024 ** 2)
            with tempfile.NamedTemporaryFile(dir=root, delete=False) as tmp:
                try:
                    while remaining:
                        chunk = self.rfile.read(min(remaining, 1024 ** 2))
                        if not chunk:
                            raise RequestError(400, "上传数据不完整")
                        tmp.write(chunk)
                        remaining -= len(chunk)
                    tmp.close()
                    os.replace(tmp.name, dest)
                finally:
                    Path(tmp.name).unlink(missing_ok=True)
            self.json_response(201, {"ok": True, "result": dest.name})
        elif self.command == "GET":
            parts = path.split("/")
            if len(parts) > 1 and not (len(parts) == 3 and parts[0] == "diagnostics"):
                raise RequestError(404, "未知文件")
            src = root.joinpath(*(filename(p) for p in parts))
            if not src.is_file() or root not in src.resolve().parents:
                raise RequestError(404, "文件不存在")
            with src.open("rb") as stream:
                self.send_response(200)
                self.send_header("Content-Type", {"png": "image/png", "xml": "application/xml"}.get(
                    src.suffix[1:], "application/octet-stream"))
                self.send_header("Content-Length", str(os.fstat(stream.fileno()).st_size))
                self.end_headers()
                shutil.copyfileobj(stream, self.wfile)
        else:
            raise RequestError(405, "该路由支持 PUT 和 GET")

    def rpc(self):
        if self.headers.get_content_type() != "application/json":
            raise RequestError(415, "需要 application/json")
        data = json.loads(self.rfile.read(self.length(64 * 1024)))
        self.json_response(*command(data, self.server.files))

    def route(self):
        try:
            self.authorize()
            if self.command == "POST" and self.path == "/v1/command":
                self.rpc()
            elif self.path.startswith(FILE_ROUTE):
                self.file_request()
            else:
                raise RequestError(404, "未知路由")
        except RequestError as exc:
            self.json_response(exc.status, failure(exc))
        except (ValueError, OSError) as exc:
            with suppress(OSError):
                self.json_response(400, failure(exc))

    do_POST = do_GET = do_PUT = route


def websocket_server(port, token, files):
    try:
        from websockets.sync.server import serve as ws_serve
    except ImportError as exc:
        raise ValueError("WebSocket 需要 websockets，使用 uv run --with websockets==15.0.1 启动") from exc

    def authorize(conn, request):
        if request.path != "/v1/command":
            return conn.respond(404, "Unknown route")
        values = request.headers.get_all("Authorization")
        if len(values) != 1 or not hmac.compare_digest(values[0].encode(), b"Bearer " + token):
            return conn.respond(401, "Unauthorized")
        return None

    def handle(conn):
        for message in conn:
            try:
                if not isinstance(message, str):
                    raise ValueError("需要 JSON 文本消息")
                _, result = command(json.loads(message), files)
            except (ValueError, OSError) as exc:
                result = failure(exc)
            conn.send(json.dumps(result, ensure_ascii=False))

    return ws_serve(handle, "127.0.0.1", port, origins=[None], process_request=authorize,
                    compression=None, max_size=64 * 1024)


def serve(port, ws_port=None):
    token = os.environ.get("CF_API_TOKEN", "")
    if not re.fullmatch(r"[A-Za-z0-9._~-]{16,256}", token):
        raise ValueError("请设置 CF_API_TOKEN，使用 16 到 256 个字母、数字或 ._~-")
    if not 0 <= port <= 65535 or ws_port is not None and not 0 <= ws_port <= 65535:
        raise ValueError("API 端口须在 0 到 65535 之间")
    files = Path(os.environ.get("CF_API_FILES", LOCAL / "api-files")).resolve()
    files.mkdir(mode=0o700, parents=True, exist_ok=True)
    with ThreadingHTTPServer(("127.0.0.1", port), Handler) as server, ExitStack() as stack:
        server.token = token.encode()
        server.files = files
        if ws_port is not None:
            ws = websocket_server(ws_port, server.token, files)
            thread = threading.Thread(target=ws.serve_forever, daemon=True)
            thread.start()
            stack.callback(thread.join, 3)
            stack.callback(ws.shutdown)
        print(f"http://127.0.0.1:{server.server_port}", flush=True)
        if ws_port is not None:
            print(f"ws://127.0.0.1:{ws.socket.getsockname()[1]}/v1/command", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
