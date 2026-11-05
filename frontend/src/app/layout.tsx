import type { Metadata, Viewport } from "next";

import { AppHeader } from "@/components/app-header";
import { AuthProvider } from "@/components/auth-provider";

import "./globals.css";

export const metadata: Metadata = {
  title: "ChainWise",
  description: "AI-ассистент для аудита смарт-контрактов",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#0a0f1a",
  interactiveWidget: "resizes-content",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <body className="flex h-dvh flex-col overflow-hidden">
        <AuthProvider>
          <AppHeader />
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden">{children}</div>
        </AuthProvider>
      </body>
    </html>
  );
}
