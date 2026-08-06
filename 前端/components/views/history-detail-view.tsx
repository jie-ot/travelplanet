"use client"

import { useSearchParams } from "next/navigation"
import { useApp } from "@/components/shared/app-context"
import { TopBar } from "@/components/shared/top-bar"
import { ItineraryDetail } from "@/components/shared/itinerary-detail"
import { EmptyState } from "@/components/shared/empty-state"
import { Compass } from "lucide-react"

export function HistoryDetailView() {
  const searchParams = useSearchParams()
  const { plans } = useApp()
  const plan = plans.find((p) => p.id === searchParams.get("planId"))

  if (!plan) {
    return (
      <div className="flex h-full min-h-0 flex-col">
        <TopBar title="规划详情" />
        <div className="min-h-0 flex-1 overflow-y-auto no-scrollbar">
          <EmptyState
            icon={<Compass className="size-7" aria-hidden />}
            title="规划不存在"
            description="该规划可能已被删除，返回历史规划查看其它行程。"
          />
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <TopBar title={plan.location} subtitle={plan.dateLabel} />
      <div className="min-h-0 flex-1 overflow-y-auto no-scrollbar">
        <ItineraryDetail data={plan.itineraryData} />
      </div>
    </div>
  )
}
