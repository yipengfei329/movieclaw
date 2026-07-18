"""通用图片代理接口（带本地磁盘缓存）。"""

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from movieclaw_api.services.image_cache import get_image_cache

router = APIRouter(prefix="/images", tags=["images"])


@router.get("/proxy", response_class=FileResponse, summary="代理并缓存远程图片")
async def proxy_image(url: str = Query(min_length=1, max_length=2048)) -> FileResponse:
    """前端所有远程图片的统一入口：命中读本地缓存，未命中回源抓取后落盘。

    域名安全（SSRF 防护）、类型和体积校验在 ImageProxy 服务层完成。
    图床 URL 对应的内容事实上不可变，浏览器侧直接给一年 immutable 缓存。
    """
    cached = await get_image_cache().get_or_fetch(url)
    return FileResponse(
        cached.path,
        media_type=cached.content_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
