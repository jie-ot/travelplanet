"use client"

import { useMemo, useState } from "react"
import { FileChartColumn, Sparkles, Trash2 } from "lucide-react"
import { useApp } from "@/components/shared/app-context"
import { TopBar } from "@/components/shared/top-bar"
import { ActionMenu } from "@/components/shared/action-menu"
import { ConfirmDialog } from "@/components/shared/confirm-dialog"
import { EmptyState } from "@/components/shared/empty-state"
import { PLACEHOLDER_IMAGE, handleImageError, resolveAssetUrl } from "@/lib/asset"
import type { Report } from "@/types"

export function ReportsView() {
  const { reports, navigate, goBack, deleteReport, deletingId } = useApp()
  const [pendingDelete, setPendingDelete] = useState<Report | null>(null)
  const [imageRatios, setImageRatios] = useState<Record<string, number>>({})
  const deleting = !!pendingDelete && deletingId === pendingDelete.id

  const columns = useMemo(
    () =>
      buildMasonryColumns(reports, (report, index) => {
        const ratio = imageRatios[report.id] ?? DEFAULT_RATIOS[index % DEFAULT_RATIOS.length]
        return 100 / ratio + 72 + Math.min(report.personalitySummary.length, 16) * 2.4
      }),
    [reports, imageRatios],
  )

  const rememberRatio = (id: string, img: HTMLImageElement) => {
    if (!img.naturalWidth || !img.naturalHeight) return
    const ratio = img.naturalWidth / img.naturalHeight
    setImageRatios((prev) => (Math.abs((prev[id] ?? 0) - ratio) < 0.01 ? prev : { ...prev, [id]: ratio }))
  }

  return (
    <>
      <TopBar title="旅行人格档案" subtitle="读懂旅途中的你" />

      <div className="flex-1 overflow-y-auto no-scrollbar px-4 pb-10 pt-4 min-[400px]:px-5 min-[400px]:pt-5">
        {reports.length === 0 ? (
          <EmptyState
            icon={<FileChartColumn className="size-7" aria-hidden />}
            title="还没有人格报告"
            description="上传照片并开启“生成报告”，旅行星球会分析出你的旅行人格画像。"
          >
            <button
              type="button"
              onClick={goBack}
              className="ui-pressable min-h-12 rounded-full bg-primary px-6 py-2.5 text-sm font-semibold text-primary-foreground shadow-[0_12px_24px_-16px_rgba(7,143,171,0.85)]"
            >
              去生成报告
            </button>
          </EmptyState>
        ) : (
          <div className="flex items-start gap-3 min-[400px]:gap-4">
            {columns.map((col, ci) => (
              <div key={ci} className="flex flex-1 flex-col gap-3 min-[400px]:gap-4">
                {col.map((report) => (
                  <ReportCard
                    key={report.id}
                    report={report}
                    onOpen={() => navigate({ page: "report-detail", reportId: report.id })}
                    onDelete={() => setPendingDelete(report)}
                    onImageLoad={(img) => rememberRatio(report.id, img)}
                  />
                ))}
              </div>
            ))}
          </div>
        )}
      </div>

      <ConfirmDialog
        open={!!pendingDelete}
        title="删除该报告？"
        description={pendingDelete ? `“${pendingDelete.location}”的旅行人格报告将被移除。` : ""}
        onClose={() => setPendingDelete(null)}
        actions={[
          {
            label: deleting ? "删除中…" : "删除",
            variant: "danger",
            onClick: () => {
              if (!pendingDelete || deleting) return
              void deleteReport(pendingDelete.id).then(() => setPendingDelete(null))
            },
          },
          { label: "取消", variant: "ghost", onClick: () => setPendingDelete(null) },
        ]}
      />
    </>
  )
}

function ReportCard({
  report,
  onOpen,
  onDelete,
  onImageLoad,
}: {
  report: Report
  onOpen: () => void
  onDelete: () => void
  onImageLoad: (img: HTMLImageElement) => void
}) {
  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={`查看 ${report.location} 旅行人格报告`}
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
          src={resolveAssetUrl(report.coverImage) || PLACEHOLDER_IMAGE}
          alt={`${report.location} 报告封面`}
          className="w-full object-cover transition duration-300 group-active:scale-[0.99]"
          onError={handleImageError}
          onLoad={(e) => onImageLoad(e.currentTarget)}
        />
        <span className="absolute left-2 top-2 flex items-center gap-1 rounded-full bg-background/88 px-2 py-1 text-[11px] font-medium text-foreground shadow-sm backdrop-blur">
          <Sparkles className="size-3" aria-hidden />
          人格
        </span>
        <div className="absolute right-2 top-2">
          <ActionMenu
            items={[{ label: "删除", icon: <Trash2 className="size-4" aria-hidden />, onClick: onDelete, danger: true }]}
          />
        </div>
        </div>
        <div className="px-3 pb-3 pt-2.5">
          <p className="font-editorial text-[11px] font-medium text-muted-foreground">
            {report.location} · {report.dateLabel}
          </p>
          <p className="mt-1 font-display text-[15px] font-bold leading-snug tracking-tight text-foreground text-balance">
            {report.personalitySummary}
          </p>
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
