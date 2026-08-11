"use client"

import { useEffect, useState, type CSSProperties } from "react"
import {
  ChevronLeft,
  FileChartColumn,
  MapPin,
  Sparkles,
} from "lucide-react"
import { useSearchParams } from "next/navigation"
import { useApp } from "@/components/shared/app-context"
import { EmptyState } from "@/components/shared/empty-state"
import { THEME_COLORS } from "@/components/shared/persona-planet"
import { RadarChart } from "@/components/shared/radar-chart"
import type {
  Report,
  ReportChartPoint,
  TravelProfileData,
  VisualTheme,
} from "@/types"

type ThemeStyle = CSSProperties & Record<`--${string}`, string | number>

const LEGACY_THEME_BY_DIMENSION: Record<ReportChartPoint["dimension"], VisualTheme> = {
  自然探索: "forest_light",
  人文体验: "museum_gold",
  美食偏好: "sunset_orange",
  慢节奏: "ocean_blue",
  社交意愿: "city_neon",
}

export const ARCHETYPE_THEME_MAP: Record<string, VisualTheme> = {
  文博深潜者: "museum_gold",
  山野追光者: "forest_light",
  风景猎人: "forest_light",
  巷陌寻味家: "sunset_orange",
  在地生活家: "sunset_orange",
  城市漫游者: "city_neon",
  海岛放空者: "ocean_blue",
  夜色收藏家: "night_purple",
  路线掌控者: "snow_silver",
  即兴漂流者: "desert_amber",
}

function legacyTheme(report: Report): VisualTheme {
  let highest = report.chartData[0]
  for (const item of report.chartData) {
    if (!highest || item.value > highest.value) highest = item
  }
  return highest ? LEGACY_THEME_BY_DIMENSION[highest.dimension] : "ocean_blue"
}

function profileTheme(profile: TravelProfileData | null | undefined, report: Report): VisualTheme {
  if (!profile) return legacyTheme(report)
  return ARCHETYPE_THEME_MAP[profile.archetypeName] ?? profile.visualTheme
}

export function ReportDetailView() {
  const searchParams = useSearchParams()
  const { reports, goBack } = useApp()
  const report = reports.find((item) => item.id === searchParams.get("reportId"))

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

  const profile = report.profileData
  const theme = profileTheme(profile, report)
  const colors = THEME_COLORS[theme]
  const spectrumValue = (id: TravelProfileData["spectrums"][number]["id"]) =>
    profile?.spectrums.find((item) => item.id === id)?.value ?? 50
  const themeStyle: ThemeStyle = {
    "--profile-accent": colors.primary,
    "--profile-accent-2": colors.secondary,
    "--profile-glow": colors.glow,
    "--profile-bg": colors.background,
    "--profile-paper": colors.paper,
    "--profile-paper-alt": colors.paperAlt,
    "--profile-ink": colors.ink,
    "--profile-muted": colors.muted,
    "--profile-line": colors.line,
    "--profile-stamp": colors.stamp,
    "--profile-skew": `${((50 - spectrumValue("planning")) / 50) * 0.8}deg`,
    "--foreground": colors.ink,
    "--muted-foreground": colors.muted,
    "--border": colors.line,
    "--primary": colors.primary,
  }

  return (
    <div
      className="persona-report persona-editorial-report flex min-h-0 flex-1 flex-col"
      style={themeStyle}
      data-theme={theme}
    >
      <button
        type="button"
        onClick={goBack}
        className="ui-icon-button persona-back-button"
        aria-label="返回"
      >
        <ChevronLeft className="size-5" aria-hidden />
      </button>

      <header className="persona-editorial-header">
        <div className="persona-editorial-motes" aria-hidden>
          <span />
          <span />
          <span />
        </div>
        <p className="persona-editorial-kicker">TRAVEL PERSONA · 旅行人格</p>
        <div className="persona-editorial-meta">
          <span><MapPin aria-hidden />{report.location}</span>
          <i aria-hidden />
          <span>{report.dateLabel}</span>
        </div>
        <h1>{profile?.archetypeName ?? report.personalitySummary}</h1>
        <div className="persona-editorial-route" aria-hidden>
          <span />
          <b>✦</b>
          <span />
        </div>
      </header>

      <main className={`persona-report-content persona-editorial-content ${profile ? "has-profile" : "is-legacy"}`}>
        <section className="persona-section persona-radar-section persona-reveal">
          <SectionHeading
            index="01"
            title="旅行能量图"
            icon={<Sparkles className="size-4" aria-hidden />}
          />
          <div className="persona-radar">
            <RadarChart data={report.chartData} size={210} />
          </div>
        </section>

        {profile ? (
          <>
            <ProfileNarrative content={report.content} profile={profile} />

            <section className="persona-declaration persona-reveal" aria-label="旅行人格印记">
              <div className="persona-signature">
                <p className="persona-code">{profile.personaCode}</p>
                <p className="persona-slogan">{profile.slogan}</p>
              </div>
              <div className="persona-keywords">
                {profile.keywords.map((keyword, index) => (
                  <span key={keyword} style={{ animationDelay: `${index * 90}ms` }}>
                    {keyword}
                  </span>
                ))}
              </div>
            </section>

            <section className="persona-section persona-spectrum-section persona-reveal">
              <SectionHeading index="02" title="四条旅行光谱" />
              <div className="persona-spectrums">
                {profile.spectrums.map((spectrum) => (
                  <div
                    key={spectrum.id}
                    className="persona-spectrum"
                    style={{ "--spectrum-value": spectrum.value } as ThemeStyle}
                  >
                    <div className="persona-spectrum-labels">
                      <span>{spectrum.leftLabel}</span>
                      <strong>{spectrum.value}</strong>
                      <span>{spectrum.rightLabel}</span>
                    </div>
                    <div className="persona-energy-band">
                      <span className="persona-energy-fill" />
                      <span className="persona-energy-core" />
                    </div>
                  </div>
                ))}
              </div>
            </section>
          </>
        ) : (
          <section
            className={`persona-legacy-copy persona-reveal ${contentDensity(report.content)}`}
            aria-label="旅行人格报告正文"
          >
            <ReportContent content={report.content} title={report.personalitySummary} />
          </section>
        )}
      </main>
    </div>
  )
}

type NarrativeMoment = {
  title: string
  content: string
}

function parseProfileNarrative(content: string, profile: TravelProfileData) {
  const blocks = content
    .replace(/\r\n/g, "\n")
    .split(/\n+/)
    .map((block) => block.trim())
    .filter(Boolean)
  const momentPattern = /^瞬间[一二三]\s*[｜|]\s*([^：:]{1,12})[：:]\s*(.+)$/
  const parsedMoments = blocks
    .map((block) => block.match(momentPattern))
    .filter((match): match is RegExpMatchArray => Boolean(match))
    .map<NarrativeMoment>((match) => ({ title: match[1].trim(), content: match[2].trim() }))
  const moments = profile.modules.map((module, index) => parsedMoments[index] ?? module)
  const introBlocks = blocks.filter(
    (block) => !/^瞬间[一二三]\s*[｜|]/.test(block) && !/^人格判词\s*[｜|]/.test(block),
  )
  const verdictBlock = blocks.find((block) => /^人格判词\s*[｜|]/.test(block))

  return {
    intro: introBlocks.join(" ") || profile.slogan,
    moments,
    verdict:
      verdictBlock?.replace(/^人格判词\s*[｜|]\s*/, "") ||
      profile.nextTripInspiration ||
      profile.slogan,
  }
}

function ProfileNarrative({
  content,
  profile,
}: {
  content: string
  profile: TravelProfileData
}) {
  const narrative = parseProfileNarrative(content, profile)
  const [activeMoment, setActiveMoment] = useState(0)
  const [paused, setPaused] = useState(false)
  const moment = narrative.moments[activeMoment] ?? narrative.moments[0]

  useEffect(() => {
    if (paused || narrative.moments.length < 2) return
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)")
    if (reducedMotion.matches) return
    const timer = window.setInterval(
      () => setActiveMoment((current) => (current + 1) % narrative.moments.length),
      7200,
    )
    return () => window.clearInterval(timer)
  }, [narrative.moments.length, paused])

  return (
    <section
      className="persona-narrative persona-reveal"
      aria-label="旅行人格正文"
      onPointerEnter={() => setPaused(true)}
      onPointerLeave={() => setPaused(false)}
      onFocusCapture={() => setPaused(true)}
      onBlurCapture={() => setPaused(false)}
    >
      <p className="persona-narrative-intro">{narrative.intro}</p>
      <div className="persona-moment-heading">
        <span>旅行瞬间</span>
        <div role="tablist" aria-label="切换旅行瞬间">
          {narrative.moments.map((item, index) => (
            <button
              key={`${item.title}-${index}`}
              type="button"
              role="tab"
              aria-selected={activeMoment === index}
              aria-label={`旅行瞬间 ${index + 1}：${item.title}`}
              onClick={() => setActiveMoment(index)}
            >
              {String(index + 1).padStart(2, "0")}
            </button>
          ))}
        </div>
      </div>
      {moment ? (
        <article className="persona-moment" key={`${activeMoment}-${moment.title}`}>
          <h3>{moment.title}</h3>
          <p>{moment.content}</p>
        </article>
      ) : null}
      <p className="persona-verdict">
        <span>人格判词</span>
        {narrative.verdict}
      </p>
    </section>
  )
}

function contentDensity(content: string) {
  const length = content.replace(/\s/g, "").length
  if (length > 650) return "is-dense"
  if (length > 380) return "is-compact"
  return "is-comfortable"
}

const HEADING_RE = /^\s*(#{1,6})\s+(.*\S)\s*$/
const BULLET_RE = /^\s*[-*]\s+/

function renderInline(text: string) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, index) => {
    const bold = part.match(/^\*\*([^*]+)\*\*$/)
    return bold ? <strong key={index}>{bold[1]}</strong> : part
  })
}

function normalizeHeading(text: string) {
  return text.replace(/[「」【】《》\s]/g, "").toLowerCase()
}

function ReportContent({ content, title }: { content: string; title: string }) {
  const blocks = content.split("\n\n").filter((block) => block.trim())
  let bodyParagraphIndex = 0
  return (
    <div className="persona-legacy-content">
      {blocks.map((block, blockIndex) => {
        const lines = block.split("\n").filter((line) => line.trim())
        return (
          <div key={blockIndex}>
            {lines.map((line, lineIndex) => {
              const heading = line.match(HEADING_RE)
              const text = line.replace(BULLET_RE, "").trim()
              if (heading) {
                const headingText = heading[2].trim()
                const normalizedTitle = normalizeHeading(title)
                const normalizedHeading = normalizeHeading(headingText)
                if (normalizedHeading === normalizedTitle) return null
                const subtitle = normalizedHeading.startsWith(`${normalizedTitle}：`)
                  ? headingText.slice(headingText.indexOf("：") + 1).trim()
                  : headingText
                return subtitle ? <h3 key={lineIndex}>{renderInline(subtitle)}</h3> : null
              }
              const isKeywords = /^(?:👉|✨|✅)?\s*(?:你的)?(?:专属)?旅行?关键标签|^关键标签/.test(text)
              const paragraphClass = [
                BULLET_RE.test(line) ? "legacy-bullet" : "",
                isKeywords ? "legacy-keywords" : "",
                !isKeywords && bodyParagraphIndex++ === 0 ? "legacy-lead" : "",
              ].filter(Boolean).join(" ")
              return (
                <p key={lineIndex} className={paragraphClass || undefined}>
                  {renderInline(text)}
                </p>
              )
            })}
          </div>
        )
      })}
    </div>
  )
}

function SectionHeading({
  index,
  title,
  icon,
}: {
  index: string
  title: string
  icon?: React.ReactNode
}) {
  return (
    <div className="persona-section-heading">
      <span>{index}</span>
      <h2>{title}</h2>
      {icon}
    </div>
  )
}
