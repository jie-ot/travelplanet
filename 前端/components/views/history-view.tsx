"use client"

import { useState } from "react"
import { useApp } from "@/components/shared/app-context"
import { TopBar } from "@/components/shared/top-bar"
import { ActionMenu } from "@/components/shared/action-menu"
import { ConfirmDialog } from "@/components/shared/confirm-dialog"
import { EmptyState } from "@/components/shared/empty-state"
import type { Plan } from "@/types"
import { Calendar, MapPin, Trash2, Compass, Pencil } from "lucide-react"

// 历史卡片仅需纯文本摘要：剥离 Markdown 标记（#、**强调**、行首 - 列表），换行折叠为空格
function toPlainPreview(md: string): string {
  return md
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/[*_`]/g, "")
    .replace(/^\s*#{1,6}\s+/gm, "")
    .replace(/^\s*[-*]\s+/gm, "")
    .replace(/\s*\n\s*/g, " ")
    .trim()
}

export function HistoryView() {
  const { plans, navigate, goBack, deletePlan, deletingId, beginEditPlan } = useApp()
  const [pendingDelete, setPendingDelete] = useState<Plan | null>(null)
  const deleting = !!pendingDelete && deletingId === pendingDelete.id

  return (
    <div className="flex h-full min-h-0 flex-col">
      <TopBar title="历史规划" />

      <div className="min-h-0 flex-1 overflow-y-auto no-scrollbar px-4 py-4 min-[400px]:px-5 min-[400px]:py-5">
        {plans.length === 0 ? (
          <EmptyState
            icon={<Compass className="size-7" aria-hidden />}
            title="还没有保存的规划"
            description="去「旅行规划」生成一段专属行程并保存吧。"
          >
            <button
              type="button"
              onClick={goBack}
              className="ui-pressable min-h-12 rounded-full bg-primary px-6 py-2.5 text-sm font-semibold text-primary-foreground shadow-[0_12px_24px_-16px_rgba(7,143,171,0.85)]"
            >
              去规划行程
            </button>
          </EmptyState>
        ) : (
          <ul className="flex flex-col gap-3 min-[400px]:gap-4">
            {plans.map((plan) => (
              <li key={plan.id}>
                <div
                  role="button"
                  tabIndex={0}
                  aria-label={`查看 ${plan.location} 历史规划`}
                  onClick={() => navigate({ page: "history-detail", planId: plan.id })}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault()
                      navigate({ page: "history-detail", planId: plan.id })
                    }
                  }}
                  className="group relative w-full cursor-pointer overflow-hidden rounded-[1.25rem] border border-[#0a3850]/10 bg-card/96 p-4 text-left shadow-[var(--shadow-surface)] transition focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
                >
                  <div className="min-w-0 flex-1 pr-12">
                    <h3 className="flex items-center gap-1.5 font-display text-base font-bold tracking-tight text-foreground">
                      <MapPin className="h-4 w-4 shrink-0 text-primary" aria-hidden />
                      {plan.location}
                    </h3>
                    <p className="mt-1 flex items-center gap-1.5 font-editorial text-xs font-medium text-muted-foreground">
                      <Calendar className="h-3.5 w-3.5" aria-hidden />
                      {plan.dateLabel}
                    </p>
                    <p className="mt-2 line-clamp-2 text-sm leading-relaxed text-muted-foreground">
                      {toPlainPreview(plan.content)}
                    </p>
                    <div className="mt-2.5 flex items-center gap-2">
                      <span className="rounded-full bg-secondary/85 px-2.5 py-0.5 text-xs font-medium text-secondary-foreground">
                        共 {plan.itineraryData.itinerary.length} 天
                      </span>
                      <span className="rounded-full bg-accent/12 px-2.5 py-0.5 text-xs font-medium text-accent-strong">
                        {plan.itineraryData.food_recommendations.length} 项美食
                      </span>
                    </div>
                  </div>
                  <div className="absolute right-2.5 top-2.5">
                    <ActionMenu
                      items={[
                        {
                          label: "重新编辑",
                          icon: <Pencil className="size-4" aria-hidden />,
                          onClick: () => {
                            // 继续编辑：导航前从缓存载入草稿（零网络），避免在规划页 effect 内同步 setState
                            beginEditPlan(plan)
                            navigate({ page: "planning", planId: plan.id })
                          },
                        },
                        {
                          label: "删除",
                          icon: <Trash2 className="size-4" aria-hidden />,
                          danger: true,
                          onClick: () => setPendingDelete(plan),
                        },
                      ]}
                    />
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      <ConfirmDialog
        open={!!pendingDelete}
        title="删除这条规划？"
        description={pendingDelete ? `“${pendingDelete.location}”的行程规划将被移除，删除后无法恢复。` : ""}
        onClose={() => setPendingDelete(null)}
        actions={[
          {
            label: deleting ? "删除中…" : "删除",
            variant: "danger",
            onClick: () => {
              if (!pendingDelete || deleting) return
              void deletePlan(pendingDelete.id).then(() => setPendingDelete(null))
            },
          },
          { label: "取消", variant: "ghost", onClick: () => setPendingDelete(null) },
        ]}
      />
    </div>
  )
}
