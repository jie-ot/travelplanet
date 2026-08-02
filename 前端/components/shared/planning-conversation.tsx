"use client"

import { useEffect, useRef } from "react"
import {
  Bot,
  Check,
  CheckCircle2,
  CircleAlert,
  ClipboardCheck,
  MessageCircleMore,
  Send,
  Sparkles,
  WandSparkles,
} from "lucide-react"
import type { PlanningChecklistItem, PlanningPhase } from "@/types"

export interface PlanningConversationMessage {
  id: string
  role: "user" | "assistant"
  content: string
}

interface PlanningConversationProps {
  messages: PlanningConversationMessage[]
  phase: Exclude<PlanningPhase, "completed">
  checklist: PlanningChecklistItem[]
  value: string
  busy: boolean
  onChange: (value: string) => void
  onSend: () => void
  onConfirm: () => void
}

const STEP_LABELS = ["聊需求", "确认清单", "生成行程"]

export function PlanningConversation({
  messages,
  phase,
  checklist,
  value,
  busy,
  onChange,
  onSend,
  onConfirm,
}: PlanningConversationProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    })
  }, [busy, messages, phase])

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="planning-conversation">
      <div ref={scrollRef} className="flex-1 overflow-y-auto no-scrollbar">
        <div className="space-y-5 px-4 pb-6 pt-5 min-[400px]:px-5">
          <section className="overflow-hidden rounded-[1.45rem] border border-white/10 bg-[linear-gradient(140deg,#063e5d_0%,#075c72_55%,#087d8d_100%)] px-5 py-5 text-white shadow-[0_20px_42px_-28px_rgba(3,47,71,0.9)]">
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="flex items-center gap-2 text-[#84e3e3]">
                  <MessageCircleMore className="h-4 w-4" aria-hidden />
                  <span className="text-xs font-bold tracking-[0.14em]">先聊清楚，再出发</span>
                </div>
                <h2 className="mt-2 font-display text-xl font-bold tracking-tight">
                  和星球一起把旅程想明白
                </h2>
                <p className="mt-2 max-w-[26rem] text-sm leading-6 text-white/70">
                  我会先理解和追问，整理成清单。只有你确认后，才会查询事实并生成完整行程。
                </p>
              </div>
              <span className="grid size-11 shrink-0 place-items-center rounded-2xl bg-white/10 ring-1 ring-white/15">
                <Sparkles className="h-5 w-5 text-[#8ce6e4]" aria-hidden />
              </span>
            </div>
            <ol className="mt-5 grid grid-cols-3 gap-2" aria-label="规划步骤">
              {STEP_LABELS.map((label, index) => {
                const activeIndex = phase === "confirming" ? 1 : 0
                const done = index < activeIndex
                const active = index === activeIndex
                return (
                  <li
                    key={label}
                    className={`rounded-xl px-2 py-2 text-center text-[11px] font-semibold ${
                      active
                        ? "bg-white text-[#07516c]"
                        : done
                          ? "bg-white/14 text-[#bdf0e9]"
                          : "bg-black/10 text-white/46"
                    }`}
                  >
                    <span className="mx-auto mb-1 grid size-5 place-items-center rounded-full border border-current/30">
                      {done ? <Check className="h-3 w-3" /> : index + 1}
                    </span>
                    {label}
                  </li>
                )
              })}
            </ol>
          </section>

          <div className="space-y-4" aria-live="polite">
            {messages.map((message) => (
              <div
                key={message.id}
                className={`flex items-end gap-2.5 ${
                  message.role === "user" ? "justify-end" : "justify-start"
                }`}
              >
                {message.role === "assistant" ? (
                  <span className="grid size-8 shrink-0 place-items-center rounded-full bg-[#0b536d] text-[#86e2e2] shadow-sm">
                    <Bot className="h-4 w-4" aria-hidden />
                  </span>
                ) : null}
                <div
                  className={`max-w-[84%] rounded-2xl px-4 py-3 text-sm leading-6 shadow-[0_10px_24px_-22px_rgba(6,45,72,0.8)] ${
                    message.role === "user"
                      ? "rounded-br-md bg-primary text-primary-foreground"
                      : "rounded-bl-md border border-[#0a3850]/10 bg-card text-foreground"
                  }`}
                >
                  {message.content}
                </div>
              </div>
            ))}

            {busy ? (
              <div className="flex items-end gap-2.5" data-testid="planning-thinking">
                <span className="grid size-8 shrink-0 place-items-center rounded-full bg-[#0b536d] text-[#86e2e2]">
                  <Bot className="h-4 w-4" aria-hidden />
                </span>
                <div className="flex items-center gap-1.5 rounded-2xl rounded-bl-md border border-[#0a3850]/10 bg-card px-4 py-3">
                  {[0, 1, 2].map((index) => (
                    <span
                      key={index}
                      className="size-1.5 animate-pulse rounded-full bg-primary/70"
                      style={{ animationDelay: `${index * 180}ms` }}
                    />
                  ))}
                  <span className="ml-1 text-xs text-muted-foreground">正在整理你的想法</span>
                </div>
              </div>
            ) : null}
          </div>

          {phase === "confirming" && checklist.length > 0 ? (
            <section
              className="rounded-[1.4rem] border border-[#0b8ca5]/18 bg-[linear-gradient(180deg,rgba(255,255,255,.98),rgba(238,249,248,.96))] p-4 shadow-[0_18px_38px_-30px_rgba(7,83,105,0.8)]"
              data-testid="planning-checklist"
            >
              <header className="flex items-start gap-3">
                <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-[#dff6f3] text-[#087d8d]">
                  <ClipboardCheck className="h-5 w-5" aria-hidden />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="font-display text-base font-bold text-[#073d56]">出发前确认清单</h3>
                    <span className="rounded-full bg-[#fff0cd] px-2 py-1 text-[10px] font-bold text-[#986309]">
                      待你确认
                    </span>
                  </div>
                  <p className="mt-1 text-xs leading-5 text-muted-foreground">
                    有任何不准确，直接继续告诉我；确认后才会开始查询和生成。
                  </p>
                </div>
              </header>

              <dl className="mt-4 grid gap-2 sm:grid-cols-2">
                {checklist.map((item) => {
                  const Icon =
                    item.status === "ready"
                      ? CheckCircle2
                      : item.status === "assumed"
                        ? WandSparkles
                        : CircleAlert
                  return (
                    <div
                      key={item.key}
                      className={`rounded-xl border px-3 py-2.5 ${
                        item.status === "missing"
                          ? "border-amber-300/70 bg-amber-50"
                          : "border-[#0a3850]/8 bg-white/72"
                      }`}
                    >
                      <dt className="flex items-center gap-1.5 text-[11px] font-semibold text-muted-foreground">
                        <Icon
                          className={`h-3.5 w-3.5 ${
                            item.status === "ready"
                              ? "text-[#0a9a84]"
                              : item.status === "assumed"
                                ? "text-[#c18516]"
                                : "text-amber-600"
                          }`}
                          aria-hidden
                        />
                        {item.label}
                      </dt>
                      <dd className="mt-1 text-sm font-semibold leading-5 text-[#0a3850]">
                        {item.value}
                      </dd>
                    </div>
                  )
                })}
              </dl>

              <div className="mt-4 grid gap-2 sm:grid-cols-[1fr_auto]">
                <button
                  type="button"
                  onClick={onConfirm}
                  disabled={busy}
                  className="ui-pressable flex min-h-12 items-center justify-center gap-2 rounded-full bg-primary px-5 text-sm font-semibold text-primary-foreground shadow-[0_14px_26px_-18px_rgba(7,143,171,0.9)] disabled:opacity-60"
                  data-testid="confirm-and-generate"
                >
                  <Sparkles className="h-4 w-4" aria-hidden />
                  确认无误，生成行程
                </button>
                <button
                  type="button"
                  onClick={() => inputRef.current?.focus()}
                  className="ui-pressable min-h-11 rounded-full px-4 text-sm font-semibold text-primary"
                >
                  继续补充
                </button>
              </div>
            </section>
          ) : null}
        </div>
      </div>

      <div className="shrink-0 border-t border-[#0a3850]/10 bg-card/96 px-4 py-3 shadow-[0_-12px_30px_-26px_rgba(6,45,72,0.7)] backdrop-blur min-[400px]:px-5">
        <div className="flex items-end gap-2">
          <textarea
            ref={inputRef}
            value={value}
            onChange={(event) => onChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault()
                onSend()
              }
            }}
            disabled={busy}
            placeholder={
              phase === "confirming"
                ? "还想补充预算、节奏、住宿或任何偏好…"
                : "说说你的旅行想法…"
            }
            rows={1}
            className="max-h-28 min-h-12 flex-1 resize-none rounded-2xl border border-border/80 bg-[#f8fbfc] px-4 py-3 text-sm leading-6 text-foreground outline-none transition placeholder:text-muted-foreground focus:border-primary focus:ring-2 focus:ring-primary/20 disabled:opacity-65"
            aria-label="旅行需求"
            data-testid="planning-chat-input"
          />
          <button
            type="button"
            onClick={onSend}
            disabled={busy || !value.trim()}
            className="ui-icon-button grid size-12 shrink-0 place-items-center rounded-full bg-primary text-primary-foreground shadow-md disabled:opacity-45"
            aria-label="发送旅行需求"
            data-testid="planning-chat-send"
          >
            <Send className="h-5 w-5" aria-hidden />
          </button>
        </div>
        <p className="mt-2 text-center text-[10px] text-muted-foreground">
          Enter 发送 · Shift + Enter 换行
        </p>
      </div>
    </div>
  )
}
