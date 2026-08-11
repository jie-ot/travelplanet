"use client"

import type { ReactNode } from "react"
import { ChevronLeft } from "lucide-react"
import { useApp } from "@/components/shared/app-context"
import { UserBadge } from "@/components/shared/user-badge"

/**
 * 子页面统一顶部栏：标题操作区 + 右上角单用户头像。
 * onBack 可覆盖默认返回行为（如草稿拦截）。
 */
export function TopBar({
  title,
  subtitle,
  onBack,
  right,
  showUserBadge = true,
}: {
  title?: string
  subtitle?: string
  onBack?: () => void
  right?: ReactNode
  showUserBadge?: boolean
}) {
  const { goBack } = useApp()

  return (
    <header className="top-bar sticky top-0 z-30 border-b border-white/12 bg-[#062d48]/94 px-4 pb-3 pt-[max(0.65rem,env(safe-area-inset-top))] text-white shadow-[0_12px_30px_-22px_rgba(6,45,72,0.92)] backdrop-blur-xl min-[400px]:px-5 [&_[title]]:bg-white [&_[title]]:text-[#082f49] [&_[title]]:ring-white/30">
      <div className="grid min-h-11 grid-cols-[3.5rem_minmax(0,1fr)_8.75rem] items-center gap-2 min-[400px]:grid-cols-[3.75rem_minmax(0,1fr)_9.5rem]">
        <button
          type="button"
          onClick={onBack ?? goBack}
          className="ui-icon-button flex size-11 items-center justify-center justify-self-start rounded-full bg-white/12 text-white shadow-sm ring-1 ring-white/22 backdrop-blur-md"
          aria-label="返回"
        >
          <ChevronLeft className="size-5.5" strokeWidth={2.35} aria-hidden />
        </button>
        {title ? (
          <h1 className="min-w-0 truncate text-center font-display text-[1.1rem] font-bold leading-tight text-white">
            {title}
          </h1>
        ) : (
          <span />
        )}
        <div className="flex min-w-0 items-center justify-end gap-1.5">
          {right}
          {showUserBadge ? <UserBadge subtitle={subtitle} /> : null}
        </div>
      </div>
    </header>
  )
}
