from __future__ import annotations

from pydantic import BaseModel, Field


class BackdropItem(BaseModel):
    """图库中的一张背景图。

    ``url`` 是**带版本号**的相对地址（形如 ``/api/v1/appearance/backdrops/<id>?v=…``），
    版本号取文件修改时间的纳秒值，用来强制刷新浏览器与 WebGL 着色器对旧图的缓存。
    """

    id: str = Field(description="背景图 id（uuid4 hex）")
    url: str = Field(description="图片文件的相对 URL（含版本号）")


class AppearanceView(BaseModel):
    """外观设置的对外视图。

    背景图是一个「图库」：用户上传的图全部保留（``backdrops``，按上传时间升序），
    其中至多一张为当前生效图（``active_id`` / ``active_url``）。二者为空表示
    正在使用内置默认背景——默认背景是前端内置资源，不出现在图库列表里。
    """

    active_id: str | None = Field(
        default=None, description="当前生效的背景图 id；为空表示使用内置默认背景"
    )
    active_url: str | None = Field(
        default=None, description="当前生效背景图的相对 URL（含版本号）；为空表示内置默认"
    )
    backdrops: list[BackdropItem] = Field(
        default_factory=list, description="图库中的全部自定义背景图（上传时间升序）"
    )


class ActiveBackdropUpdate(BaseModel):
    """切换当前生效背景图的请求体。``backdrop_id`` 为空表示切回内置默认背景。"""

    backdrop_id: str | None = Field(
        default=None, description="要启用的背景图 id；为空表示使用内置默认背景"
    )
