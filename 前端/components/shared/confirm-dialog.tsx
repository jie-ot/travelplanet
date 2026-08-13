"use client"

import { useEffect, useId, useRef, type ReactNode } from "react"
import { useApp } from "@/components/shared/app-context"
import { cn } from "@/lib/utils"

export interface DialogAction {
  label: string
  onClick: () => void
  variant?: "primary" | "danger" | "ghost"
}

/** 通用确认/多选弹窗（保存确认、草稿保存/丢弃/取消等）。 */
export function ConfirmDialog({
  open,
  title,
  description,
  actions,
  onClose,
  icon,
}: {
  open: boolean
  title: string
  description?: string
  actions: DialogAction[]
  onClose: () => void
  icon?: ReactNode
}) {
  const { registerBackHandler } = useApp()
  const titleId = useId()
  const descriptionId = useId()
  const firstActionRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    const previousFocus = document.activeElement as HTMLElement | null
    const frame = window.requestAnimationFrame(() => firstActionRef.current?.focus())
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose()
    }
    document.addEventListener("keydown", onKeyDown)
    return () => {
      window.cancelAnimationFrame(frame)
      document.removeEventListener("keydown", onKeyDown)
      previousFocus?.focus()
    }
  }, [open, onClose])

  useEffect(() => {
    if (!open) return
    return registerBackHandler(onClose)
  }, [open, onClose, registerBackHandler])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/42 px-6 backdrop-blur-md animate-in fade-in"
      onClick={(event) => {
        event.stopPropagation()
        onClose()
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        className="ui-material-in w-full max-w-xs overflow-hidden rounded-[1.75rem] border border-white/65 bg-card/97 p-6 text-center shadow-[0_26px_64px_-22px_rgba(6,45,72,0.58)] ring-1 ring-[#0a3850]/10 backdrop-blur-xl"
        onClick={(e) => e.stopPropagation()}
      >
        {icon && <div className="mb-3 flex justify-center">{icon}</div>}
        <h2 id={titleId} className="font-display text-lg font-bold tracking-[-0.01em] text-foreground text-balance">{title}</h2>
        {description ? <p id={descriptionId} className="mt-2 font-editorial text-sm font-medium leading-relaxed text-muted-foreground text-pretty">{description}</p> : null}
        <div className="mt-6 flex flex-col gap-2.5">
          {actions.map((a, index) => (
            <button
              key={a.label}
              ref={index === 0 ? firstActionRef : undefined}
              type="button"
              onClick={a.onClick}
              className={cn(
                "ui-pressable min-h-12 rounded-2xl text-sm font-semibold",
                a.variant === "danger" && "bg-destructive text-white",
                a.variant === "ghost" && "bg-secondary text-secondary-foreground",
                (!a.variant || a.variant === "primary") && "bg-primary text-primary-foreground",
              )}
            >
              {a.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
