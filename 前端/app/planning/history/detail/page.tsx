import { Suspense } from "react"
import { HistoryDetailView } from "@/components/views/history-detail-view"

export default function Page() {
  return (
    <Suspense fallback={null}>
      <HistoryDetailView />
    </Suspense>
  )
}
