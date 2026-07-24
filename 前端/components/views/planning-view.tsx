"use client"

import { useRef, useState } from "react"
import { useApp } from "@/components/shared/app-context"
import { TopBar } from "@/components/shared/top-bar"
import { ItineraryDetail } from "@/components/shared/itinerary-detail"
import { PlanningProgress } from "@/components/shared/planning-progress"
import { ConfirmDialog } from "@/components/shared/confirm-dialog"
import { Sparkles, Save, RotateCcw, Wand2, History, Send } from "lucide-react"

export function PlanningView() {
  const {
    navigate,
    goBack,
    toast,
    toastCode,
    draftItineraryData,
    editingPlanId,
    hasUnsavedDraft,
    planning,
    planFirstTurn,
    planRefine,
    beginNewPlan,
    discardDraft,
    saving,
    savePlan,
  } = useApp()

  const [prompt, setPrompt] = useState("")
  const [refinePrompt, setRefinePrompt] = useState("")
  const [saved, setSaved] = useState(false)
  // 未保存草稿返回拦截（规范 §10.5）
  const [showLeaveConfirm, setShowLeaveConfirm] = useState(false)
  const pendingLeave = useRef<(() => void) | null>(null)

  // 继续编辑的草稿载入已在「历史规划」点击时完成（beginEditPlan + URL 导航），此处仅消费 context

  const phase: "input" | "generating" | "result" = planning
    ? "generating"
    : draftItineraryData
      ? "result"
      : "input"

  async function handleGenerate() {
    const text = prompt.trim()
    if (!text) {
      toastCode(1002)
      return
    }
    setSaved(false)
    const result = await planFirstTurn(text)
    if (result) setPrompt("")
  }

  async function handleRefine() {
    const text = refinePrompt.trim()
    if (!text) {
      toast("请描述你想如何调整这份规划", "info")
      return
    }
    setSaved(false)
    setRefinePrompt("")
    const result = await planRefine(text)
    if (result) toast("已根据你的调整重新打磨规划", "success")
  }

  async function handleSave() {
    const wasEditing = !!editingPlanId
    const plan = await savePlan()
    if (plan) {
      setSaved(true)
      toast(wasEditing ? "已更新该规划" : "已保存到历史规划", "success")
    }
  }

  function handleStartOver() {
    beginNewPlan()
    setPrompt("")
    setRefinePrompt("")
    setSaved(false)
  }

  function leavePlanning(action: () => void) {
    beginNewPlan()
    setPrompt("")
    setRefinePrompt("")
    setSaved(false)
    action()
  }

  // 离开规划页前若有未保存草稿则拦截确认
  function guardedLeave(action: () => void) {
    if (hasUnsavedDraft && draftItineraryData) {
      pendingLeave.current = action
      setShowLeaveConfirm(true)
    } else {
      action()
    }
  }

  function runPendingLeave() {
    const action = pendingLeave.current
    pendingLeave.current = null
    setShowLeaveConfirm(false)
    action?.()
  }

  return (
    <div className="flex h-full flex-col">
      <TopBar
        title={editingPlanId ? "重新编辑规划" : "旅行规划"}
        onBack={() => guardedLeave(() => leavePlanning(goBack))}
        right={
          <button
            type="button"
            onClick={() =>
              guardedLeave(() =>
                leavePlanning(() =>
                  editingPlanId ? goBack() : navigate({ page: "history" }),
                ),
              )
            }
            className="ui-pressable flex min-h-11 items-center gap-1.5 rounded-full bg-card/94 px-3.5 py-2 text-xs font-semibold text-foreground shadow-sm ring-1 ring-border/80"
          >
            <History className="h-3.5 w-3.5 text-primary" aria-hidden />
            历史规划
          </button>
        }
      />

      <div className="flex-1 overflow-y-auto no-scrollbar">
        {phase === "input" && (
          <div className="flex flex-col gap-5 px-4 py-5 min-[400px]:gap-6 min-[400px]:px-5 min-[400px]:py-6">
            <div className="rounded-2xl border border-white/10 bg-[#07516c] p-5 text-white shadow-[0_16px_32px_-22px_rgba(6,45,72,0.85)]">
              <div className="flex items-center gap-2 text-[#73d4df]">
                <Wand2 className="h-5 w-5" />
                <span className="font-display text-sm font-bold tracking-tight">告诉星球你的旅行想法</span>
              </div>
              <p className="mt-2 font-editorial text-sm font-medium leading-relaxed text-white/76">
                描述出发日期、旅行天数、出发地、目的地等，为您生成专属行程。
              </p>
            </div>

            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="例如：我想在7月20号从武汉去大理、丽江玩3天，我喜欢古城和自然风光..."
              rows={5}
              className="w-full resize-none rounded-2xl border border-[#0a3850]/12 bg-card/95 p-4 text-sm leading-relaxed text-foreground shadow-[0_12px_24px_-22px_rgba(6,45,72,0.7)] outline-none transition placeholder:text-muted-foreground focus:border-primary focus:ring-2 focus:ring-primary/20"
            />

            <button
              type="button"
              onClick={handleGenerate}
              className="ui-pressable mt-1 flex min-h-12 items-center justify-center gap-2 rounded-full bg-primary py-3 text-sm font-semibold text-primary-foreground shadow-[0_14px_26px_-16px_rgba(7,143,171,0.9)]"
            >
              <Sparkles className="h-4 w-4" />
              生成专属行程
            </button>
          </div>
        )}

        {phase === "generating" && (
          <div className="min-h-full px-4 py-6 min-[400px]:px-5">
            <PlanningProgress />
          </div>
        )}

        {phase === "result" && draftItineraryData && (
          <div className="pb-6">
            <div className="mx-4 mb-4 mt-5 flex items-start gap-2 rounded-xl border border-primary/10 bg-secondary/85 px-3 py-2.5 text-sm text-secondary-foreground min-[400px]:mx-5">
              <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
              <span>
                {editingPlanId
                  ? "这是你之前保存的规划，可在下方继续用文字描述来打磨它。"
                  : "已为你生成专属行程，可在下方继续补充想法、随时调整。"}
              </span>
            </div>
            <ItineraryDetail data={draftItineraryData} />
          </div>
        )}
      </div>

      {/* 结果态底部操作区：对话式打磨 + 保存 */}
      {phase === "result" && draftItineraryData && (
        <div className="border-t border-[#0a3850]/12 bg-card/96 px-4 py-3 shadow-[0_-12px_30px_-24px_rgba(6,45,72,0.65)] backdrop-blur min-[400px]:px-5 min-[400px]:py-4">
          <div className="flex items-end gap-2">
            <textarea
              value={refinePrompt}
              onChange={(e) => setRefinePrompt(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault()
                  void handleRefine()
                }
              }}
              placeholder="继续描述如何调整，例如：第二天多留点自由时间"
              rows={1}
              className="max-h-24 min-h-[2.75rem] flex-1 resize-none rounded-2xl border border-border/80 bg-card px-3.5 py-3 text-sm leading-snug text-foreground outline-none transition placeholder:text-muted-foreground focus:border-primary focus:ring-2 focus:ring-primary/20"
            />
            <button
              type="button"
              onClick={() => void handleRefine()}
              className="ui-icon-button flex size-11 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-md"
              aria-label="发送调整"
            >
              <Send className="h-5 w-5" />
            </button>
          </div>

          <div className="mt-2.5">
            <button
              type="button"
              onClick={() => void handleSave()}
              disabled={saving || (saved && !hasUnsavedDraft)}
              className="ui-pressable flex min-h-12 w-full items-center justify-center gap-1.5 rounded-full bg-primary py-2.5 text-sm font-semibold text-primary-foreground shadow-md disabled:opacity-60"
            >
              <Save className="h-4 w-4" />
              {saving ? "保存中…" : saved && !hasUnsavedDraft ? "已保存" : editingPlanId ? "更新规划" : "保存规划"}
            </button>
          </div>

          <div className="mt-2 flex items-center justify-center gap-4">
            <button
              type="button"
              onClick={handleStartOver}
              className="ui-pressable flex min-h-11 items-center gap-1 rounded-full px-3 text-xs font-medium text-muted-foreground"
            >
              <RotateCcw className="h-3.5 w-3.5" />
              重新开始
            </button>
            {saved && (
              <button
                type="button"
                onClick={() =>
                  leavePlanning(() =>
                    editingPlanId ? goBack() : navigate({ page: "history" }),
                  )
                }
                className="ui-pressable min-h-11 rounded-full px-3 text-xs font-medium text-primary"
              >
                前往「历史规划」查看 →
              </button>
            )}
          </div>
        </div>
      )}

      <ConfirmDialog
        open={showLeaveConfirm}
        title="还有未保存的规划"
        description="离开后未保存的修改将丢失，是否先保存这份草稿？"
        onClose={() => {
          pendingLeave.current = null
          setShowLeaveConfirm(false)
        }}
        actions={[
          {
            label: saving ? "保存中…" : "保存草稿",
            variant: "primary",
            onClick: () => {
              void (async () => {
                const plan = await savePlan()
                if (plan) runPendingLeave()
              })()
            },
          },
          {
            label: "不保存，直接离开",
            variant: "danger",
            onClick: () => {
              discardDraft()
              runPendingLeave()
            },
          },
          {
            label: "取消",
            variant: "ghost",
            onClick: () => {
              pendingLeave.current = null
              setShowLeaveConfirm(false)
            },
          },
        ]}
      />
    </div>
  )
}
