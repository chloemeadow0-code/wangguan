"""
通用 ASGI 网关中间件 (Generic ASGI Gateway Middleware)
======================================================
负责：
- 修正反代场景下的 Host 头
- 统一处理 CORS 预检
- 🔐 全局 API 安全拦截（校验 API_SECRET，对 /sse /messages /api/* 强制鉴权）
- 暴露一组管理 / 健康检查 / 配置接口
- 将业务请求转发给下游 MCP 应用

所有敏感配置均从环境变量读取，无硬编码。
"""

import os
import json
import asyncio
import requests


class HostFixMiddleware:
    """
    ASGI 中间件：
    1. 对管理类 HTTP 接口直接返回，不进入下游应用
    2. 对 /sse /messages /api/* 强制校验 API_SECRET（照抄桌面可跑通版本）
    3. 对其余请求修正 Host 头后透传给下游 app
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # ---------- NapCat 反向 WebSocket 端点 ----------
        if scope["type"] == "websocket" and scope["path"] == "/qq-ws":
            try:
                import napcat
                await napcat.handle_napcat_ws(scope, receive, send)
            except Exception as e:
                print(f"❌ NapCat WS 处理异常: {e}")
            return

        # 非 HTTP 类型直接透传给下游
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # ---------- 健康检查 ----------
        if scope["path"] == "/health":
            await _send_json_resp(send, 200, {"status": "ok", "service": "generic-mcp-gateway"})
            return

        # 🛡️ 全局 API 安全拦截 (涵盖自定义接口与底层 MCP 引擎端点 /sse /messages，并放行 OPTIONS 跨域预检)
        # 照抄桌面版(能跑通的版本)的校验逻辑：客户端必须带正确的 API_SECRET 才能访问
        # 这是修复 421 / "Request validation failed" 的关键：MCP 客户端需带正确密钥才能连上 /sse
        if (scope["path"].startswith("/api/") or scope["path"].startswith("/sse") or scope["path"].startswith("/messages")) and scope["method"] != "OPTIONS":
            headers_dict = {k.decode("utf-8").lower(): v.decode("utf-8") for k, v in scope.get("headers", [])}
            auth_token = headers_dict.get("authorization", "").replace("Bearer ", "").replace("bearer ", "").strip()
            x_api_key = headers_dict.get("x-api-key", "").strip()

            API_SECRET = os.environ.get("API_SECRET", "").strip()
            # 校验密钥 (如果没有配置 API_SECRET，为了安全，直接默认拒绝所有外部请求)
            if not API_SECRET or (auth_token != API_SECRET and x_api_key != API_SECRET):
                await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"application/json"), (b"access-control-allow-origin", b"*")]})
                await send({"type": "http.response.body", "body": b'{"error":"Unauthorized: Missing or invalid API key"}'})
                return

        # ---------- CORS 预检 ----------
        if scope["method"] == "OPTIONS":
            await _send_cors_preflight(send)
            return

        # ---------- 配置热更新接口 ----------
        if scope["path"] == "/api/config" and scope["method"] == "POST":
            await self._handle_config_update(receive, send)
            return

        # ---------- 运行日志接口 ----------
        if scope["path"] == "/api/logs":
            await self._handle_logs(send)
            return

        # ---------- 服务重启接口 (通用云平台占位) ----------
        if scope["path"] == "/api/restart" and scope["method"] == "POST":
            await self._handle_restart(send)
            return

        # ---------- 兜底其余请求 (Host Fix) ----------
        # 照抄桌面版(能跑通的版本)：把 Host 头改成下游 MCP 应用期望的值。
        # 注意：Host Fix 不是 421 的原因（桌面版同样改 Host 却能跑），421 是因为上面缺少 API_SECRET 校验。
        # 但为了让下游 MCP 引擎（FastMCP/Starlette）能正确生成 SSE 回调地址，仍需统一 Host。
        headers = dict(scope.get("headers", []))
        headers[b"host"] = b"localhost:8000"
        scope["headers"] = list(headers.items())

        await self.app(scope, receive, send)

    # ------------------------------------------
    # 子处理函数
    # ------------------------------------------

    async def _handle_config_update(self, receive, send):
        """接收前端推送的配置 JSON，写入环境变量。"""
        try:
            body = b""
            while True:
                msg = await receive()
                body += msg.get("body", b"")
                if not msg.get("more_body", False):
                    break

            req_data = json.loads(body.decode("utf-8"))

            # 将配置项映射到环境变量 (key 直接透传为大写)
            for key, value in req_data.items():
                if value:
                    os.environ[str(key).upper()] = str(value).strip()

            await _send_json_resp(send, 200, {"status": "ok"})
        except Exception as e:
            await _send_json_resp(send, 500, {"error": str(e)})

    async def _handle_logs(self, send):
        """返回最近的运行日志 (占位实现)。"""
        try:
            # 通用版：从环境变量指定的日志文件读取，或返回占位信息
            log_file = os.environ.get("LOG_FILE", "")
            if log_file and os.path.exists(log_file):
                with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()[-100:]
                await _send_json_resp(send, 200, {"logs": "".join(lines)})
            else:
                await _send_json_resp(send, 200, {"logs": "（日志功能未配置，请设置 LOG_FILE 环境变量）"})
        except Exception as e:
            await _send_json_resp(send, 500, {"error": str(e)})

    async def _handle_restart(self, send):
        """
        通用服务重启接口。
        不同云平台的重启方式不同，这里通过环境变量配置重启回调 URL。
        若未配置，返回提示信息。
        """
        restart_url = os.environ.get("RESTART_WEBHOOK_URL", "").strip()
        if not restart_url:
            await _send_json_resp(send, 400, {
                "success": False,
                "error": "未配置 RESTART_WEBHOOK_URL，请在环境变量中设置云平台的重启回调地址"
            })
            return

        try:
            def _call():
                return requests.post(restart_url, timeout=15)
            resp = await asyncio.to_thread(_call)
            await _send_json_resp(send, 200, {
                "success": True,
                "status_code": resp.status_code
            })
        except Exception as e:
            await _send_json_resp(send, 500, {"success": False, "error": str(e)})


# ==========================================
# 辅助函数
# ==========================================

async def _send_json_resp(send, status: int, data: dict):
    """统一的 JSON 响应工具。"""
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"access-control-allow-origin", b"*"),
            (b"access-control-allow-methods", b"GET, POST, OPTIONS"),
            (b"access-control-allow-headers", b"Content-Type, Authorization"),
        ]
    })
    await send({"type": "http.response.body", "body": body})


async def _send_cors_preflight(send):
    """处理 CORS 预检请求。"""
    await send({
        "type": "http.response.start",
        "status": 204,
        "headers": [
            (b"access-control-allow-origin", b"*"),
            (b"access-control-allow-methods", b"GET, POST, OPTIONS"),
            (b"access-control-allow-headers", b"Content-Type, Authorization"),
            (b"access-control-max-age", b"86400"),
        ]
    })
    await send({"type": "http.response.body", "body": b""})