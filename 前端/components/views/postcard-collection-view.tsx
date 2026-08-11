"use client"

import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react"
import { useSearchParams } from "next/navigation"
import { ChevronLeft, Download, MapPin } from "lucide-react"
import { useApp } from "@/components/shared/app-context"
import { ConfirmDialog } from "@/components/shared/confirm-dialog"
import { EmptyState } from "@/components/shared/empty-state"
import { PLACEHOLDER_IMAGE, handleImageError, resolveAssetUrl } from "@/lib/asset"
import { savePostcardImage } from "@/lib/postcard-save"
import type { Postcard } from "@/types"

const LONG_PRESS_MS = 650
const LONG_PRESS_MOVE_TOLERANCE = 12

export function PostcardCollectionView() {
  const searchParams = useSearchParams()
  const { postcardGroups, goBack, toast } = useApp()
  const group = postcardGroups.find((g) => g.id === searchParams.get("groupId"))
  const [active, setActive] = useState(0)
  const [pendingSave, setPendingSave] = useState<Postcard | null>(null)
  const [saving, setSaving] = useState(false)
  const trackRef = useRef<HTMLDivElement>(null)
  const longPressTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pressStartRef = useRef<{ x: number; y: number } | null>(null)

  const cancelLongPress = () => {
    if (longPressTimerRef.current) clearTimeout(longPressTimerRef.current)
    longPressTimerRef.current = null
    pressStartRef.current = null
  }

  useEffect(
    () => () => {
      if (longPressTimerRef.current) clearTimeout(longPressTimerRef.current)
    },
    [],
  )

  const startLongPress = (event: ReactPointerEvent<HTMLImageElement>, postcard: Postcard) => {
    if (event.pointerType === "mouse" && event.button !== 0) return
    cancelLongPress()
    pressStartRef.current = { x: event.clientX, y: event.clientY }
    longPressTimerRef.current = setTimeout(() => {
      longPressTimerRef.current = null
      pressStartRef.current = null
      if ("vibrate" in navigator) navigator.vibrate(30)
      setPendingSave(postcard)
    }, LONG_PRESS_MS)
  }

  const moveLongPress = (event: ReactPointerEvent<HTMLImageElement>) => {
    const start = pressStartRef.current
    if (!start) return
    if (
      Math.abs(event.clientX - start.x) > LONG_PRESS_MOVE_TOLERANCE ||
      Math.abs(event.clientY - start.y) > LONG_PRESS_MOVE_TOLERANCE
    ) {
      cancelLongPress()
    }
  }

  if (!group) {
    return (
      <div className="flex flex-1 flex-col">
        <EmptyState
          icon={<MapPin className="size-7" aria-hidden />}
          title="内容已被删除"
          description="该明信片组不存在或已被移除。"
        >
          <button
            type="button"
            onClick={goBack}
            className="ui-pressable min-h-12 rounded-full bg-primary px-6 py-2.5 text-sm font-semibold text-primary-foreground shadow-md"
          >
            返回
          </button>
        </EmptyState>
      </div>
    )
  }

  const onScroll = () => {
    const el = trackRef.current
    if (!el) return
    const idx = Math.round(el.scrollLeft / el.clientWidth)
    if (idx !== active) setActive(idx)
  }

  const current = group.postcards[active]

  return (
    <div className="relative flex flex-1 flex-col bg-[#062d48]">
      {/* 顶部栏 */}
      <header className="absolute inset-x-0 top-0 z-20 flex items-center justify-between px-4 pt-[max(0.75rem,env(safe-area-inset-top))]">
        <button
          type="button"
          onClick={goBack}
          className="ui-icon-button flex size-11 items-center justify-center rounded-full bg-card/18 text-white shadow-md ring-1 ring-white/22 backdrop-blur-xl"
          aria-label="返回"
        >
          <ChevronLeft className="size-5" aria-hidden />
        </button>
        <div className="rounded-full bg-card/18 px-3.5 py-2 text-xs font-semibold text-white ring-1 ring-white/18 backdrop-blur-xl">
          {active + 1} / {group.postcards.length}
        </div>
      </header>

      {/* 轮播：横向滚动 + scroll-snap，支持滑动 */}
      <div
        ref={trackRef}
        onScroll={onScroll}
        className="flex flex-1 snap-x snap-mandatory overflow-x-auto overscroll-x-contain no-scrollbar"
      >
        {group.postcards.map((pc) => (
          <div
            key={pc.id}
            className="flex w-full shrink-0 snap-center items-center justify-center p-6 [scroll-snap-stop:always]"
          >
            <img
              src={resolveAssetUrl(pc.imageUrl) || PLACEHOLDER_IMAGE}
              alt={pc.title}
              draggable={false}
              className="max-h-[62vh] w-full select-none rounded-xl border-[5px] border-[#eee5d8] object-contain shadow-2xl [-webkit-touch-callout:none]"
              onContextMenu={(event) => event.preventDefault()}
              onPointerDown={(event) => startLongPress(event, pc)}
              onPointerMove={moveLongPress}
              onPointerUp={cancelLongPress}
              onPointerCancel={cancelLongPress}
              onPointerLeave={cancelLongPress}
              onError={handleImageError}
            />
          </div>
        ))}
      </div>

      {/* 底部信息 + 圆点 */}
      <div className="relative z-10 bg-gradient-to-t from-[#062d48] to-transparent px-6 pb-[max(1.5rem,env(safe-area-inset-bottom))] pt-4 text-center">
        <p className="flex items-center justify-center gap-1.5 text-xs text-background/70">
          <MapPin className="size-3.5" aria-hidden />
          {group.location} · {group.dateLabel}
        </p>
        <h2 className="mt-1.5 font-display text-lg font-bold tracking-tight text-white text-balance">{current?.title}</h2>
        <div className="mt-2 flex items-center justify-center gap-0.5">
          {group.postcards.map((pc, i) => (
            <button
              key={pc.id}
              type="button"
              aria-label={`查看第 ${i + 1} 张`}
              onClick={() => {
                const behavior = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth"
                trackRef.current?.scrollTo({ left: i * (trackRef.current?.clientWidth ?? 0), behavior })
              }}
              className="ui-icon-button grid size-11 place-items-center rounded-full"
            >
              <span className={`h-1.5 rounded-full transition-all ${i === active ? "w-6 bg-background" : "w-1.5 bg-background/40"}`} />
            </button>
          ))}
        </div>
      </div>

      <ConfirmDialog
        open={!!pendingSave}
        title="保存这张明信片？"
        description="图片将保存到手机系统相册的“旅行星球”中。"
        icon={
          <span className="grid size-12 place-items-center rounded-full bg-primary/12 text-primary">
            <Download className="size-6" aria-hidden />
          </span>
        }
        onClose={() => {
          if (!saving) setPendingSave(null)
        }}
        actions={[
          {
            label: saving ? "正在保存…" : "保存到相册",
            onClick: () => {
              if (!pendingSave || saving) return
              setSaving(true)
              const imageUrl = resolveAssetUrl(pendingSave.imageUrl) || PLACEHOLDER_IMAGE
              void savePostcardImage(imageUrl, pendingSave.title)
                .then(() => {
                  setPendingSave(null)
                  toast("已保存到系统相册", "success")
                })
                .catch(() => toast("保存失败，请稍后重试", "error"))
                .finally(() => setSaving(false))
            },
          },
          {
            label: "取消",
            variant: "ghost",
            onClick: () => {
              if (!saving) setPendingSave(null)
            },
          },
        ]}
      />
    </div>
  )
}
