"use client"

import { cn } from "@/lib/utils"

/** 生成中待机动画：星球旋转 + 轨道 + 星尘流动。 */
export function PlanetLoader({ label = "正在生成中…", className }: { label?: string; className?: string }) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-4 py-10", className)}>
      <div className="relative flex size-30 items-center justify-center">
        <div className="absolute inset-0 rounded-full bg-card/65 shadow-[0_18px_44px_-24px_var(--primary)] ring-1 ring-border/70 backdrop-blur" />
        <div className="absolute inset-4 animate-orbit-pulse rounded-full border border-primary/20" />
        <div className="absolute inset-7 rounded-full border border-dashed border-primary/25" />
        <div className="absolute size-20 animate-planet-spin rounded-full border border-transparent border-t-primary/55 border-r-accent/50" />
        <div className="relative size-12 animate-float-soft overflow-hidden rounded-full bg-gradient-to-br from-primary to-[oklch(0.72_0.11_180)] shadow-[0_12px_30px_-10px_var(--primary)]">
          <div className="absolute left-2 top-2 size-4 rounded-full bg-primary-foreground/28" />
          <div className="absolute bottom-2 right-2 size-2.5 rounded-full bg-primary-foreground/22" />
          <div className="absolute -bottom-4 left-3 h-8 w-12 rotate-[-18deg] rounded-full border-t border-primary-foreground/20" />
        </div>
        <span className="absolute left-1/2 top-1/2 size-2.5 animate-comet rounded-full bg-accent shadow-[0_0_16px_var(--accent)]" />
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="absolute h-1 rounded-full bg-primary/25"
            style={{
              width: `${18 + i * 6}px`,
              left: `${23 + i * 14}%`,
              bottom: `${24 + i * 8}%`,
              animationDelay: `${i * 0.28}s`,
            }}
          />
        ))}
      </div>
      <p className="text-sm font-medium text-muted-foreground">{label}</p>
    </div>
  )
}

/** 卡片骨架屏（瀑布流加载占位）。 */
export function SkeletonCard({ ratio = "aspect-[3/4]" }: { ratio?: string }) {
  return (
    <div className="overflow-hidden rounded-2xl bg-card ring-1 ring-border">
      <div className={cn("skeleton-shimmer bg-secondary", ratio)} />
      <div className="space-y-2 p-3">
        <div className="skeleton-shimmer h-3.5 w-2/3 rounded-full bg-secondary" />
        <div className="skeleton-shimmer h-3 w-1/3 rounded-full bg-secondary" />
      </div>
    </div>
  )
}
