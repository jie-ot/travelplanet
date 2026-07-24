"use client"

import { useEffect, useState } from "react"
import {
  BedDouble,
  BrainCircuit,
  Check,
  MapPinned,
  Route,
  Sparkles,
  TrainFront,
} from "lucide-react"

const STAGES = [
  { label: "正在理解旅行需求", icon: BrainCircuit },
  { label: "正在查找往返交通", icon: TrainFront },
  { label: "正在搜索住宿区域", icon: BedDouble },
  { label: "正在探索景点与美食", icon: MapPinned },
  { label: "正在计算关键路线", icon: Route },
  { label: "正在整理每日行程", icon: Sparkles },
]

export function PlanningProgress() {
  const [activeIndex, setActiveIndex] = useState(0)

  useEffect(() => {
    const timer = window.setInterval(() => {
      setActiveIndex((current) => Math.min(current + 1, STAGES.length - 1))
    }, 9000)
    return () => window.clearInterval(timer)
  }, [])

  return (
    <div className="planning-progress">
      <div className="planning-progress-orbit" aria-hidden>
        <span />
        <span />
        <span />
      </div>
      <div className="planning-progress-copy">
        <p>AI 正在探索目的地</p>
        <h2>{STAGES[activeIndex].label}</h2>
        <span>模型正在逐步理解范围、查询事实并审阅路线</span>
      </div>
      <ol>
        {STAGES.map((stage, index) => {
          const Icon = stage.icon
          const state = index < activeIndex ? "done" : index === activeIndex ? "active" : "pending"
          return (
            <li key={stage.label} className={`is-${state}`}>
              <span>{state === "done" ? <Check /> : <Icon />}</span>
              <p>{stage.label}</p>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
