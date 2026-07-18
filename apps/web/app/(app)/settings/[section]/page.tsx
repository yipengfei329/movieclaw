import { notFound } from "next/navigation";

import { SettingsPanel } from "@/components/settings-view";
import { settingsSections } from "@/lib/mock-data";

/** 设置分区（/settings/[section]）：个人信息 / 外观 / 搜索 / 站点 / 下载器 / 插件。 */
export default async function SettingsSectionPage({
  params,
}: {
  params: Promise<{ section: string }>;
}) {
  const { section } = await params;
  if (!settingsSections.some((s) => s.id === section)) notFound();
  return <SettingsPanel active={section} />;
}
