"use client"

import { ChevronLeft, FileChartColumn } from "lucide-react"
import { useSearchParams } from "next/navigation"
import { useApp } from "@/components/shared/app-context"
import { EmptyState } from "@/components/shared/empty-state"
import { RadarChart } from "@/components/shared/radar-chart"
import { PLACEHOLDER_IMAGE, handleImageError, resolveAssetUrl } from "@/lib/asset"

export function ReportDetailView() {
  const searchParams = useSearchParams()
  const { reports, goBack } = useApp()
  const report = reports.find((r) => r.id === searchParams.get("reportId"))

  if (!report) {
    return (
      <div className="flex flex-1 flex-col">
        <EmptyState
          icon={<FileChartColumn className="size-7" aria-hidden />}
          title="内容已被删除"
          description="该报告不存在或已被移除。"
        >
          <button
            type="button"
            onClick={goBack}
            className="ui-pressable min-h-12 rounded-full bg-primary px-6 py-2.5 text-sm font-semibold text-primary-foreground shadow-md"
          >
            返回
          </button>
        </EmptyState>
      </div>
    )
  }

  return (
    <div className="flex flex-1 flex-col overflow-y-auto no-scrollbar pb-10">
      {/* 封面 hero */}
      <div className="relative h-56 w-full shrink-0">
        <img
          src={resolveAssetUrl(report.coverImage) || PLACEHOLDER_IMAGE}
          alt={`${report.location} 报告封面`}
          className="size-full object-cover"
          onError={handleImageError}
        />
        <div className="absolute inset-0 bg-gradient-to-t from-background via-background/20 to-[#062d48]/30" />
        <button
          type="button"
          onClick={goBack}
          className="ui-icon-button absolute left-4 top-[max(0.75rem,env(safe-area-inset-top))] flex size-11 items-center justify-center rounded-full bg-card/88 text-foreground shadow-md ring-1 ring-white/55 backdrop-blur-xl"
          aria-label="返回"
        >
          <ChevronLeft className="size-5" aria-hidden />
        </button>
        <div className="absolute inset-x-0 bottom-0 px-5 pb-4">
          <p className="font-editorial text-xs font-medium text-foreground/70">
            {report.location} · {report.dateLabel}
          </p>
          <h1 className="mt-1 font-display text-2xl font-black tracking-tight text-foreground text-balance">{report.personalitySummary}</h1>
        </div>
      </div>

      {/* 雷达图 */}
      <section className="px-4 pt-5 min-[400px]:px-5 min-[400px]:pt-6">
        <div className="rounded-[1.25rem] bg-card/96 p-4 shadow-[var(--shadow-surface)] ring-1 ring-[#0a3850]/10">
          <h2 className="mb-1 font-display text-sm font-bold text-foreground">旅行维度画像</h2>
          <p className="text-xs text-muted-foreground">五个维度反映你在旅途中的倾向</p>
          <div className="mt-2 flex justify-center">
            <RadarChart data={report.chartData} />
          </div>
        </div>
      </section>

      {/* 报告正文 */}
      <section className="px-4 pt-4 min-[400px]:px-5 min-[400px]:pt-5">
        <div className="rounded-[1.25rem] bg-card/96 p-5 shadow-[var(--shadow-surface)] ring-1 ring-[#0a3850]/10">
          <ReportContent content={report.content} />
        </div>
      </section>
    </div>
  )
}

// 匹配行首 Markdown 标题（# ~ ######），捕获 # 个数与标题文本
const HEADING_RE = /^\s*(#{1,6})\s+(.*\S)\s*$/
// 匹配行首无序列表项（- / * 开头）
const BULLET_RE = /^\s*[-*]\s+/

/** 渲染行内 Markdown：仅处理 **强调**，其余原样输出（不引入 Markdown 库，保持轻量）。 */
function renderInline(text: string) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) => {
    const bold = part.match(/^\*\*([^*]+)\*\*$/)
    return bold ? (
      <strong key={i} className="font-semibold text-foreground">
        {bold[1]}
      </strong>
    ) : (
      part
    )
  })
}

/**
 * 将报告 content（Markdown 纯文本：# 标题 / - 列表 / **强调**）渲染为有层级的排版。
 * 兼容旧的纯文本格式（无 # 标记时，首块首行仍作为标题展示）。
 */
function ReportContent({ content }: { content: string }) {
  const blocks = content.split("\n\n").filter((b) => b.trim())
  return (
    <div className="flex flex-col gap-3.5">
      {blocks.map((block, bi) => {
        const lines = block.split("\n").filter((l) => l.trim())
        const bullets = lines.filter((l) => BULLET_RE.test(l))
        const normals = lines.filter((l) => !BULLET_RE.test(l))
        return (
          <div key={bi} className="flex flex-col gap-1.5">
            {normals.map((line, i) => {
              const heading = line.match(HEADING_RE)
              if (heading) {
                return (
                  <p key={i} className="pt-1 font-display text-base font-bold text-foreground text-balance">
                    {renderInline(heading[2])}
                  </p>
                )
              }
              const trimmed = line.trim()
              if (bi === 0 && i === 0) {
                return (
                  <p key={i} className="font-display text-base font-bold text-foreground text-balance">
                    {renderInline(trimmed)}
                  </p>
                )
              }
              if (trimmed.endsWith("：")) {
                return (
                  <p key={i} className="pt-1 text-sm font-semibold text-foreground">
                    {renderInline(trimmed)}
                  </p>
                )
              }
              return (
                <p key={i} className="text-sm leading-relaxed text-muted-foreground text-pretty">
                  {renderInline(trimmed)}
                </p>
              )
            })}
            {bullets.length > 0 && (
              <div className="mt-1 flex flex-wrap gap-2">
                {bullets.map((b, i) => (
                  <span
                    key={i}
                    className="rounded-full bg-secondary px-3 py-1.5 text-xs font-medium text-secondary-foreground"
                  >
                    {renderInline(b.replace(BULLET_RE, ""))}
                  </span>
                ))}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
