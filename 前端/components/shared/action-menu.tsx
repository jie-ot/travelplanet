"use client"

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react"
import { createPortal } from "react-dom"
import { MoreHorizontal } from "lucide-react"
import { cn } from "@/lib/utils"

export interface MenuItem {
  label: string
  icon: React.ReactNode
  onClick: () => void
  danger?: boolean
}

interface Coords {
  top: number
  left: number
  placement: "top" | "bottom"
}

const MENU_WIDTH = 144
const MENU_GAP = 8

/**
 * 卡片角落的小菜单按钮，点击弹出操作项（删除 / 分享 / 重新编辑等）。
 * 菜单通过 Portal 渲染到 body，避免被卡片的 overflow-hidden 裁切。
 */
export function ActionMenu({ items, label = "更多操作" }: { items: MenuItem[]; label?: string }) {
  const [open, setOpen] = useState(false)
  const [coords, setCoords] = useState<Coords | null>(null)
  const btnRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  const computeCoords = useCallback(() => {
    const btn = btnRef.current
    if (!btn) return
    const rect = btn.getBoundingClientRect()
    const estHeight = items.length * 46 + 8
    const spaceBelow = window.innerHeight - rect.bottom
    const placement: "top" | "bottom" = spaceBelow < estHeight + MENU_GAP ? "top" : "bottom"
    // 右对齐到按钮右边缘，并夹在视口内
    let left = rect.right - MENU_WIDTH
    left = Math.max(8, Math.min(left, window.innerWidth - MENU_WIDTH - 8))
    const top = placement === "bottom" ? rect.bottom + MENU_GAP : rect.top - MENU_GAP
    setCoords({ top, left, placement })
  }, [items.length])

  useLayoutEffect(() => {
    if (open) computeCoords()
  }, [open, computeCoords])

  useEffect(() => {
    if (!open) return
    const onPointer = (e: PointerEvent) => {
      const t = e.target as Node
      if (btnRef.current?.contains(t) || menuRef.current?.contains(t)) return
      setOpen(false)
    }
    const onReposition = () => setOpen(false)
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false)
        btnRef.current?.focus()
      }
    }
    document.addEventListener("pointerdown", onPointer)
    document.addEventListener("keydown", onKeyDown)
    window.addEventListener("resize", onReposition)
    // 捕获阶段监听滚动（包含内部可滚动容器）
    window.addEventListener("scroll", onReposition, true)
    return () => {
      document.removeEventListener("pointerdown", onPointer)
      document.removeEventListener("keydown", onKeyDown)
      window.removeEventListener("resize", onReposition)
      window.removeEventListener("scroll", onReposition, true)
    }
  }, [open])

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        onClick={(e) => {
          e.stopPropagation()
          setOpen((o) => !o)
        }}
        className="ui-icon-button flex size-11 items-center justify-center rounded-full bg-card/94 text-foreground shadow-[0_8px_18px_-10px_rgba(6,45,72,0.68)] ring-1 ring-[#0a3850]/14 backdrop-blur-md"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <MoreHorizontal className="size-5" strokeWidth={2.15} aria-hidden />
      </button>

      {open &&
        coords &&
        typeof document !== "undefined" &&
        createPortal(
          <div
            ref={menuRef}
            role="menu"
            onClick={(e) => e.stopPropagation()}
            style={{
              position: "fixed",
              top: coords.top,
              left: coords.left,
              width: MENU_WIDTH,
              transform: coords.placement === "top" ? "translateY(-100%)" : undefined,
            }}
            className="ui-material-in z-50 overflow-hidden rounded-2xl bg-popover/96 py-1.5 shadow-[0_20px_44px_-18px_rgba(6,45,72,0.58)] ring-1 ring-[#0a3850]/14 backdrop-blur-xl"
          >
            {items.map((item) => (
              <button
                key={item.label}
                type="button"
                role="menuitem"
                onClick={(e) => {
                  e.stopPropagation()
                  setOpen(false)
                  item.onClick()
                }}
                className={cn(
                  "flex min-h-11 w-full items-center gap-3 px-4 py-2.5 text-sm font-medium transition-colors hover:bg-secondary focus-visible:bg-secondary focus-visible:outline-none",
                  item.danger ? "text-destructive" : "text-popover-foreground",
                )}
              >
                <span className="shrink-0">{item.icon}</span>
                {item.label}
              </button>
            ))}
          </div>,
          document.body,
        )}
    </>
  )
}
