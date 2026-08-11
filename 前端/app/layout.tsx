import { Analytics } from '@vercel/analytics/next'
import type { Metadata, Viewport } from 'next'
import { AppFrame } from '@/components/shared/app-frame'
import { AppProvider } from '@/components/shared/app-context'
import { MobileShell } from '@/components/shared/mobile-shell'
import './globals.css'
import './experience-refined.css'

export const metadata: Metadata = {
  title: '旅行星球 · 懂你的旅行助手',
  description: '旅行星球：用照片生成专属明信片与旅行人格报告，并帮你规划下一段旅程。',
}

export const viewport: Viewport = {
  colorScheme: 'light',
  themeColor: '#eaf2f6',
  width: 'device-width',
  initialScale: 1,
  viewportFit: 'cover',
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="zh-CN" className="bg-background">
      <body className="font-sans antialiased">
        <AppProvider>
          <MobileShell>
            <AppFrame>{children}</AppFrame>
          </MobileShell>
        </AppProvider>
        {process.env.VERCEL === '1' && <Analytics />}
      </body>
    </html>
  )
}
