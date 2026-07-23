"use client"

import type { ReactNode } from "react"
import { useApp } from "@/components/shared/app-context"
import { PlanetLoader } from "@/components/shared/planet-loader"
import { ToastHost } from "@/components/shared/toast-host"

export function AppFrame({ children }: { children: ReactNode }) {
  const { initLoading } = useApp()

  return (
    <>
      {initLoading ? (
        <div className="app-loading-overlay absolute inset-0 z-50 flex items-center justify-center bg-background/85 backdrop-blur-sm">
          <PlanetLoader label="正在唤醒你的旅行星球…" />
        </div>
      ) : null}
      {children}
      <ToastHost />
    </>
  )
}
