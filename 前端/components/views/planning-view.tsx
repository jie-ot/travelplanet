"use client"

import { useEffect, useRef, useState } from "react"
import { useApp } from "@/components/shared/app-context"
import { TopBar } from "@/components/shared/top-bar"
import { ItineraryDetail } from "@/components/shared/itinerary-detail"
import {
  PlanningConversation,
  type PlanningConversationMessage,
} from "@/components/shared/planning-conversation"
import { PlanningProgress } from "@/components/shared/planning-progress"
import { PlanningModelSelector } from "@/components/shared/planning-model-selector"
import { ConfirmDialog } from "@/components/shared/confirm-dialog"
import { MemoryEntryButton } from "@/components/shared/memory-entry-button"
import { TravelMemoryDrawer } from "@/components/shared/travel-memory-drawer"
import { usePlanningProgress } from "@/lib/use-planning-progress"
import { History, Sparkles, Save, RotateCcw, Send } from "lucide-react"
import type { PlanningBrief, PlanningChecklistItem, PlanningModel } from "@/types"

const DEFAULT_PLANNING_MODEL: PlanningModel = "doubao-seed-2.0-pro"

const INITIAL_MESSAGES: PlanningConversationMessage[] = [
  {
    id: "welcome",
    role: "assistant",
    content:
      "你好，我会先和你聊清楚这趟旅行，再整理一份确认清单。你可以从任何想法开始：想去哪、什么时候出发，或者只是想找点灵感。",
  },
]

export function PlanningView() {
  const {
    navigate,
    goBack,
    registerBackHandler,
    toast,
    toastCode,
    draftItineraryData,
    editingPlanId,
    hasUnsavedDraft,
    planning,
    planningProgressToken,
    planningTurn,
    planRefine,
    beginNewPlan,
    discardDraft,
    saving,
    savePlan,
  } = useApp()

  const [prompt, setPrompt] = useState("")
  const [refinePrompt, setRefinePrompt] = useState("")
  const [saved, setSaved] = useState(false)
  const [chatMessages, setChatMessages] =
    useState<PlanningConversationMessage[]>(INITIAL_MESSAGES)
  const [brief, setBrief] = useState<PlanningBrief | null>(null)
  const [checklist, setChecklist] = useState<PlanningChecklistItem[]>([])
  const [conversationPhase, setConversationPhase] =
    useState<"collecting" | "confirming">("collecting")
  const [selectedModel, setSelectedModel] =
    useState<PlanningModel>(DEFAULT_PLANNING_MODEL)
  const [refinementModelLocked, setRefinementModelLocked] = useState(false)
  const [generationRequested, setGenerationRequested] = useState(false)
  const [showMemoryDrawer, setShowMemoryDrawer] = useState(false)
  const [confirmationToken, setConfirmationToken] = useState<string | null>(null)
  const confirmationInFlight = useRef(false)
  // 未保存草稿返回拦截（规范 §10.5）
  const [showLeaveConfirm, setShowLeaveConfirm] = useState(false)
  const pendingLeave = useRef<(() => void) | null>(null)

  // 继续编辑的草稿载入已在「历史规划」点击时完成（beginEditPlan + URL 导航），此处仅消费 context

  const phase: "conversation" | "generating" | "result" =
    planning && (generationRequested || draftItineraryData)
      ? "generating"
      : draftItineraryData
        ? "result"
        : "conversation"
  // 只在待机屏可见时轮询；对话澄清轮不占用额外请求。
  const progress = usePlanningProgress(planningProgressToken, phase === "generating")
  const conversationModelLocked = chatMessages.some(
    (message) => !!message.planningModel,
  )
  const resultModelLocked = conversationModelLocked || refinementModelLocked

  async function handleConversationSend() {
    const text = prompt.trim()
    if (!text) {
      toastCode(1002)
      return
    }
    const userMessage: PlanningConversationMessage = {
      id: `user-${Date.now()}`,
      role: "user",
      content: text,
      planningModel: selectedModel,
    }
    const nextMessages = [...chatMessages, userMessage]
    setChatMessages(nextMessages)
    setPrompt("")
    setSaved(false)
    // Any new user requirement invalidates the checklist revision currently on
    // screen until the backend returns its newly normalized snapshot.
    setConfirmationToken(null)
    const response = await planningTurn({
      message: text,
      planningModel: selectedModel,
      context: null,
      messages: nextMessages.map(({ role, content, planningModel }) => ({
        role,
        content,
        planningModel,
      })),
      brief,
      confirmed: false,
    })
    if (!response) return
    setBrief(response.brief)
    setChecklist(response.checklist)
    setConfirmationToken(response.confirmationToken)
    if (response.phase !== "completed") setConversationPhase(response.phase)
    if (response.assistantMessage) {
      setChatMessages((current) => [
        ...current,
        {
          id: `assistant-${Date.now()}`,
          role: "assistant",
          content: response.assistantMessage,
          planningModel: response.planningModel,
        },
      ])
    }
  }

  async function handleConfirmPlan() {
    if (
      !brief ||
      !confirmationToken ||
      conversationPhase !== "confirming" ||
      planning ||
      confirmationInFlight.current
    ) return
    confirmationInFlight.current = true
    const confirmationText = "这份确认清单无误，请开始生成行程。"
    setGenerationRequested(true)
    try {
      // Confirmation is a state transition, not a magic chat phrase. Keep the
      // transcript unchanged and bind the request to the exact visible brief.
      const response = await planningTurn({
        message: confirmationText,
        planningModel: selectedModel,
        context: null,
        messages: chatMessages.map(({ role, content, planningModel }) => ({
          role,
          content,
          planningModel,
        })),
        brief,
        confirmed: true,
        confirmationToken,
      })
      if (!response || response.itinerary) return
      setBrief(response.brief)
      setChecklist(response.checklist)
      setConfirmationToken(response.confirmationToken)
      if (response.phase !== "completed") setConversationPhase(response.phase)
      if (response.assistantMessage) {
        setChatMessages((current) => [
          ...current,
          {
            id: `assistant-confirm-${Date.now()}`,
            role: "assistant",
            content: response.assistantMessage,
            planningModel: response.planningModel,
          },
        ])
      }
    } finally {
      setGenerationRequested(false)
      confirmationInFlight.current = false
    }
  }

  async function handleRefine() {
    const text = refinePrompt.trim()
    if (!text) {
      toast("请描述你想如何调整这份规划", "info")
      return
    }
    setSaved(false)
    setRefinePrompt("")
    setRefinementModelLocked(true)
    const result = await planRefine(text, selectedModel)
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
    setChatMessages(INITIAL_MESSAGES)
    setBrief(null)
    setChecklist([])
    setConversationPhase("collecting")
    setSelectedModel(DEFAULT_PLANNING_MODEL)
    setRefinementModelLocked(false)
    setGenerationRequested(false)
    setConfirmationToken(null)
  }

  function leavePlanning(action: () => void) {
    beginNewPlan()
    setPrompt("")
    setRefinePrompt("")
    setSaved(false)
    setSelectedModel(DEFAULT_PLANNING_MODEL)
    setRefinementModelLocked(false)
    setConfirmationToken(null)
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

  useEffect(
    () =>
      registerBackHandler(() => {
        const leave = () => {
          beginNewPlan()
          setPrompt("")
          setRefinePrompt("")
          setSaved(false)
          goBack()
        }
        if (hasUnsavedDraft && draftItineraryData) {
          pendingLeave.current = leave
          setShowLeaveConfirm(true)
          return
        }
        leave()
      }),
    [beginNewPlan, draftItineraryData, goBack, hasUnsavedDraft, registerBackHandler],
  )

  return (
    <div className="flex h-full min-h-0 flex-col">
      <TopBar
        title={editingPlanId ? "重新编辑规划" : "旅行规划"}
        onBack={() => guardedLeave(() => leavePlanning(goBack))}
        showUserBadge={false}
        right={
          <div className="flex w-full items-center justify-end gap-1.5">
            <button
              type="button"
              onClick={() =>
                guardedLeave(() => leavePlanning(() => navigate({ page: "history" })))
              }
              className="ui-pressable flex h-10 shrink-0 items-center justify-center gap-1 rounded-full bg-card/94 px-3 text-[0.7rem] font-semibold text-foreground shadow-sm ring-1 ring-border/80 min-[400px]:h-11 min-[400px]:gap-1.5 min-[400px]:px-3.5 min-[400px]:text-xs"
            >
              <History className="h-3.5 w-3.5 text-primary" aria-hidden />
              历史规划
            </button>
            <MemoryEntryButton onClick={() => setShowMemoryDrawer(true)} />
          </div>
        }
      />

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        {phase === "conversation" && (
          <PlanningConversation
            messages={chatMessages}
            phase={conversationPhase}
            checklist={checklist}
            value={prompt}
            busy={planning}
            planningModel={selectedModel}
            modelLocked={conversationModelLocked}
            confirmationReady={Boolean(confirmationToken)}
            onChange={setPrompt}
            onModelChange={setSelectedModel}
            onSend={() => void handleConversationSend()}
            onConfirm={() => void handleConfirmPlan()}
          />
        )}

        {phase === "generating" && (
          <div className="h-full overflow-y-auto px-4 py-6 no-scrollbar min-[400px]:px-5">
            <PlanningProgress snapshot={progress} />
          </div>
        )}

        {phase === "result" && draftItineraryData && (
          <div className="h-full overflow-y-auto pb-6 no-scrollbar">
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
          {!resultModelLocked ? (
            <div className="mb-3">
              <PlanningModelSelector
                value={selectedModel}
                locked={resultModelLocked}
                disabled={planning}
                compact
                onChange={setSelectedModel}
              />
            </div>
          ) : null}
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
                  guardedLeave(() => leavePlanning(() => navigate({ page: "history" })))
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
      {showMemoryDrawer && (
        <TravelMemoryDrawer onClose={() => setShowMemoryDrawer(false)} />
      )}
    </div>
  )
}
