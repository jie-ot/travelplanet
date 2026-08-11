"use client"

import { Bot, LockKeyhole } from "lucide-react"
import type { PlanningModel } from "@/types"

interface PlanningModelSelectorProps {
  value: PlanningModel
  locked: boolean
  disabled?: boolean
  compact?: boolean
  onChange: (model: PlanningModel) => void
}

const MODEL_OPTIONS: ReadonlyArray<{
  value: PlanningModel
  label: string
  detail: string
}> = [
  {
    value: "doubao-seed-2.0-pro",
    label: "蓝心大模型",
    detail: "Doubao-Seed-2.0-pro",
  },
  {
    value: "deepseek-v4-flash",
    label: "DeepSeek V4 Flash",
    detail: "思考模式",
  },
  {
    value: "deepseek-v4-pro",
    label: "DeepSeek V4 Pro",
    detail: "思考模式",
  },
]

export function PlanningModelSelector({
  value,
  locked,
  disabled = false,
  compact = false,
  onChange,
}: PlanningModelSelectorProps) {
  return (
    <section
      className={`rounded-2xl border border-[#0a3850]/10 bg-card/92 ${compact ? "p-3" : "p-4"}`}
      aria-label="选择本次旅行规划模型"
      data-testid="planning-model-selector"
    >
      <div className="mb-2.5 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-[13px] font-bold text-[#0a3850]">
          <Bot className="h-4 w-4 text-primary" aria-hidden />
          本次规划模型
        </div>
        {locked ? (
          <span className="flex items-center gap-1 text-[11px] font-medium text-muted-foreground">
            <LockKeyhole className="h-3 w-3" aria-hidden />
            本次对话已锁定
          </span>
        ) : (
          <span className="text-[11px] text-muted-foreground">发送后锁定</span>
        )}
      </div>
      <div className="grid grid-cols-3 gap-2" role="radiogroup">
        {MODEL_OPTIONS.map((option) => {
          const selected = option.value === value
          return (
            <button
              key={option.value}
              type="button"
              role="radio"
              aria-checked={selected}
              disabled={disabled || locked}
              onClick={() => onChange(option.value)}
              className={`ui-pressable min-h-14 rounded-xl border px-1.5 py-2 text-left transition disabled:cursor-not-allowed ${
                selected
                  ? "border-primary bg-primary/10 text-[#073d56] ring-1 ring-primary/20"
                  : "border-border/70 bg-[#f8fbfc] text-muted-foreground"
              }`}
              data-testid={`planning-model-${option.value}`}
            >
              <span className="block whitespace-nowrap text-[11px] font-bold leading-4 tracking-[-0.02em] min-[400px]:text-xs">
                {option.label}
              </span>
              <span className="mt-0.5 block whitespace-nowrap text-[9px] leading-3 tracking-[-0.03em] opacity-75 min-[400px]:text-[9.5px]">
                {option.detail}
              </span>
            </button>
          )
        })}
      </div>
    </section>
  )
}
