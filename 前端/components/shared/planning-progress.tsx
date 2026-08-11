"use client"

/**
 * 行程生成待机屏。
 *
 * 展示的是后端 planning_progress 的真实阶段与耗时，而非固定节奏的假动画：
 * 阶段名、研究轮次、外部查询次数、已核实事实数、预计剩余时间都来自轮询快照。
 * 快照可能缺失（令牌尚未注册、轮询抖动、进程重启），此时退回“准备中”的
 * 不定态展示，绝不阻塞或误导。
 */

import { useEffect, useRef, useState } from "react"
import {
  BrainCircuit,
  Check,
  Radar,
  Route,
  ShieldCheck,
} from "lucide-react"
import type { PlanningProgressPhase, PlanningProgressSnapshot } from "@/types"

const MILESTONES: Array<{
  phase: PlanningProgressPhase
  label: string
  icon: typeof BrainCircuit
}> = [
  { phase: "preparing", label: "理解旅行需求", icon: BrainCircuit },
  { phase: "researching", label: "核对交通住宿事实", icon: Radar },
  { phase: "composing", label: "编排每日行程", icon: Route },
  { phase: "verifying", label: "校验时刻与可执行性", icon: ShieldCheck },
]

const FALLBACK_LABEL = "正在准备规划任务"
const ACTIVITY_LIMIT = 5

function formatDuration(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000))
  const minutes = Math.floor(total / 60)
  const seconds = total % 60
  return `${minutes}:${String(seconds).padStart(2, "0")}`
}

/**
 * 估算是外推出来的，真实规划可能更久。走完预算却还没结束时说“即将完成”，
 * 而不是把 0:00 一直挂着——那会让人以为卡死了。
 */
function formatRemaining(ms: number | null): string {
  if (ms === null) return "估算中"
  return ms < 5_000 ? "即将完成" : formatDuration(ms)
}

/**
 * 已用时长本地推进，只在收到新快照时对齐服务端值。
 * 轮询间隔是 1.5s，若直接显示快照里的 elapsedMs，计时会一跳一顿。
 */
function useSmoothElapsed(snapshot: PlanningProgressSnapshot | null): number {
  const [elapsed, setElapsed] = useState(0)
  const anchor = useRef<{ at: number; elapsedMs: number } | null>(null)

  useEffect(() => {
    if (snapshot) anchor.current = { at: Date.now(), elapsedMs: snapshot.elapsedMs }
  }, [snapshot])

  useEffect(() => {
    const timer = window.setInterval(() => {
      const current = anchor.current
      setElapsed(current ? current.elapsedMs + (Date.now() - current.at) : 0)
    }, 500)
    return () => window.clearInterval(timer)
  }, [])

  return snapshot?.done ? snapshot.elapsedMs : elapsed
}

export function PlanningProgress({
  snapshot,
}: {
  snapshot: PlanningProgressSnapshot | null
}) {
  const elapsedMs = useSmoothElapsed(snapshot)
  const percent = Math.min(100, Math.max(0, snapshot?.percent ?? 0))
  const activePhase = snapshot?.phase ?? "preparing"
  const activeIndex =
    activePhase === "completed"
      ? MILESTONES.length
      : Math.max(
          0,
          MILESTONES.findIndex((milestone) => milestone.phase === activePhase),
        )
  const remainingMs = snapshot
    ? Math.max(0, snapshot.estimatedTotalMs - elapsedMs)
    : null
  const activities = [...(snapshot?.recentActivities ?? [])]
    .reverse()
    .slice(0, ACTIVITY_LIMIT)

  return (
    <div className="planning-progress" role="status" aria-live="polite">
      <div
        className="planning-progress-dial"
        style={{ "--progress": `${percent}%` } as React.CSSProperties}
        aria-label={`完成进度 ${Math.round(percent)}%`}
      >
        <div className="planning-progress-readout">
          <strong>{Math.round(percent)}</strong>
          <span>%</span>
        </div>
      </div>

      <div className="planning-progress-copy">
        <h2>{snapshot?.stageLabel ?? FALLBACK_LABEL}</h2>
        <span>
          {snapshot?.detail ??
            "模型会依次声明范围、查询真实班次与路线，再编排并复核行程"}
        </span>
      </div>

      <dl className="planning-progress-stats">
        <div className="is-primary">
          <dt>已用时间</dt>
          <dd>{formatDuration(elapsedMs)}</dd>
        </div>
        <div className="is-primary">
          <dt>预计剩余</dt>
          <dd>{formatRemaining(remainingMs)}</dd>
        </div>
        <div>
          <dt>外部查询</dt>
          <dd>{snapshot?.toolCallCount ?? 0} 次</dd>
        </div>
        <div>
          <dt>已核实事实</dt>
          <dd>{snapshot?.factCount ?? 0} 条</dd>
        </div>
      </dl>

      <ol className="planning-progress-steps" aria-label="规划阶段">
        {MILESTONES.map((milestone, index) => {
          const Icon = milestone.icon
          const state =
            index < activeIndex ? "done" : index === activeIndex ? "active" : "pending"
          const isResearch = milestone.phase === "researching"
          const rounds =
            isResearch && snapshot && snapshot.researchRound > 0
              ? `第 ${snapshot.researchRound}/${Math.max(
                  snapshot.targetRounds,
                  snapshot.researchRound,
                )} 轮`
              : null
          return (
            <li key={milestone.phase} className={`is-${state}`}>
              <span>{state === "done" ? <Check /> : <Icon />}</span>
              <p>{milestone.label}</p>
              {rounds ? <em>{rounds}</em> : null}
            </li>
          )
        })}
      </ol>

      {activities.length > 0 ? (
        <div className="planning-progress-activities">
          <p className="planning-progress-activities-title">实时进程</p>
          <ul>
            {activities.map((activity, index) => (
              <li key={`${activity}-${index}`} style={{ opacity: 1 - index * 0.16 }}>
                <i aria-hidden />
                {activity}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  )
}
