import { Analytics } from '@vercel/analytics/next'
import type { Metadata, Viewport } from 'next'
import { Geist, Geist_Mono, Noto_Sans_SC, Noto_Serif_SC } from 'next/font/google'
import { AppFrame } from '@/components/shared/app-frame'
import { AppProvider } from '@/components/shared/app-context'
import { MobileShell } from '@/components/shared/mobile-shell'
import './globals.css'

const geistSans = Geist({ variable: '--font-geist-sans', subsets: ['latin'] })
const geistMono = Geist_Mono({
  variable: '--font-geist-mono',
  subsets: ['latin'],
})

// 标题展示用：粗黑体（对齐设计图“旅行星球”重磅黑体观感）
const notoSansSC = Noto_Sans_SC({
  variable: '--font-display-sc',
  subsets: ['latin'],
  weight: ['500', '700', '900'],
  display: 'swap',
})

// 正文/副标题用：宋体衬线（对齐设计图副标题质感）
const notoSerifSC = Noto_Serif_SC({
  variable: '--font-editorial-sc',
  subsets: ['latin'],
  weight: ['500', '600', '700'],
  display: 'swap',
})

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
    <html
      lang="zh-CN"
      className={`${geistSans.variable} ${geistMono.variable} ${notoSansSC.variable} ${notoSerifSC.variable} bg-background`}
    >
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
