"use client"

import { useMemo, useState } from "react"
import { Images, Layers, Share2, Trash2 } from "lucide-react"
import { useApp } from "@/components/shared/app-context"
import { TopBar } from "@/components/shared/top-bar"
import { ActionMenu } from "@/components/shared/action-menu"
import { ConfirmDialog } from "@/components/shared/confirm-dialog"
import { EmptyState } from "@/components/shared/empty-state"
import { PLACEHOLDER_IMAGE, handleImageError, resolveAssetUrl } from "@/lib/asset"
import type { PostcardGroup } from "@/types"

export function PostcardsView() {
  const { postcardGroups, navigate, goBack, deletePostcardGroup, deletingId, toast } = useApp()
  const [pendingDelete, setPendingDelete] = useState<PostcardGroup | null>(null)
  const [imageRatios, setImageRatios] = useState<Record<string, number>>({})
  const deleting = !!pendingDelete && deletingId === pendingDelete.id

  const columns = useMemo(
    () =>
      buildMasonryColumns(postcardGroups, (group, index) => {
        const ratio = imageRatios[group.id] ?? DEFAULT_RATIOS[index % DEFAULT_RATIOS.length]
        return 100 / ratio + 52 + Math.min(group.location.length, 12) * 1.8
      }),
    [postcardGroups, imageRatios],
  )

  const rememberRatio = (id: string, img: HTMLImageElement) => {
    if (!img.naturalWidth || !img.naturalHeight) return
    const ratio = img.naturalWidth / img.naturalHeight
    setImageRatios((prev) => (Math.abs((prev[id] ?? 0) - ratio) < 0.01 ? prev : { ...prev, [id]: ratio }))
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <TopBar title="我的明信片" subtitle="旅行的高光时刻" />

      <div className="min-h-0 flex-1 overflow-y-auto no-scrollbar px-4 pb-10 pt-4 min-[400px]:px-5 min-[400px]:pt-5">
        {postcardGroups.length === 0 ? (
          <EmptyState
            icon={<Images className="size-7" aria-hidden />}
            title="还没有明信片"
            description="回到首页上传旅行照片，开启“生成明信片”，让旅行星球为你定格旅程。"
          >
            <button
              type="button"
              onClick={goBack}
              className="ui-pressable min-h-12 rounded-full bg-primary px-6 py-2.5 text-sm font-semibold text-primary-foreground shadow-[0_12px_24px_-16px_rgba(7,143,171,0.85)]"
            >
              去上传照片
            </button>
          </EmptyState>
        ) : (
          <div className="flex items-start gap-3 min-[400px]:gap-4">
            {columns.map((col, ci) => (
              <div key={ci} className="flex flex-1 flex-col gap-3 min-[400px]:gap-4">
                {col.map((group) => (
                  <GroupCard
                    key={group.id}
                    group={group}
                    onOpen={() => navigate({ page: "postcard-collection", groupId: group.id })}
                    onDelete={() => setPendingDelete(group)}
                    onShare={() => toast("分享链接已复制（示例）", "success")}
                    onImageLoad={(img) => rememberRatio(group.id, img)}
                  />
                ))}
              </div>
            ))}
          </div>
        )}
      </div>

      <ConfirmDialog
        open={!!pendingDelete}
        title="删除该明信片组？"
        description={pendingDelete ? `“${pendingDelete.location}”的 ${pendingDelete.postcards.length} 张明信片将被移除。` : ""}
        onClose={() => setPendingDelete(null)}
        actions={[
          {
            label: deleting ? "删除中…" : "删除",
            variant: "danger",
            onClick: () => {
              if (!pendingDelete || deleting) return
              void deletePostcardGroup(pendingDelete.id).then(() => setPendingDelete(null))
            },
          },
          { label: "取消", variant: "ghost", onClick: () => setPendingDelete(null) },
        ]}
      />
    </div>
  )
}

function GroupCard({
  group,
  onOpen,
  onDelete,
  onShare,
  onImageLoad,
}: {
  group: PostcardGroup
  onOpen: () => void
  onDelete: () => void
  onShare: () => void
  onImageLoad: (img: HTMLImageElement) => void
}) {
  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={`查看 ${group.location} 明信片`}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault()
          onOpen()
        }
      }}
      className="group relative cursor-pointer overflow-hidden rounded-[1.2rem] bg-card text-left shadow-[var(--shadow-surface)] ring-1 ring-[#0a3850]/11 transition focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
    >
      <div>
        <div className="relative overflow-hidden bg-secondary">
        <img
          src={resolveAssetUrl(group.coverImage) || PLACEHOLDER_IMAGE}
          alt={`${group.location} 明信片封面`}
          className="w-full object-cover transition duration-300 group-active:scale-[0.99]"
          onError={handleImageError}
          onLoad={(e) => onImageLoad(e.currentTarget)}
        />
        {/* 数量角标 */}
        {group.postcards.length > 1 && (
          <span className="absolute left-2 top-2 flex items-center gap-1 rounded-full bg-background/88 px-2 py-1 text-[11px] font-medium text-foreground shadow-sm backdrop-blur">
            <Layers className="size-3" aria-hidden />
            {group.postcards.length}
          </span>
        )}
        <div className="absolute right-2 top-2">
          <ActionMenu
            items={[
              { label: "分享", icon: <Share2 className="size-4" aria-hidden />, onClick: onShare },
              { label: "删除", icon: <Trash2 className="size-4" aria-hidden />, onClick: onDelete, danger: true },
            ]}
          />
        </div>
        </div>
        <div className="px-3 pb-3 pt-2.5">
          <p className="font-display text-[15px] font-bold leading-snug tracking-tight text-foreground text-balance">
            {group.location}
          </p>
          <div className="mt-2 flex items-center justify-between gap-2 font-editorial text-xs font-medium text-muted-foreground">
            <span className="truncate">{group.dateLabel}</span>
            <span className="shrink-0">{group.postcards.length} 张</span>
          </div>
        </div>
      </div>
    </div>
  )
}

const DEFAULT_RATIOS = [0.78, 1.18, 0.86, 1.02, 0.72, 1.28]

function buildMasonryColumns<T>(items: T[], estimateHeight: (item: T, index: number) => number): [T[], T[]] {
  const columns: [T[], T[]] = [[], []]
  const heights = [0, 0]
  items.forEach((item, index) => {
    const target = heights[0] <= heights[1] ? 0 : 1
    columns[target].push(item)
    heights[target] += estimateHeight(item, index) + 12
  })
  return columns
}
