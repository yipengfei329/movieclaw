function readPublicEnv(key: string, fallback: string): string {
  const value = process.env[key]?.trim();
  return value ? value : fallback;
}

export const publicEnv = {
  apiBaseUrl: readPublicEnv("NEXT_PUBLIC_API_BASE_URL", "/api/v1"),
  appName: readPublicEnv("NEXT_PUBLIC_APP_NAME", "movieclaw console"),
} as const;
