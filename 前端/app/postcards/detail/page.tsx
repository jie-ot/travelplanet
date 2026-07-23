import { Suspense } from "react"
import { PostcardCollectionView } from "@/components/views/postcard-collection-view"

export default function Page() {
  return (
    <Suspense fallback={null}>
      <PostcardCollectionView />
    </Suspense>
  )
}
