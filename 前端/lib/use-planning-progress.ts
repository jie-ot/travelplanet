"use client"

/**
 * 轮询 GET /ai/planning/progress，把后端真实阶段喂给待机动画。
 *
 * 规划本身仍是一次阻塞 POST（后端不提供 SSE/WebSocket），所以进度只能轮询。
 * 轮询是纯读、可失败的旁路：拿不到快照时动画退回“准备中”，绝不影响出图。
 */
import { useEffect, useRef, useState } from "react"
import { getPlanningProgress } from "@/lib/api"
import type { PlanningProgressSnapshot } from "@/types"

const POLL_INTERVAL_MS = 1500

export function usePlanningProgress(
  token: string | null,
  active: boolean,
): PlanningProgressSnapshot | null {
  // 快照与令牌一同保存：换一次规划就自然失效，无需在 effect 里清空状态。
  const [tracked, setTracked] = useState<{
    token: string
    data: PlanningProgressSnapshot
  } | null>(null)
  // 只保留“最新一次”结果：慢响应不得覆盖更新的快照，否则进度条会回退。
  const latestRequest = useRef(0)

  useEffect(() => {
    if (!token || !active) return
    let cancelled = false
    let timer: number | undefined

    const poll = async () => {
      const requestId = ++latestRequest.current
      try {
        const next = await getPlanningProgress(token)
        if (cancelled || requestId !== latestRequest.current || !next) return
        setTracked({ token, data: next })
      } catch {
        // 轮询失败保留上一帧：后端仍在生成，动画不该因为一次抖动而重置。
      } finally {
        if (!cancelled) timer = window.setTimeout(() => void poll(), POLL_INTERVAL_MS)
      }
    }

    void poll()
    return () => {
      cancelled = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [active, token])

  return tracked && tracked.token === token ? tracked.data : null
}
