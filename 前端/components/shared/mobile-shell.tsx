"use client"

import type { ReactNode } from "react"
import { cn } from "@/lib/utils"

/**
 * 移动端统一外壳容器。
 * 居中一个最大宽度的竖向列，使用动态视口高度与安全区适配。
 * 背景：轻旅行纸感底图，控制存在感，避免干扰主内容。
 */
export function MobileShell({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className="relative flex h-dvh w-full justify-center overflow-hidden bg-[#dce7e9]">
      <main
        className={cn(
          "relative flex h-dvh w-full max-w-md flex-col overflow-hidden bg-background",
          "shadow-[0_0_56px_-24px_rgba(6,45,72,0.32)]",
          className,
        )}
      >
        {/* 背景氛围层：在外壳内部，置于内容之下 */}
        <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
          <div className="absolute inset-0 bg-background" />
          {/* 旅行风情插画 */}
          <img
            src="/images/travel-backdrop.png"
            alt=""
            className="absolute inset-0 size-full object-cover opacity-[0.14] saturate-50"
          />
          <div className="absolute inset-0 bg-gradient-to-b from-background/94 via-background/86 to-background/95" />
          <div className="absolute inset-0 opacity-40 [background-image:linear-gradient(115deg,rgba(7,49,73,0.025)_1px,transparent_1px)] [background-size:18px_18px]" />
        </div>

        {/* 内容层：锁定视口高度，内部页面自行滚动 */}
        <div className="relative z-10 flex h-full min-h-0 flex-1 flex-col">{children}</div>
      </main>
    </div>
  )
}
