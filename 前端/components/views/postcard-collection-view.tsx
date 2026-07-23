"use client"

import { useRef, useState } from "react"
import { useSearchParams } from "next/navigation"
import { ChevronLeft, MapPin } from "lucide-react"
import { useApp } from "@/components/shared/app-context"
import { EmptyState } from "@/components/shared/empty-state"
import { PLACEHOLDER_IMAGE, handleImageError, resolveAssetUrl } from "@/lib/asset"

export function PostcardCollectionView() {
  const searchParams = useSearchParams()
  const { postcardGroups, goBack } = useApp()
  const group = postcardGroups.find((g) => g.id === searchParams.get("groupId"))
  const [active, setActive] = useState(0)
  const trackRef = useRef<HTMLDivElement>(null)

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
        className="flex flex-1 snap-x snap-mandatory overflow-x-auto no-scrollbar"
      >
        {group.postcards.map((pc) => (
          <div key={pc.id} className="flex w-full shrink-0 snap-center items-center justify-center p-6">
            <img
              src={resolveAssetUrl(pc.imageUrl) || PLACEHOLDER_IMAGE}
              alt={pc.title}
              className="max-h-[62vh] w-full rounded-xl border-[5px] border-[#eee5d8] object-contain shadow-2xl"
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
    </div>
  )
}
