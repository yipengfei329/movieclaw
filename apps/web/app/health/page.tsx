import type { Metadata } from "next";

import HealthClient from "./HealthClient";

export const metadata: Metadata = {
  title: "Health Check",
};

export default function HealthPage() {
  return <HealthClient />;
}
