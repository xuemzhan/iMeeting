"""可选：托管前端生产构建（绿色包单端口部署）。

仅当设置了 ``IMEETING_WEB_DIR``（或显式传入目录）且该目录下存在
``index.html`` 时启用；开发环境不设置该变量，行为与之前完全一致。

启用后：
- ``/`` 返回前端 ``index.html``（覆盖默认的 JSON 信息页）；
- ``/assets/*`` 走静态文件（Vite 产物带哈希，可长缓存）；
- 其余 GET 路径先找同名文件（favicon / manifest / pcm-worklet.js 等），
  找不到再回落到 ``index.html``，从而支持 React Router 的客户端路由；
- ``/api/*`` 与 ``/ws/*`` 保持 404，不会把接口错误伪装成首页。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from starlette.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

# 这些前缀属于后端接口，不能被 SPA 兜底吞掉
_RESERVED_PREFIXES = ("api/", "ws/")

_web_root: Path | None = None


def configure(directory: str | Path | None = None) -> Path | None:
    """解析并缓存前端构建目录；未配置或无效时返回 None。"""
    global _web_root

    raw = str(directory or os.getenv("IMEETING_WEB_DIR") or "").strip()
    if not raw:
        _web_root = None
        return None

    root = Path(raw).expanduser().resolve()
    if not (root / "index.html").is_file():
        logger.warning("未找到前端构建产物 index.html：%s（跳过静态托管）", root)
        _web_root = None
        return None

    _web_root = root
    return root


def index_path() -> Path | None:
    """返回 ``index.html`` 路径；未启用静态托管时返回 None。"""
    if _web_root is None:
        return None
    return _web_root / "index.html"


def mount_web(app: FastAPI, directory: str | Path | None = None) -> bool:
    """把前端构建目录挂到 ``app`` 上；未配置或目录无效时返回 False。"""
    root = configure(directory)
    if root is None:
        return False

    assets = root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="web-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str) -> FileResponse:
        if full_path.startswith(_RESERVED_PREFIXES):
            raise HTTPException(status_code=404, detail="Not Found")

        if full_path:
            candidate = (root / full_path).resolve()
            if candidate.is_file() and (candidate == root or root in candidate.parents):
                return FileResponse(candidate)

        return FileResponse(root / "index.html")

    logger.info("已托管前端静态资源：%s", root)
    return True
