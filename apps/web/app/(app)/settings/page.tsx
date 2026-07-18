import { redirect } from "next/navigation";
import type { Route } from "next";

import { settingsSections } from "@/lib/mock-data";

/** /settings 裸地址重定向到首个分区，保证设置页始终有明确的分区地址。 */
export default function SettingsIndexPage() {
  redirect(`/settings/${settingsSections[0].id}` as Route);
}
