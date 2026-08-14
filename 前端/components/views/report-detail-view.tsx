"use client"

import { useState, type CSSProperties, type ReactNode } from "react"
import {
  ChevronLeft,
  Compass,
  Download,
  FileChartColumn,
  MapPin,
  Music2,
  Navigation,
  Sparkles,
  Tags,
} from "lucide-react"
import { useSearchParams } from "next/navigation"
import { useApp } from "@/components/shared/app-context"
import { EmptyState } from "@/components/shared/empty-state"
import { THEME_COLORS } from "@/components/shared/persona-planet"
import { RadarChart } from "@/components/shared/radar-chart"
import { saveTravelImage } from "@/lib/postcard-save"
import type { Report, ReportChartPoint, TravelProfileData, VisualTheme } from "@/types"

type ThemeStyle = CSSProperties & Record<`--${string}`, string | number>

const LEGACY_THEME_BY_DIMENSION: Record<ReportChartPoint["dimension"], VisualTheme> = {
  自然探索: "forest_light",
  人文体验: "museum_gold",
  美食偏好: "sunset_orange",
  慢节奏: "ocean_blue",
  社交意愿: "city_neon",
}

const MOMENT_LABELS = ["01", "02", "03"]

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
  const { reports, goBack, toast } = useApp()
  const report = reports.find((item) => item.id === searchParams.get("reportId"))
  const [exporting, setExporting] = useState(false)

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

  const currentReport = report
  const profile = currentReport.profileData
  const theme = profileTheme(profile, currentReport)
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
  const exportName = `${profile?.archetypeName ?? currentReport.personalitySummary}-旅行人格报告.png`

  async function handleExportImage() {
    if (!report || exporting) return
    setExporting(true)
    try {
      await exportReportAsImage(report, exportName)
      toast("报告图片已保存", "success")
    } catch (error) {
      toast(resolveExportMessage(error), "error")
    } finally {
      setExporting(false)
    }
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

      <button
        type="button"
        onClick={() => void handleExportImage()}
        disabled={exporting}
        className="ui-pressable persona-export-button"
      >
        <Download className="size-4" aria-hidden />
        {exporting ? "保存中" : "保存图片"}
      </button>

      <div className="persona-export-sheet">
        <header className="persona-editorial-header">
          <div className="persona-editorial-motes" aria-hidden>
            <span />
            <span />
            <span />
          </div>
          <p className="persona-editorial-kicker">TRAVEL PERSONA · 旅行人格</p>
          <div className="persona-editorial-meta">
            <span>
              <MapPin aria-hidden />
              {currentReport.location}
            </span>
            <i aria-hidden />
            <span>{currentReport.dateLabel}</span>
          </div>
          <h1>{profile?.archetypeName ?? currentReport.personalitySummary}</h1>
          <p className="persona-editorial-subtitle">
            {profile?.souvenirLine || profile?.slogan || "把旅途里的选择，折成一份可带走的人格地图。"}
          </p>
          {profile?.summary ? <p className="persona-editorial-summary">{profile.summary}</p> : null}
          <div className="persona-editorial-route" aria-hidden>
            <span />
            <b>✦</b>
            <span />
          </div>
        </header>

        <main
          className={`persona-report-content persona-editorial-content ${profile ? "has-profile" : "is-legacy"}`}
        >
          <section className="persona-section persona-radar-section persona-reveal">
            <SectionHeading
              index="01"
              title="旅行能量图"
              icon={<Sparkles className="size-4" aria-hidden />}
            />
            <div className="persona-radar-layout">
              <div className="persona-radar">
                <RadarChart data={currentReport.chartData} size={220} />
              </div>
              <div className="persona-radar-notes">
                {topDimensions(currentReport.chartData).map((item, index) => (
                  <article key={item.dimension} className="persona-note-card">
                    <span>{String(index + 1).padStart(2, "0")}</span>
                    <strong>{item.dimension}</strong>
                    <p>{dimensionSummary(item.dimension, item.value)}</p>
                  </article>
                ))}
              </div>
            </div>
          </section>

          {profile ? (
            <>
              <ProfileOverview report={currentReport} profile={profile} />
              <ProfileMoments content={currentReport.content} profile={profile} />
              <ProfileGuidance profile={profile} />
              <section className="persona-section persona-spectrum-section persona-reveal">
                <SectionHeading index="06" title="旅行光谱" icon={<Compass className="size-4" aria-hidden />} />
                <div className="persona-spectrum-grid">
                  {profile.spectrums.map((spectrum) => (
                    <article
                      key={spectrum.id}
                      className="persona-spectrum-card"
                      style={{ "--spectrum-value": spectrum.value } as ThemeStyle}
                    >
                      <div className="persona-spectrum-card-head">
                        <strong>{spectrum.leftLabel}</strong>
                        <strong className="is-right">{spectrum.rightLabel}</strong>
                      </div>
                      <div className="persona-spectrum-track" aria-hidden>
                        <span className="persona-spectrum-fill" />
                        <span className="persona-spectrum-knob" />
                        <span className="persona-spectrum-value">{spectrum.value}</span>
                      </div>
                    </article>
                  ))}
                </div>
              </section>
            </>
          ) : (
            <section
              className={`persona-legacy-copy persona-reveal ${contentDensity(currentReport.content)}`}
              aria-label="旅行人格报告正文"
            >
              <ReportContent content={currentReport.content} title={currentReport.personalitySummary} />
            </section>
          )}
        </main>
      </div>
    </div>
  )
}

function ProfileOverview({
  report,
  profile,
}: {
  report: Report
  profile: TravelProfileData
}) {
  const narrative = parseProfileNarrative(report.content, profile)
  const summary =
    profile.summary ||
    summarizeText(narrative.intro || profile.slogan, 88) ||
    "这是一种有自己取景方式、也有自己出发节奏的旅行人格。"
  return (
    <section className="persona-section persona-overview-section persona-reveal">
      <SectionHeading index="02" title="人格速写" icon={<Tags className="size-4" aria-hidden />} />
      <div className="persona-overview">
        <div className="persona-overview-main">
          <p className="persona-overview-lead">{summary}</p>
          <div className="persona-overview-code">
            <span>{profile.personaCode}</span>
            <strong>{profile.slogan}</strong>
          </div>
        </div>
        <div className="persona-overview-side">
          <div className="persona-keywords persona-keywords-wide">
            {profile.keywords.map((keyword, index) => (
              <span key={keyword} style={{ animationDelay: `${index * 90}ms` }}>
                {keyword}
              </span>
            ))}
          </div>
          <p className="persona-overview-foot">
            {profile.souvenirLine || narrative.verdict || "把旅途里的偏好，整理成更清楚的出发方式。"}
          </p>
        </div>
      </div>
    </section>
  )
}

function ProfileMoments({
  content,
  profile,
}: {
  content: string
  profile: TravelProfileData
}) {
  const narrative = parseProfileNarrative(content, profile)
  return (
    <section className="persona-section persona-moments-section persona-reveal">
      <SectionHeading index="03" title="旅行瞬间" icon={<Sparkles className="size-4" aria-hidden />} />
      <div className="persona-moment-list">
        {narrative.moments.map((moment, index) => (
          <article key={`${moment.title}-${index}`} className="persona-moment-card">
            <span className="persona-moment-index">{MOMENT_LABELS[index] ?? "03"}</span>
            <h3>{moment.title}</h3>
            <p>{moment.content}</p>
          </article>
        ))}
      </div>
      <div className="persona-verdict-card">
        <span>人格判词</span>
        <p>{narrative.verdict}</p>
      </div>
    </section>
  )
}

function ProfileGuidance({ profile }: { profile: TravelProfileData }) {
  const music = profile.musicRecommendation ?? fallbackMusic(profile)
  const prescription =
    profile.travelPrescription ||
    profile.nextTripInspiration ||
    "下一次，选择一个能慢慢停留的片区，把时间留给真正吸引你的细节。"
  return (
    <section className="persona-section persona-guidance-section persona-reveal">
      <div className="persona-guidance-grid">
        <article className="persona-extra-card persona-music-card">
          <SectionHeading index="04" title="人格声轨" icon={<Music2 className="size-4" aria-hidden />} />
          <p className="persona-music-title">{music.title}</p>
          <p className="persona-music-mood">{music.mood}</p>
          <p className="persona-extra-copy">{music.reason}</p>
        </article>
        <article className="persona-extra-card">
          <SectionHeading index="05" title="下一站处方" icon={<Navigation className="size-4" aria-hidden />} />
          <p className="persona-prescription">{prescription}</p>
        </article>
      </div>
    </section>
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

function fallbackMusic(profile: TravelProfileData) {
  if (profile.visualTheme === "night_purple") {
    return { title: "Midnight City - M83", mood: "夜行", reason: "适合把城市灯光和独处步速连成一条线。" }
  }
  if (profile.visualTheme === "museum_gold") {
    return {
      title: "Experience - Ludovico Einaudi",
      mood: "沉静",
      reason: "适合在展馆、老街和长镜头式步行里慢慢铺开。",
    }
  }
  if (profile.visualTheme === "sunset_orange") {
    return {
      title: "A Sunday Kind of Love - Etta James",
      mood: "温热",
      reason: "适合把小店、市集和傍晚街角听成一段生活。",
    }
  }
  if (profile.visualTheme === "ocean_blue") {
    return {
      title: "An Ending, a Beginning - Dustin O'Halloran",
      mood: "舒展",
      reason: "适合海风、空镜和不急着抵达的路上。",
    }
  }
  return {
    title: "A Walk - Tycho",
    mood: "清透",
    reason: "适合自然光、慢步行和把风景收进照片的时刻。",
  }
}

async function exportReportAsImage(report: Report, filename: string) {
  const canvas = await renderReportCanvas(report)
  const dataUrl = canvas.toDataURL("image/png")
  await saveTravelImage(dataUrl, filename.replace(/\.png$/i, ""), "旅行人格报告")
}

type ReportSnapshot = {
  title: string
  subtitle: string
  location: string
  dateLabel: string
  profileCode?: string
  summary: string
  keywords: string[]
  strengths: string[]
  actionTips: string[]
  music?: string
  prescription?: string
  theme: VisualTheme
}

async function renderReportCanvas(report: Report) {
  const profile = report.profileData
  const theme = profileTheme(profile, report)
  const snapshot = buildReportSnapshot(report, theme)
  return renderSnapshotCanvas(snapshot)
}

function buildReportSnapshot(report: Report, theme: VisualTheme): ReportSnapshot {
  const profile = report.profileData
  if (!profile) {
    return {
      title: report.personalitySummary,
      subtitle: "把旅途里的选择，折成一份可带走的人格地图。",
      location: report.location,
      dateLabel: report.dateLabel,
      summary: summarizeText(report.content, 78),
      keywords: [],
      strengths: ["偏好会自然流露在旅途选择里。"],
      actionTips: ["下一次出发前，先留一段给真正想停下来的片刻。"],
      theme,
    }
  }
  const narrative = parseProfileNarrative(report.content, profile)
  const music = profile.musicRecommendation ?? fallbackMusic(profile)
  const prescription = profile.travelPrescription || profile.nextTripInspiration
  return {
    title: profile.archetypeName,
    subtitle: profile.souvenirLine || profile.slogan || "把旅途里的选择，折成一份可带走的人格地图。",
    location: report.location,
    dateLabel: report.dateLabel,
    profileCode: profile.personaCode,
    summary: profile.summary || summarizeText(narrative.intro || profile.slogan, 90),
    keywords: profile.keywords.slice(0, 4),
    strengths: nonEmptyList(profile.strengths, [profile.slogan]).slice(0, 3),
    actionTips: buildExportActionTips(profile, narrative, prescription),
    music: music.title,
    prescription: prescription || undefined,
    theme,
  }
}

async function renderSnapshotCanvas(snapshot: ReportSnapshot) {
  const ratio = Math.min(2, window.devicePixelRatio || 1)
  const width = 1080
  const height = 1480
  const colors = THEME_COLORS[snapshot.theme]
  const canvas = document.createElement("canvas")
  const context = canvas.getContext("2d")
  if (!context) throw new Error("无法创建图片")
  canvas.width = width * ratio
  canvas.height = height * ratio
  context.scale(ratio, ratio)

  drawPosterBackground(context, width, height, colors)
  drawPosterFrame(context, width, height, colors)
  drawPosterHeader(context, snapshot, width, colors)
  drawPosterSummary(context, snapshot, width, colors)
  drawPosterTags(context, snapshot, width, colors)
  drawPosterInsightColumns(context, snapshot, width, colors)
  drawPosterFooter(context, snapshot, width, height, colors)
  return canvas
}

function drawPosterBackground(
  context: CanvasRenderingContext2D,
  width: number,
  height: number,
  colors: (typeof THEME_COLORS)[VisualTheme],
) {
  const gradient = context.createLinearGradient(0, 0, width, height)
  gradient.addColorStop(0, colors.background)
  gradient.addColorStop(0.45, colors.paperAlt)
  gradient.addColorStop(1, colors.paper)
  context.fillStyle = gradient
  context.fillRect(0, 0, width, height)

  context.save()
  context.globalAlpha = 0.12
  context.fillStyle = colors.glow
  context.beginPath()
  context.arc(width - 130, 150, 210, 0, Math.PI * 2)
  context.fill()
  context.beginPath()
  context.arc(80, height - 180, 150, 0, Math.PI * 2)
  context.fill()
  context.restore()
}

function drawPosterFrame(
  context: CanvasRenderingContext2D,
  width: number,
  height: number,
  colors: (typeof THEME_COLORS)[VisualTheme],
) {
  context.fillStyle = "rgba(255,255,255,0.76)"
  roundRect(context, 40, 40, width - 80, height - 80, 36)
  context.fill()
  context.strokeStyle = `${colors.primary}42`
  context.lineWidth = 2
  context.stroke()
}

function drawPosterHeader(
  context: CanvasRenderingContext2D,
  snapshot: ReportSnapshot,
  width: number,
  colors: (typeof THEME_COLORS)[VisualTheme],
) {
  context.fillStyle = colors.muted
  context.font = "700 22px Georgia, serif"
  context.fillText("TRAVEL PERSONA", 98, 124)

  context.fillStyle = colors.ink
  context.font = '900 78px "PingFang SC", "Microsoft YaHei", sans-serif'
  context.fillText(snapshot.title, 94, 220)

  context.fillStyle = colors.primary
  context.font = '600 28px "PingFang SC", "Microsoft YaHei", sans-serif'
  wrapText(context, snapshot.subtitle, width - 220)
    .slice(0, 2)
    .forEach((line, index) => {
      context.fillText(line, 98, 274 + index * 36)
    })

  context.fillStyle = colors.muted
  context.font = '600 22px "PingFang SC", "Microsoft YaHei", sans-serif'
  context.fillText(snapshot.location || "旅行未命名", 98, 358)
  context.fillText(snapshot.dateLabel, width - 240, 358)

  if (snapshot.profileCode) {
    context.fillStyle = `${colors.primary}14`
    roundRect(context, 96, 390, 168, 46, 23)
    context.fill()
    context.fillStyle = colors.primary
    context.font = "700 20px Georgia, serif"
    context.fillText(snapshot.profileCode, 124, 420)
  }
}

function drawPosterSummary(
  context: CanvasRenderingContext2D,
  snapshot: ReportSnapshot,
  width: number,
  colors: (typeof THEME_COLORS)[VisualTheme],
) {
  context.fillStyle = "rgba(255,255,255,0.84)"
  roundRect(context, 84, 474, width - 168, 222, 30)
  context.fill()
  context.strokeStyle = `${colors.primary}28`
  context.lineWidth = 2
  context.stroke()

  context.fillStyle = colors.primary
  context.font = '700 24px "PingFang SC", "Microsoft YaHei", sans-serif'
  context.fillText("人格速写", 116, 524)

  context.fillStyle = colors.ink
  context.font = '500 30px "PingFang SC", "Microsoft YaHei", sans-serif'
  wrapText(context, snapshot.summary, width - 232)
    .slice(0, 3)
    .forEach((line, index) => {
      context.fillText(line, 116, 584 + index * 40)
    })
}

function drawPosterTags(
  context: CanvasRenderingContext2D,
  snapshot: ReportSnapshot,
  width: number,
  colors: (typeof THEME_COLORS)[VisualTheme],
) {
  if (!snapshot.keywords.length) return
  drawTagRow(context, snapshot.keywords, 96, 728, width - 192, colors)
}

function drawPosterInsightColumns(
  context: CanvasRenderingContext2D,
  snapshot: ReportSnapshot,
  width: number,
  colors: (typeof THEME_COLORS)[VisualTheme],
) {
  drawPosterListCard(context, {
    x: 84,
    y: 798,
    width: (width - 184) / 2,
    height: 252,
    title: "你的优势",
    items: snapshot.strengths,
    colors,
  })
  drawPosterListCard(context, {
    x: width / 2 + 8,
    y: 798,
    width: (width - 184) / 2,
    height: 252,
    title: "行动建议",
    items: snapshot.actionTips,
    colors,
  })
}

function drawPosterListCard(
  context: CanvasRenderingContext2D,
  options: {
    x: number
    y: number
    width: number
    height: number
    title: string
    items: string[]
    colors: (typeof THEME_COLORS)[VisualTheme]
  },
) {
  const { x, y, width, height, title, items, colors } = options
  context.fillStyle = "rgba(255,255,255,0.82)"
  roundRect(context, x, y, width, height, 28)
  context.fill()
  context.strokeStyle = `${colors.primary}26`
  context.lineWidth = 2
  context.stroke()

  context.fillStyle = colors.primary
  context.font = '700 22px "PingFang SC", "Microsoft YaHei", sans-serif'
  context.fillText(title, x + 28, y + 46)

  context.fillStyle = colors.ink
  context.font = '500 23px "PingFang SC", "Microsoft YaHei", sans-serif'
  items.slice(0, 3).forEach((item, index) => {
    const top = y + 92 + index * 52
    context.fillStyle = `${colors.primary}18`
    roundRect(context, x + 28, top - 22, 26, 26, 13)
    context.fill()
    context.fillStyle = colors.primary
    context.font = "700 16px Georgia, serif"
    context.fillText(String(index + 1), x + 37, top - 4)
    context.fillStyle = colors.ink
    context.font = '500 23px "PingFang SC", "Microsoft YaHei", sans-serif'
    wrapText(context, summarizeText(item, 24), width - 92)
      .slice(0, 2)
      .forEach((line, lineIndex) => {
        context.fillText(line, x + 68, top + lineIndex * 24)
      })
  })
}

function drawPosterFooter(
  context: CanvasRenderingContext2D,
  snapshot: ReportSnapshot,
  width: number,
  height: number,
  colors: (typeof THEME_COLORS)[VisualTheme],
) {
  context.fillStyle = `${colors.primary}10`
  roundRect(context, 84, 1090, width - 168, 244, 28)
  context.fill()
  context.strokeStyle = `${colors.primary}28`
  context.lineWidth = 2
  context.stroke()

  context.fillStyle = colors.primary
  context.font = '700 22px "PingFang SC", "Microsoft YaHei", sans-serif'
  context.fillText("声轨与下一站", 116, 1140)

  if (snapshot.music) {
    context.fillStyle = colors.muted
    context.font = '600 20px "PingFang SC", "Microsoft YaHei", sans-serif'
    context.fillText("推荐配乐", 116, 1194)
    context.fillStyle = colors.ink
    context.font = "700 24px Georgia, serif"
    context.fillText(snapshot.music, 230, 1194)
  }

  if (snapshot.prescription) {
    context.fillStyle = colors.muted
    context.font = '600 20px "PingFang SC", "Microsoft YaHei", sans-serif'
    context.fillText("下一站", 116, 1252)
    context.fillStyle = colors.ink
    context.font = '500 26px "PingFang SC", "Microsoft YaHei", sans-serif'
    wrapText(context, summarizeText(snapshot.prescription, 42), width - 300)
      .slice(0, 2)
      .forEach((line, index) => {
        context.fillText(line, 206, 1252 + index * 34)
      })
  }

  context.save()
  context.fillStyle = colors.muted
  context.font = "500 18px Georgia, serif"
  context.fillText("Travel Planet", width - 214, height - 86)
  context.restore()
}

function drawTagRow(
  context: CanvasRenderingContext2D,
  tags: string[],
  x: number,
  y: number,
  maxWidth: number,
  colors: (typeof THEME_COLORS)[VisualTheme],
) {
  context.font = '500 18px "PingFang SC", "Microsoft YaHei", sans-serif'
  let cursorX = x
  let cursorY = y
  for (const tag of tags) {
    const tagWidth = context.measureText(tag).width + 28
    if (cursorX + tagWidth > x + maxWidth) {
      cursorX = x
      cursorY += 38
    }
    context.fillStyle = "rgba(255,255,255,0.95)"
    roundRect(context, cursorX, cursorY, tagWidth, 28, 14)
    context.fill()
    context.fillStyle = colors.primary
    context.fillText(tag, cursorX + 14, cursorY + 19)
    cursorX += tagWidth + 8
  }
}

function wrapText(context: CanvasRenderingContext2D, text: string, maxWidth: number) {
  if (!text) return []
  const paragraphs = text.split("\n").filter(Boolean)
  const lines: string[] = []
  for (const paragraph of paragraphs) {
    let current = ""
    for (const char of paragraph) {
      const next = current + char
      if (current && context.measureText(next).width > maxWidth) {
        lines.push(current)
        current = char
      } else {
        current = next
      }
    }
    if (current) lines.push(current)
  }
  return lines
}

function summarizeText(text: string | undefined, maxLength: number) {
  const normalized = text?.replace(/\s+/g, " ").trim() ?? ""
  if (!normalized) return ""
  if (normalized.length <= maxLength) return normalized
  return `${normalized.slice(0, Math.max(0, maxLength - 1))}…`
}

function roundRect(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number,
) {
  context.beginPath()
  context.moveTo(x + radius, y)
  context.lineTo(x + width - radius, y)
  context.quadraticCurveTo(x + width, y, x + width, y + radius)
  context.lineTo(x + width, y + height - radius)
  context.quadraticCurveTo(x + width, y + height, x + width - radius, y + height)
  context.lineTo(x + radius, y + height)
  context.quadraticCurveTo(x, y + height, x, y + height - radius)
  context.lineTo(x, y + radius)
  context.quadraticCurveTo(x, y, x + radius, y)
  context.closePath()
}

function resolveExportMessage(error: unknown) {
  if (error instanceof Error && error.message.trim()) return error.message
  return "保存图片失败，请稍后重试"
}

function contentDensity(content: string) {
  const length = content.replace(/\s/g, "").length
  if (length > 650) return "is-dense"
  if (length > 380) return "is-compact"
  return "is-comfortable"
}

function topDimensions(chartData: ReportChartPoint[]) {
  return [...chartData].sort((a, b) => b.value - a.value).slice(0, 3)
}

function dimensionSummary(dimension: ReportChartPoint["dimension"], value: number) {
  if (dimension === "自然探索") return value >= 65 ? "会被自然光线、空景和开阔环境牵引。" : "更喜欢带一点自然呼吸感的停留。"
  if (dimension === "人文体验") return value >= 65 ? "会主动走进展馆、街区和有故事的空间。" : "对地方气质和细节变化保持敏感。"
  if (dimension === "美食偏好") return value >= 65 ? "愿意通过味道认识一座城市的日常。" : "吃什么不是主角，但会记住恰好的那一顿。"
  if (dimension === "慢节奏") return value >= 65 ? "更适合留白充足、允许停下来的旅程。" : "节奏有弹性，不喜欢被流程完全框住。"
  return value >= 65 ? "只有在真正合拍的人和场景里才愿意靠近。" : "相处不吵闹，更偏向安静地共享感受。"
}

function nonEmptyList(primary: string[] | undefined, fallback: string[]) {
  const normalized = (primary ?? []).map((item) => item.trim()).filter(Boolean)
  if (normalized.length) return normalized
  return fallback.map((item) => item.trim()).filter(Boolean)
}

function buildExportActionTips(
  profile: TravelProfileData,
  narrative: ReturnType<typeof parseProfileNarrative>,
  prescription?: string | null,
) {
  const base = nonEmptyList(profile.actionTips, [])
  const filtered = base.filter((item) => !looksLikePrescription(item, prescription))
  const fallback = [
    "先给自己留一个最想停留的点，再安排其他部分。",
    profile.watchouts?.[0] ? `留意：${profile.watchouts[0].replace(/[。！？；;,.、]+$/g, "")}` : "",
    profile.strengths?.[0] ? `优势可以用在：${profile.strengths[0].replace(/[。！？；;,.、]+$/g, "")}` : "",
    narrative.moments[0] ? `先从「${narrative.moments[0].title}」这种场景开始。` : "",
  ].filter(Boolean)
  return uniqueMeaningfulItems(filtered, fallback, prescription).slice(0, 3)
}

function uniqueMeaningfulItems(
  primary: string[] | undefined,
  fallback: string[],
  disallow?: string | null,
) {
  const banned = normalizeMeaningfulText(disallow)
  const seen = new Set<string>()
  const output: string[] = []
  for (const item of [...(primary ?? []), ...fallback]) {
    const text = item.trim()
    if (!text) continue
    const normalized = normalizeMeaningfulText(text)
    if (!normalized || normalized === banned || seen.has(normalized)) continue
    seen.add(normalized)
    output.push(text)
  }
  return output
}

function normalizeMeaningfulText(value: string | null | undefined) {
  return (value ?? "").replace(/\s+/g, "").replace(/[。！？；;,.、]/g, "").trim()
}

function looksLikePrescription(text: string, prescription?: string | null) {
  const normalized = normalizeMeaningfulText(text)
  const banned = normalizeMeaningfulText(prescription)
  if (!normalized) return true
  if (banned && normalized === banned) return true
  return /^(下一次|下次|下一站|建议|去|选择|把时间|留给|先把|留意)/.test(normalized)
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
              ]
                .filter(Boolean)
                .join(" ")
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
  icon?: ReactNode
}) {
  return (
    <div className="persona-section-heading">
      <span>{index}</span>
      <h2>{title}</h2>
      {icon}
    </div>
  )
}
