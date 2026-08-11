"use client"

import { Sparkles } from "lucide-react"

export function MemoryEntryButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="ui-pressable flex h-10 shrink-0 items-center justify-center gap-1 rounded-full bg-card/94 px-3 text-[0.7rem] font-semibold text-foreground shadow-sm ring-1 ring-border/80 min-[400px]:h-11 min-[400px]:gap-1.5 min-[400px]:px-3.5 min-[400px]:text-xs"
      aria-label="打开旅行记忆"
      title="旅行记忆"
    >
      <Sparkles className="h-3.5 w-3.5 text-primary" aria-hidden />
      <span>旅行记忆</span>
    </button>
  )
}
