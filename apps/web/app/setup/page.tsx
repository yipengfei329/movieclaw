"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { AuthError, AuthField, AuthScreen } from "@/components/auth-screen";
import { createAdmin, getBootstrapStatus } from "@/lib/api/auth";
import { HttpError } from "@/lib/http";

/**
 * 首次初始化引导页：创建超级管理员账号（全生命周期只此一次）。
 *
 * 挂载时校验初始化状态：已初始化则立即转登录页——这只是防误入的导航，
 * 真正的"只能初始化一次"由后端一次性锁保证（重复提交必得 409），
 * 改前端代码绕不过去。
 */
export default function SetupPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getBootstrapStatus()
      .then((status) => {
        if (!cancelled && status.initialized) router.replace("/login");
      })
      .catch(() => {
        // 状态查询失败（后端未起）：留在引导页，提交时自然会报错
      });
    return () => {
      cancelled = true;
    };
  }, [router]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;

    const name = username.trim();
    if (name.length < 3) {
      setError("用户名至少 3 个字符");
      return;
    }
    if (password.length < 8) {
      setError("密码至少 8 位，建议混用字母与数字");
      return;
    }
    if (password !== confirm) {
      setError("两次输入的密码不一致");
      return;
    }

    setError(null);
    setBusy(true);
    try {
      await createAdmin(name, password);
      // 建号即自动登录，整页进入首页让应用在已登录态下初始化
      window.location.href = "/";
    } catch (err) {
      if (err instanceof HttpError && err.status === 409) {
        // 一次性锁已闭合（可能在另一个标签页里完成了初始化）
        router.replace("/login");
        return;
      }
      setError(err instanceof HttpError ? err.message : "网络异常，请稍后重试");
      setBusy(false);
    }
  };

  return (
    <AuthScreen
      title="初始化"
      subtitle="欢迎使用。请设置超级管理员账号——它是本站唯一的管理身份，此流程仅在首次部署时出现。"
    >
      <form onSubmit={submit} className="space-y-4">
        <AuthField
          label="管理员用户名"
          type="text"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoComplete="username"
          autoFocus
        />
        <AuthField
          label="密码（至少 8 位）"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="new-password"
        />
        <AuthField
          label="确认密码"
          type="password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          autoComplete="new-password"
        />
        <AuthError message={error} />
        <button
          type="submit"
          disabled={busy || !username.trim() || !password || !confirm}
          className="btn-accent w-full rounded-full px-4.5 py-2.5 text-[13px] font-semibold disabled:opacity-40"
        >
          {busy ? "创建中…" : "创建账号并进入"}
        </button>
      </form>
    </AuthScreen>
  );
}
