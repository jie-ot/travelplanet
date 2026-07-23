"use client"

import { UserRound } from "lucide-react"

/**
 * 单用户展示入口：仅保留右上角圆形头像，纯展示，无交互。
 */
export function UserBadge({ subtitle }: { subtitle?: string }) {
  void subtitle

  return (
    <div
      className="flex size-10 shrink-0 items-center justify-center rounded-full bg-card/96 text-[#082f49] shadow-[0_8px_20px_-11px_rgba(6,45,72,0.72)] ring-1 ring-white/70 backdrop-blur-md"
      aria-label="用户头像"
    >
      <UserRound className="size-[1.15rem]" strokeWidth={2.1} aria-hidden />
    </div>
  )
}
