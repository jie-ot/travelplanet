"use client"

import { useEffect, useState } from "react"
import { Check, Compass, ImageIcon, Loader2, Mail, MapPin, Route, Sparkles, Trash2, X } from "lucide-react"
import {
  deleteTravelMemoryDescription,
  getTravelMemory,
  updateTravelMemoryDescription,
  updateTravelMemoryOverview,
  updateTravelMemoryPlanningPreferences,
} from "@/lib/api"
import type {
  ItineraryData,
  Plan,
  PostcardGroup,
  Report,
  TravelMemoryDescription,
  TravelMemoryDisplay,
  TravelMemoryPlanningPreference,
} from "@/types"
import { useApp } from "@/components/shared/app-context"

const SOURCE_META = {
  照片分析: { icon: ImageIcon, label: "照片分析" },
  旅行规划: { icon: Compass, label: "旅行规划" },
  明信片创作: { icon: Mail, label: "明信片创作" },
}

const UNKNOWN_LOCATIONS = new Set(["未知地点", "未知目的地", ""])

const PLANNING_FIELD_ORDER: Array<{
  key: TravelMemoryPlanningPreference["key"]
  label: string
}> = [
  { key: "transport", label: "交通偏好" },
  { key: "hotel", label: "酒店偏好" },
  { key: "attractions", label: "景点偏好" },
  { key: "food", label: "餐饮偏好" },
  { key: "pace", label: "行程节奏" },
  { key: "other", label: "其他特别偏好" },
]

type PlanningForm = Record<TravelMemoryPlanningPreference["key"], string>

function planningFormFromMemory(memory: TravelMemoryDisplay): PlanningForm {
  const source = Object.fromEntries(
    memory.planningPreferences.map((item) => [item.key, item.value ?? ""]),
  ) as Partial<PlanningForm>
  return {
    transport: source.transport ?? "",
    hotel: source.hotel ?? "",
    attractions: source.attractions ?? "",
    food: source.food ?? "",
    pace: source.pace ?? "",
    other: source.other ?? "",
  }
}

function buildVisitedPlaces({
  draftItineraryData,
  plans,
  postcardGroups,
  reports,
}: {
  draftItineraryData: ItineraryData | null
  plans: Plan[]
  postcardGroups: PostcardGroup[]
  reports: Report[]
}): string[] {
  const seen = new Set<string>()
  const places: string[] = []
  const add = (location: string) => {
    const normalized = location.trim()
    if (UNKNOWN_LOCATIONS.has(normalized)) return
    const key = normalized.toLowerCase()
    if (seen.has(key)) return
    seen.add(key)
    places.push(normalized)
  }

  if (draftItineraryData) add(draftItineraryData.trip_info.destination)
  postcardGroups.forEach((group) => add(group.location))
  reports.forEach((report) => add(report.location))
  plans.forEach((plan) => add(plan.location))
  return places
}

function overviewText(memory: TravelMemoryDisplay): string {
  return (
    memory.overviewContent?.trim() ||
    memory.intro?.trim() ||
    "星球还在根据你的旅行记录学习你的长期偏好。"
  )
}

function MemoryOverview({
  memory,
  visitedPlaces,
  saving,
  onSave,
}: {
  memory: TravelMemoryDisplay
  visitedPlaces: string[]
  saving: boolean
  onSave: (content: string) => void
}) {
  const current = overviewText(memory)
  const [editing, setEditing] = useState(false)
  const [content, setContent] = useState(current)
  const visiblePlaces = visitedPlaces.slice(0, 5)
  const remainingPlaces = visitedPlaces.length - visiblePlaces.length

  return (
    <section className="relative overflow-hidden rounded-[1.5rem] border border-white/70 bg-white/82 p-4 shadow-[0_18px_42px_-32px_rgba(6,45,72,0.42)] ring-1 ring-[#0a3850]/8 min-[400px]:p-5">
      <div className="pointer-events-none absolute inset-0 opacity-45 [background-image:radial-gradient(circle_at_18px_24px,rgba(12,76,107,0.14)_1px,transparent_1.6px),radial-gradient(circle_at_82px_58px,rgba(12,76,107,0.1)_1px,transparent_1.6px)] [background-size:110px_86px]" />
      <div className="pointer-events-none absolute right-5 top-4 text-[#9ed4eb]">
        <Sparkles className="size-5" />
      </div>
      <div className="relative">
        <div className="mb-3 flex items-center gap-2">
          <span className="flex size-8 items-center justify-center rounded-full bg-[#e6f5fb] text-[#0d5574] ring-1 ring-[#0a3850]/8">
            <Sparkles className="size-4" />
          </span>
          <h3 className="font-display text-lg font-bold text-foreground">旅行记忆概述</h3>
        </div>

        {editing ? (
          <div>
            <textarea
              value={content}
              onChange={(event) => setContent(event.target.value)}
              rows={6}
              autoFocus
              className="min-h-[11rem] w-full resize-none rounded-[1.15rem] border border-[#8ec8dd]/55 bg-white px-4 py-3 text-[15px] leading-7 text-foreground shadow-[inset_0_1px_0_rgba(255,255,255,0.9)] outline-none transition focus:border-[#2f86aa] focus:ring-4 focus:ring-[#8ec8dd]/20"
            />
            {visiblePlaces.length > 0 ? (
              <VisitedPlaces places={visiblePlaces} remaining={remainingPlaces} />
            ) : null}
            <div className="mt-3 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => {
                  setContent(current)
                  setEditing(false)
                }}
                className="ui-pressable min-h-10 rounded-full bg-[#edf5f9] px-4 text-xs font-semibold text-[#426579]"
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => {
                  onSave(content)
                  setEditing(false)
                }}
                disabled={saving || !content.trim()}
                className="ui-pressable flex min-h-10 items-center gap-1.5 rounded-full bg-[#0d5574] px-4 text-xs font-semibold text-white shadow-sm disabled:opacity-55"
              >
                <Check className="size-3.5" />
                {saving ? "保存中…" : "保存"}
              </button>
            </div>
          </div>
        ) : (
          <button
            type="button"
            onClick={() => setEditing(true)}
            className="ui-pressable block w-full rounded-[1.15rem] px-1 py-1 text-left"
          >
            <p className="whitespace-pre-line text-[15px] leading-7 text-[#173d52]">{current}</p>
          </button>
        )}
        {!editing && visiblePlaces.length > 0 ? (
          <VisitedPlaces places={visiblePlaces} remaining={remainingPlaces} />
        ) : null}
      </div>
    </section>
  )
}

function VisitedPlaces({
  places,
  remaining,
}: {
  places: string[]
  remaining: number
}) {
  return (
    <div className="mt-4 overflow-hidden rounded-[1.15rem] border border-[#dcecf3] bg-[linear-gradient(180deg,rgba(247,252,255,0.94),rgba(239,248,252,0.9))] px-3.5 py-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.92)]">
      <div className="mb-2.5 flex items-center gap-2 text-xs font-semibold text-[#5c7a8c]">
        <span className="flex size-6 items-center justify-center rounded-full bg-white text-[#0d5574] shadow-sm ring-1 ring-[#0a3850]/8">
          <Route className="size-3.5" />
        </span>
        <span>留下过旅行轨迹</span>
      </div>
      <div className="relative pl-3">
        <span className="absolute bottom-3 left-[0.35rem] top-2 w-px bg-[linear-gradient(180deg,rgba(13,85,116,0.18),rgba(13,85,116,0.04))]" />
        <div className="flex flex-wrap gap-2">
          {places.map((place) => (
            <span
              key={place}
              className="inline-flex max-w-full items-center gap-1.5 rounded-full bg-white/88 px-3 py-1.5 text-xs font-semibold leading-5 text-[#244f64] shadow-[0_8px_18px_-16px_rgba(6,45,72,0.5)] ring-1 ring-[#0a3850]/8"
            >
              <MapPin className="size-3.5 shrink-0 text-[#66aeca]" />
              <span className="truncate">{place}</span>
            </span>
          ))}
          {remaining > 0 ? (
            <span className="inline-flex items-center rounded-full bg-[#e8f4f9] px-3 py-1.5 text-xs font-semibold leading-5 text-[#6d8796] ring-1 ring-[#0a3850]/6">
              还有 {remaining} 个地方
            </span>
          ) : null}
        </div>
      </div>
    </div>
  )
}

function PlanningPreferencesSection({
  memory,
  saving,
  onSave,
}: {
  memory: TravelMemoryDisplay
  saving: boolean
  onSave: (form: PlanningForm) => void
}) {
  const [form, setForm] = useState<PlanningForm>(() => planningFormFromMemory(memory))

  return (
    <section className="rounded-[1.45rem] border border-[#dbeef6] bg-white/84 p-4 shadow-[0_18px_40px_-32px_rgba(6,45,72,0.3)] ring-1 ring-[#0a3850]/7 min-[400px]:p-5">
      <div className="mb-2 flex items-center gap-2">
        <span className="flex size-8 items-center justify-center rounded-full bg-[#e6f5fb] text-[#0d5574] ring-1 ring-[#0a3850]/8">
          <Compass className="size-4" />
        </span>
        <div>
          <h3 className="font-display text-lg font-bold text-foreground">给规划填写的特别偏好</h3>
          <p className="text-xs leading-6 text-[#5a7482]">
            直接写会影响路线选择的硬偏好，比如住宿位置、交通方式、饮食禁忌或行程节奏。
          </p>
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        {PLANNING_FIELD_ORDER.map((field) => (
          <label
            key={field.key}
            className="rounded-[1.1rem] border border-[#e0edf3] bg-[linear-gradient(180deg,rgba(250,253,255,0.95),rgba(242,248,251,0.92))] p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.92)]"
          >
            <div className="mb-2 flex items-center justify-between gap-2">
              <span className="text-sm font-semibold text-[#244f64]">{field.label}</span>
            </div>
            <textarea
              value={form[field.key]}
              onChange={(event) => setForm((current) => ({ ...current, [field.key]: event.target.value }))}
              rows={2}
              placeholder={`例如：${field.label === "行程节奏" ? "不要太赶，留出午休和临时停留" : "可以直接写你更在意的特别偏好"}`}
              className="min-h-[4.5rem] w-full resize-none rounded-[0.95rem] border border-[#8ec8dd]/45 bg-white px-3 py-2.5 text-[14px] leading-6 text-foreground outline-none transition placeholder:text-[#97afbc] focus:border-[#2f86aa] focus:ring-4 focus:ring-[#8ec8dd]/18"
            />
          </label>
        ))}
      </div>

      <div className="mt-4 flex justify-end gap-2">
        <button
          type="button"
          onClick={() => setForm(planningFormFromMemory(memory))}
          className="ui-pressable min-h-10 rounded-full bg-[#edf5f9] px-4 text-xs font-semibold text-[#426579]"
        >
          重置
        </button>
        <button
          type="button"
          onClick={() => onSave(form)}
          disabled={saving}
          className="ui-pressable flex min-h-10 items-center gap-1.5 rounded-full bg-[#0d5574] px-4 text-xs font-semibold text-white shadow-sm disabled:opacity-55"
        >
          <Check className="size-3.5" />
          {saving ? "保存中…" : "保存特别偏好"}
        </button>
      </div>
    </section>
  )
}

function SourceBadges({ labels }: { labels: string[] }) {
  const visible = labels.filter((label) => label !== "手动编辑")
  if (visible.length === 0) return null

  return (
    <div className="mt-4 flex flex-wrap items-center gap-2 text-xs text-[#6a8190]">
      <span className="font-medium">来自：</span>
      {visible.map((label) => {
        const meta = SOURCE_META[label as keyof typeof SOURCE_META] ?? SOURCE_META.旅行规划
        const Icon = meta.icon
        return (
          <span
            key={label}
            className="inline-flex items-center gap-1.5 rounded-full bg-[#f0f7fa] px-2.5 py-1.5 font-medium text-[#315d73] ring-1 ring-[#0a3850]/7"
          >
            <Icon className="size-3.5" />
            {meta.label}
          </span>
        )
      })}
    </div>
  )
}

function MemoryCard({
  item,
  saving,
  deleting,
  onSave,
  onDelete,
}: {
  item: TravelMemoryDescription
  saving: boolean
  deleting: boolean
  onSave: (id: string, title: string, content: string) => void
  onDelete: (id: string) => void
}) {
  const [editingTitle, setEditingTitle] = useState(false)
  const [editingContent, setEditingContent] = useState(false)
  const editing = editingTitle || editingContent
  const [title, setTitle] = useState(item.title)
  const [content, setContent] = useState(item.content)

  function cancel() {
    setTitle(item.title)
    setContent(item.content)
    setEditingTitle(false)
    setEditingContent(false)
  }

  function save() {
    onSave(item.id, title, content)
    setEditingTitle(false)
    setEditingContent(false)
  }

  return (
    <article className="rounded-[1.35rem] border border-white/72 bg-white/86 p-4 shadow-[0_16px_38px_-30px_rgba(6,45,72,0.38)] ring-1 ring-[#0a3850]/7">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-[#e8f6fb] px-2.5 py-1 text-[11px] font-semibold text-[#2f6378] ring-1 ring-[#0a3850]/7">
            <Sparkles className="size-3" />
            长期记忆
          </span>

          {editingTitle ? (
            <input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              autoFocus
              className="mt-3 w-full rounded-[1rem] border border-[#8ec8dd]/55 bg-white px-3.5 py-2.5 text-base font-semibold leading-relaxed text-foreground outline-none transition focus:border-[#2f86aa] focus:ring-4 focus:ring-[#8ec8dd]/20"
            />
          ) : (
            <button
              type="button"
              onClick={() => setEditingTitle(true)}
              className="ui-pressable mt-3 block w-full text-left"
            >
              <h4 className="font-display text-base font-bold leading-7 text-foreground">{item.title}</h4>
            </button>
          )}
        </div>

        <button
          type="button"
          onClick={() => onDelete(item.id)}
          disabled={deleting}
          className="ui-icon-button flex size-9 shrink-0 items-center justify-center rounded-full bg-[#f5fafc] text-[#6a8190] ring-1 ring-[#0a3850]/8 disabled:opacity-50"
          aria-label={`删除${item.title}`}
          title="删除这条记忆"
        >
          <Trash2 className="size-4" />
        </button>
      </div>

      {editingContent ? (
        <textarea
          value={content}
          onChange={(event) => setContent(event.target.value)}
          rows={5}
          autoFocus
          className="mt-3 min-h-[9.5rem] w-full resize-none rounded-[1rem] border border-[#8ec8dd]/55 bg-white px-3.5 py-3 text-[15px] leading-7 text-foreground outline-none transition focus:border-[#2f86aa] focus:ring-4 focus:ring-[#8ec8dd]/20"
        />
      ) : (
        <button
          type="button"
          onClick={() => setEditingContent(true)}
          className="ui-pressable mt-3 block w-full text-left"
        >
          <p className="whitespace-pre-line text-[15px] leading-7 text-[#294d60]">{item.content}</p>
        </button>
      )}

      {item.planningHint ? (
        <div className="mt-3 rounded-[1rem] border border-[#dbeef6] bg-[#f7fcff] px-3.5 py-2.5 text-xs leading-6 text-[#4f6f80]">
          <span className="font-semibold text-[#2f6378]">规划帮助：</span>
          {item.planningHint}
        </div>
      ) : null}

      <SourceBadges labels={item.sourceLabels ?? []} />

      {editing ? (
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={cancel}
            className="ui-pressable min-h-10 rounded-full bg-[#edf5f9] px-4 text-xs font-semibold text-[#426579]"
          >
            取消
          </button>
          <button
            type="button"
            onClick={save}
            disabled={saving || !title.trim() || !content.trim()}
            className="ui-pressable flex min-h-10 items-center gap-1.5 rounded-full bg-[#0d5574] px-4 text-xs font-semibold text-white shadow-sm disabled:opacity-55"
          >
            <Check className="size-3.5" />
            {saving ? "保存中…" : "保存"}
          </button>
        </div>
      ) : null}
    </article>
  )
}

export function TravelMemoryDrawer({ onClose }: { onClose: () => void }) {
  const { draftItineraryData, plans, postcardGroups, reports, toast, toastError, registerBackHandler } = useApp()
  const [memory, setMemory] = useState<TravelMemoryDisplay | null>(null)
  const [loading, setLoading] = useState(true)
  const [savingOverview, setSavingOverview] = useState(false)
  const [savingPlanning, setSavingPlanning] = useState(false)
  const [savingId, setSavingId] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const visitedPlaces = buildVisitedPlaces({ draftItineraryData, plans, postcardGroups, reports })

  useEffect(() => registerBackHandler(onClose), [onClose, registerBackHandler])

  useEffect(() => {
    let cancelled = false
    void getTravelMemory()
      .then((data) => {
        if (!cancelled) setMemory(data)
      })
      .catch((err) => {
        if (!cancelled) toastError(err)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [plans, postcardGroups, reports, toastError])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose()
    }
    document.addEventListener("keydown", onKeyDown)
    return () => document.removeEventListener("keydown", onKeyDown)
  }, [onClose])

  async function handleSaveOverview(content: string) {
    const nextContent = content.trim()
    if (!nextContent) {
      toast("概述内容不能为空", "info")
      return
    }
    setSavingOverview(true)
    try {
      const next = await updateTravelMemoryOverview({
        title: "旅行记忆概述",
        content: nextContent,
      })
      setMemory(next)
      toast("概述已更新", "success")
    } catch (err) {
      toastError(err)
    } finally {
      setSavingOverview(false)
    }
  }

  async function handleSave(id: string, title: string, content: string) {
    const nextTitle = title.trim()
    const nextContent = content.trim()
    if (!nextTitle || !nextContent) {
      toast("记忆内容不能为空", "info")
      return
    }
    setSavingId(id)
    try {
      const next = await updateTravelMemoryDescription(id, {
        title: nextTitle,
        content: nextContent,
      })
      setMemory(next)
      toast("旅行记忆已更新", "success")
    } catch (err) {
      toastError(err)
    } finally {
      setSavingId(null)
    }
  }

  async function handleSavePlanning(form: PlanningForm) {
    setSavingPlanning(true)
    try {
      const next = await updateTravelMemoryPlanningPreferences(form)
      setMemory(next)
      toast("特别偏好已更新", "success")
    } catch (err) {
      toastError(err)
    } finally {
      setSavingPlanning(false)
    }
  }

  async function handleDelete(id: string) {
    setMemory((current) =>
      current
        ? {
            ...current,
            memories: current.memories.filter((item) => item.id !== id),
          }
        : current,
    )
    setDeletingId(id)
    try {
      const next = await deleteTravelMemoryDescription(id)
      setMemory(next)
      toast("已删除这条长期记忆", "success")
    } catch (err) {
      toastError(err)
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-end bg-foreground/34 backdrop-blur-md animate-in fade-in"
      onClick={onClose}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="travel-memory-title"
        className="max-h-[88vh] w-full overflow-hidden rounded-t-[1.75rem] border border-white/60 bg-[#f4faff]/98 shadow-[0_-28px_80px_-34px_rgba(6,45,72,0.7)] ring-1 ring-[#0a3850]/10 backdrop-blur-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="mx-auto mt-2 h-1.5 w-12 rounded-full bg-[#0a3850]/16" />
        <div className="relative overflow-hidden px-5 pb-5 pt-5">
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-px bg-white/80" />
          <div className="pointer-events-none absolute right-8 top-6 h-12 w-20 rounded-[50%] border border-[#9ed4eb]/35" />
          <div className="pointer-events-none absolute right-12 top-10 h-px w-16 rotate-[-18deg] bg-[#9ed4eb]/45" />
          <div className="flex items-start gap-3">
            <div className="min-w-0 flex-1">
              <h2 id="travel-memory-title" className="font-display text-2xl font-bold leading-tight text-foreground">
                我的旅行记忆
              </h2>
              <p className="mt-2 text-sm font-medium leading-6 text-[#5a7482]">
                星球会根据你的旅行记录，慢慢了解你的偏好
              </p>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="ui-icon-button flex size-10 shrink-0 items-center justify-center rounded-full bg-white/82 text-foreground shadow-sm ring-1 ring-[#0a3850]/10"
              aria-label="关闭旅行记忆"
            >
              <X className="size-5" />
            </button>
          </div>
        </div>

        <div className="max-h-[calc(88vh-6.9rem)] overflow-y-auto px-5 pb-[max(1.5rem,env(safe-area-inset-bottom))] pt-5">
          {loading && (
            <div className="flex min-h-64 flex-col items-center justify-center text-muted-foreground">
              <Loader2 className="size-6 animate-spin text-primary" />
              <p className="mt-3 text-sm font-medium">正在读取旅行记忆…</p>
            </div>
          )}

          {!loading && !memory && (
            <section className="py-12 text-center text-sm leading-7 text-muted-foreground">
              暂时没有读取到旅行记忆，请稍后再试。
            </section>
          )}

          {!loading && memory && (
            <div className="space-y-7">
              <MemoryOverview
                key={`overview-${memory.version}-${overviewText(memory)}`}
                memory={memory}
                visitedPlaces={visitedPlaces}
                saving={savingOverview}
                onSave={(content) => void handleSaveOverview(content)}
              />

              <PlanningPreferencesSection
                key={`planning-preferences-${memory.version}`}
                memory={memory}
                saving={savingPlanning}
                onSave={(form) => void handleSavePlanning(form)}
              />

              <section>
                <div className="mb-3 flex items-center gap-2">
                  <span className="flex size-8 items-center justify-center rounded-full bg-white/82 text-[#0d5574] ring-1 ring-[#0a3850]/8">
                    <Sparkles className="size-4" />
                  </span>
                  <h3 className="font-display text-lg font-bold text-foreground">星球记住了这些</h3>
                </div>

                {memory.memories.length > 0 ? (
                  <div className="space-y-3.5">
                    {memory.memories.map((item) => (
                      <MemoryCard
                        key={`${item.id}-${item.title}-${item.content}`}
                        item={item}
                        saving={savingId === item.id}
                        deleting={deletingId === item.id}
                        onSave={(id, title, content) => void handleSave(id, title, content)}
                        onDelete={(id) => void handleDelete(id)}
                      />
                    ))}
                  </div>
                ) : (
                  <section className="rounded-[1.25rem] border border-white/70 bg-white/78 px-4 py-8 text-sm leading-7 text-[#5f7c8c] shadow-[0_16px_38px_-32px_rgba(6,45,72,0.36)]">
                    星球还没有足够确定的长期旅行记忆。
                  </section>
                )}
              </section>
            </div>
          )}
        </div>
      </section>
    </div>
  )
}
