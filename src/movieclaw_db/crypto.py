from __future__ import annotations

import base64
import hashlib
import logging
import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("movieclaw_db.crypto")

# 密文标记前缀：落库的密文统一带此前缀。
# 作用有二：
# 1. 让 SettingStore / Repository 能一眼区分"已加密"与"历史明文"，实现平滑迁移
#    （读到没有前缀的旧值时按明文原样返回，不至于解密报错）。
# 2. 未来若更换加密算法，可用不同前缀做版本区分。
_ENC_PREFIX = "enc::"


class SecretBox:
    """敏感配置字段的对称加密器（落库加密 / 读取解密）。

    用于保护写入数据库的 api_key、password、token 等密文。底层用 Fernet
    （AES-128-CBC + HMAC 认证加密），密文自带完整性校验，被篡改会直接解密失败。

    主密钥来源（两条通道，对应架构里的"方案 A / 方案 B"）
    -------------------------------------------------------
    - **方案 A（环境变量覆盖，面向安全敏感的高级用户）**：设置 ``MASTER_KEY``
      环境变量后，由它派生出加密密钥。密钥不落盘，即使整块 volume 被窃取、
      没有该环境变量也无法解密。代价是用户须自行妥善保管——一旦丢失，所有
      密文永久无法恢复。
    - **方案 B（自动生成密钥文件，默认，面向非开发者）**：未提供 ``MASTER_KEY``
      时，首次启动自动在数据目录生成随机密钥文件（``data/.secret_key``，权限
      0600），此后复用。用户完全无感。安全边界：能防"仅数据库文件泄露"，
      防不住"整个 volume 被端走"（因为钥匙和密文都在同一块盘上）。对自托管、
      单用户的一体化部署，这是主流做法（Vaultwarden / Nextcloud 同款思路）。

    ⚠️ 关键红线：主密钥属于"引导层"，**绝不能存进数据库**——否则等于把锁和
    钥匙锁进同一个盒子。它只存在于环境变量或数据目录的密钥文件里。
    """

    def __init__(self, master_key: str | None, key_file: Path) -> None:
        fernet_key = self._resolve_key(master_key, key_file)
        self._fernet = Fernet(fernet_key)

    # ------------------------------------------------------------------
    # 密钥解析
    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_key(master_key: str | None, key_file: Path) -> bytes:
        """解析出最终用于 Fernet 的 32 字节密钥（urlsafe base64 编码）。"""
        # 方案 A：环境变量提供的任意字符串 → 用 SHA-256 派生出定长密钥。
        # 派生是确定性的：同一个 MASTER_KEY 永远得到同一把密钥，重启后仍能解密。
        if master_key:
            logger.info("检测到 MASTER_KEY 环境变量，使用它派生加密密钥（方案 A）")
            digest = hashlib.sha256(master_key.encode("utf-8")).digest()
            return base64.urlsafe_b64encode(digest)

        # 方案 B：读取密钥文件；不存在则生成并落盘。
        if key_file.exists():
            logger.info("从密钥文件加载加密密钥（方案 B）：%s", key_file)
            return key_file.read_bytes().strip()

        return SecretBox._generate_key_file(key_file)

    @staticmethod
    def _generate_key_file(key_file: Path) -> bytes:
        """生成随机密钥并以 0600 权限写入文件，返回该密钥。"""
        key = Fernet.generate_key()
        key_file.parent.mkdir(parents=True, exist_ok=True)
        # 先创建空文件并收紧权限，再写入，避免"写入后再 chmod"之间的短暂暴露窗口
        key_file.touch(mode=stat.S_IRUSR | stat.S_IWUSR, exist_ok=True)
        try:
            os.chmod(key_file, stat.S_IRUSR | stat.S_IWUSR)  # 0600，仅属主可读写
        except OSError:
            # 某些文件系统（如 Windows 挂载卷）不支持 POSIX 权限，忽略即可
            logger.warning("无法为密钥文件设置 0600 权限，请自行确认其访问权限：%s", key_file)
        key_file.write_bytes(key)
        logger.warning(
            "已自动生成新的加密密钥文件：%s —— 请务必随数据目录一并备份，"
            "该文件丢失将导致所有已加密配置无法恢复！",
            key_file,
        )
        return key

    # ------------------------------------------------------------------
    # 加解密
    # ------------------------------------------------------------------
    def encrypt(self, plaintext: str) -> str:
        """加密明文，返回带 ``enc::`` 前缀的密文字符串。"""
        token = self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")
        return f"{_ENC_PREFIX}{token}"

    def decrypt(self, value: str) -> str:
        """解密密文。

        为兼容历史明文数据：若传入值没有 ``enc::`` 前缀，视为未加密的旧值，
        原样返回（便于将存量明文逐步迁移为密文，不会一上来就解密报错）。
        """
        if not value.startswith(_ENC_PREFIX):
            return value
        token = value[len(_ENC_PREFIX):]
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            # 密钥不匹配（换了 MASTER_KEY / 丢了密钥文件）或密文被篡改
            raise ValueError(
                "配置密文解密失败：加密密钥可能已变更或密钥文件丢失，"
                "无法还原该敏感字段。"
            ) from exc

    @staticmethod
    def is_encrypted(value: str) -> bool:
        """判断一个字符串是否为本模块产出的密文。"""
        return value.startswith(_ENC_PREFIX)


# ---------------------------------------------------------------------------
# 模块级单例 + 生命周期钩子（与 engine.py 的 init_db/get_database 保持一致风格）
# ---------------------------------------------------------------------------
_secret_box: SecretBox | None = None


def init_secret_box(master_key: str | None, key_file: Path) -> SecretBox:
    """初始化全局加密器单例。应在应用启动（lifespan）时调用一次。"""
    global _secret_box
    if _secret_box is not None:
        logger.warning("加密器已初始化，重复调用 init_secret_box 被忽略")
        return _secret_box
    _secret_box = SecretBox(master_key, key_file)
    return _secret_box


def get_secret_box() -> SecretBox:
    """获取全局加密器单例。未初始化时抛错，提示调用方检查启动流程。"""
    if _secret_box is None:
        raise RuntimeError("加密器尚未初始化，请确认应用启动时已调用 init_secret_box()")
    return _secret_box


def reset_secret_box() -> None:
    """清空全局加密器单例（主要供测试在用例间隔离状态）。"""
    global _secret_box
    _secret_box = None
