import { Suspense } from "react"
import { ReportDetailView } from "@/components/views/report-detail-view"

export default function Page() {
  return (
    <Suspense fallback={null}>
      <ReportDetailView />
    </Suspense>
  )
}
