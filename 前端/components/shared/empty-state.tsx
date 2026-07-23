"use client"

import type { ReactNode } from "react"

/** 有设计感的空状态卡片。 */
export function EmptyState({
  icon,
  title,
  description,
  children,
}: {
  icon: ReactNode
  title: string
  description: string
  children?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center justify-center px-8 py-14 text-center">
      <div className="relative mb-5 flex size-22 items-center justify-center">
        <div className="absolute inset-0 rounded-full bg-card/88 shadow-[0_14px_30px_-22px_rgba(6,45,72,0.65)] ring-1 ring-[#0a3850]/15" />
        <div className="absolute inset-3 rounded-full border border-dashed border-primary/35 animate-planet-spin" />
        <div className="relative flex size-14 items-center justify-center rounded-full bg-secondary text-primary shadow-inner">{icon}</div>
      </div>
      <h3 className="font-display text-base font-bold tracking-tight text-foreground text-balance">{title}</h3>
      <p className="mt-2 max-w-xs font-editorial text-sm font-medium leading-relaxed text-muted-foreground text-pretty">{description}</p>
      {children ? <div className="mt-6">{children}</div> : null}
    </div>
  )
}
