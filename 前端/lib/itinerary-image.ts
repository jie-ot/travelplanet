import type { DailyItinerary, ItineraryData, Schedule } from "@/types"
import { classifySchedule } from "@/lib/schedule-kind"

const WIDTH = 1080
const PADDING = 64
const CONTENT_WIDTH = WIDTH - PADDING * 2
const MAX_HEIGHT = 18_000
const BODY_FONT = '"Microsoft YaHei UI", "PingFang SC", "Noto Sans CJK SC", sans-serif'
const ART_FONT = '"STKaiti", "KaiTi", "FangSong", "Songti SC", serif'
const TRANSPORT_LABELS: Record<string, string> = {
  walking: "步行",
  transit: "公交地铁",
  driving: "打车",
  bicycling: "骑行",
}

const COLORS = {
  navy: "#073b56",
  teal: "#078fa6",
  coral: "#e87955",
  cream: "#f7f2e8",
  paper: "#fffaf2",
  ink: "#123f50",
  muted: "#627980",
  line: "#c8d8d7",
  aqua: "#8edfd9",
  paleTeal: "#e4f2ef",
  paleCoral: "#f9e8df",
  white: "#ffffff",
} as const

export type PosterEvent = {
  travelLine: string | null
  timeLabel: string
  place: string
}

export type CompactDay = {
  date: string
  title: string
  events: PosterEvent[]
}

export type CompactContent = {
  title: string
  subtitle: string
  attractions: string[]
  foods: string[]
  days: CompactDay[]
}

type HeaderMetrics = {
  height: number
  titleFontSize: number
  titleLineHeight: number
  titleLines: string[]
  subtitleFontSize: number
  subtitleLines: string[]
}

type EventBlockMetrics = {
  travelLines: string[]
  bodyLines: string[]
}

type DayRowMetrics = {
  height: number
  titleLines: string[]
  eventBlocks: EventBlockMetrics[]
}

export function getItineraryImageDiagnostics(data: ItineraryData) {
  const compact = buildItineraryPosterContent(data)
  const bodyCopy = [
    ...compact.attractions,
    ...compact.foods,
    ...compact.days.flatMap((day) => [
      day.title,
      ...day.events.flatMap((event) => [event.travelLine || "", event.timeLabel, event.place]),
    ]),
  ].join("")
  return {
    bodyCharacters: visibleLength(bodyCopy),
    dayCount: compact.days.length,
    scheduleCount: compact.days.reduce((total, day) => total + day.events.length, 0),
    attractionCount: compact.attractions.length,
    foodCount: compact.foods.length,
  }
}

export async function renderItineraryImage(data: ItineraryData): Promise<Blob> {
  await document.fonts?.ready

  const compact = buildItineraryPosterContent(data)
  const measureCanvas = document.createElement("canvas")
  const measure = measureCanvas.getContext("2d")
  if (!measure) throw new Error("当前浏览器无法生成行程长图")

  const font = (size: number, weight = 500) => `${weight} ${size}px ${BODY_FONT}`
  const artFont = (size: number, weight = 700) => `${weight} ${size}px ${ART_FONT}`
  const header = measureHeader(measure, compact, font, artFont)
  const overview = measureOverview(measure, compact, font)
  const scheduleCard = measureScheduleCard(measure, compact.days, font)
  const sectionTitleHeight = 66
  const footerHeight = 72
  const height = Math.ceil(
    PADDING + header.height + 22 + overview.height + 28 + sectionTitleHeight + scheduleCard.height + footerHeight,
  )
  if (height > MAX_HEIGHT) throw new Error("行程内容过长，暂时无法生成单张长图")

  const canvas = document.createElement("canvas")
  canvas.width = WIDTH
  canvas.height = height
  const context = canvas.getContext("2d")
  if (!context) throw new Error("当前浏览器无法生成行程长图")
  context.imageSmoothingEnabled = true
  context.imageSmoothingQuality = "high"
  context.fillStyle = COLORS.cream
  context.fillRect(0, 0, WIDTH, height)

  let y = PADDING
  drawHeader(context, compact, header, y, font, artFont)
  y += header.height + 22
  drawOverview(context, overview, y, font)
  y += overview.height + 28
  drawSectionTitle(context, y, artFont)
  y += sectionTitleHeight
  drawScheduleCard(context, compact.days, scheduleCard.rows, y, font)
  y += scheduleCard.height
  drawFooter(context, y + 28, font)

  return await new Promise<Blob>((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error("行程长图生成失败"))),
      "image/png",
    )
  })
}

export function buildItineraryPosterContent(data: ItineraryData): CompactContent {
  const destination = cleanText(data.trip_info.destination) || "旅行"
  const dayCount = data.itinerary.length
  const theme = cleanText(data.experience_summary?.tripTheme || "")
  const title = theme || `${destination}行程`
  const date = cleanText(data.trip_info.date_label)
  const subtitle = [date, dayCount && !/天/u.test(date) ? `${dayCount}天` : ""].filter(Boolean).join(" · ")

  const days = data.itinerary.map((day, dayIndex) => buildPosterDay(day, dayIndex))
  const attractions = unique(
    data.itinerary.flatMap((day) =>
      day.schedules
        .filter((schedule) => classifySchedule(schedule) === "attraction")
        .map((schedule) => cleanText(schedule.place_name || ""))
        .filter(Boolean),
    ),
  )
  const foods = collectFoods(data)

  return { title, subtitle, attractions, foods, days }
}

function buildPosterDay(day: DailyItinerary, dayIndex: number): CompactDay {
  return {
    date: cleanText(day.date),
    title: cleanDayTitle(day.title || `第 ${dayIndex + 1} 天`),
    events: day.schedules.map((schedule) => projectSchedule(schedule)),
  }
}

function cleanDayTitle(value: string): string {
  const withoutNotes = cleanText(value).replace(/[（(][^）)]*[）)]/gu, "").trim()
  return withoutNotes || "当日行程"
}

function projectSchedule(schedule: Schedule): PosterEvent {
  const place = schedulePlace(schedule)
  return {
    travelLine: travelLine(schedule, place),
    timeLabel: timeLabel(schedule),
    place,
  }
}

function timeLabel(schedule: Schedule): string {
  const start = cleanText(schedule.start_time || "")
  const end = cleanText(schedule.end_time || "")
  if (start && end) {
    const stay = classifySchedule(schedule) === "transport" ? null : stayLabel(start, end)
    return stay ? `${start}–${end}（停留 ${stay}）` : `${start}–${end}`
  }
  if (start) return start
  return cleanText(schedule.time_period) || "灵活"
}

function stayLabel(start: string, end: string): string | null {
  const startMinutes = parseClock(start)
  const endMinutes = parseClock(end)
  if (startMinutes == null || endMinutes == null || endMinutes <= startMinutes) return null
  return formatDuration(endMinutes - startMinutes)
}

function parseClock(value: string): number | null {
  const match = value.match(/^(\d{1,2}):(\d{2})$/u)
  if (!match) return null
  return Number(match[1]) * 60 + Number(match[2])
}

function formatDuration(minutes: number): string {
  if (minutes < 60) return `${minutes} 分钟`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  if (rest === 0) return `${hours} 小时`
  return `${hours} 小时 ${rest} 分`
}

function travelLine(schedule: Schedule, place: string): string | null {
  const minutes = schedule.travel_minutes
  if (!minutes || minutes <= 0) return null
  const modeLabel = TRANSPORT_LABELS[schedule.transport_mode || ""]
  const transportMatch = cleanText(schedule.transport || "").match(/(步行|地铁|公交|打车|出租车|网约车|骑行)/u)
  const verb =
    modeLabel ||
    (transportMatch
      ? transportMatch[1] === "出租车" || transportMatch[1] === "网约车"
        ? "打车"
        : transportMatch[1]
      : "")
  const destination = place || "下一地点"
  if (!verb) return `约 ${minutes} 分钟前往${destination}`
  return `${verb} ${minutes} 分钟前往${destination}`
}

function collectFoods(data: ItineraryData): string[] {
  const recommendations = (data.food_recommendations || []).map(cleanFoodName).filter(Boolean)
  const scheduled = data.itinerary.flatMap((day) =>
    day.schedules
      .filter((schedule) => classifySchedule(schedule) === "dining")
      .map((schedule) => cleanFoodName(schedule.place_name || ""))
      .filter(Boolean),
  )
  return unique([...recommendations, ...scheduled])
}

function cleanFoodName(value: string): string {
  return cleanText(value).replace(/^推荐/u, "").trim()
}

function schedulePlace(schedule: Schedule): string {
  const source = cleanText(schedule.place_name || schedule.activity)
  const kind = classifySchedule(schedule)
  const stripped = stripSensitiveDetail(source)
  if (kind === "hotel") {
    if (/退房/u.test(schedule.activity || "")) return stripped ? `退房 ${stripped}` : "办理退房"
    if (/入住/u.test(schedule.activity || "")) return stripped ? `入住 ${stripped}` : "办理入住"
  }
  return stripped
}

function stripSensitiveDetail(value: string): string {
  return cleanText(value)
    .replace(/\b(?:G|D|C|Z|T|K)\s?\d{1,5}\b/giu, "")
    .replace(/\b[A-Z]{2}\s?\d{3,4}\b/gu, "")
    .replace(/(?:¥|￥)\s?\d+(?:\.\d+)?/gu, "")
    .replace(/\d+(?:\.\d+)?\s?元/gu, "")
    .replace(/(?:商务座|一等座|二等座|硬座|软座|硬卧|软卧|经济舱|公务舱|头等舱)/gu, "")
    .replace(/(?:乘坐|搭乘)?\s*次(?:列车|航班)/gu, "")
    .replace(/(?:座位|舱位|检票口|登机口|预订号|订单号)[:：]?\s*[A-Za-z0-9-]+/giu, "")
    .replace(/[，,；;、]{2,}/gu, "、")
    .replace(/\s{2,}/gu, " ")
    .replace(/^[，,；;、\s]+|[，,；;、\s]+$/gu, "")
}

function measureHeader(
  context: CanvasRenderingContext2D,
  compact: CompactContent,
  font: (size: number, weight?: number) => string,
  artFont: (size: number, weight?: number) => string,
): HeaderMetrics {
  const maxWidth = CONTENT_WIDTH - 100
  let titleFontSize = 68
  let titleLines = wrapText(context, compact.title, maxWidth, artFont(titleFontSize, 700))
  while (titleLines.length > 1 && titleFontSize > 46) {
    titleFontSize -= 2
    titleLines = wrapText(context, compact.title, maxWidth, artFont(titleFontSize, 700))
  }
  const titleLineHeight = titleFontSize + 10
  let subtitleFontSize = 28
  let subtitleLines = wrapText(context, compact.subtitle, maxWidth, font(subtitleFontSize, 650))
  while (subtitleLines.length > 2 && subtitleFontSize > 20) {
    subtitleFontSize -= 1
    subtitleLines = wrapText(context, compact.subtitle, maxWidth, font(subtitleFontSize, 650))
  }
  const subtitleLineHeight = subtitleFontSize + 8
  const height = 40 + titleLines.length * titleLineHeight + 18 + subtitleLines.length * subtitleLineHeight + 48
  return { height, titleFontSize, titleLineHeight, titleLines, subtitleFontSize, subtitleLines }
}

function measureOverview(
  context: CanvasRenderingContext2D,
  compact: CompactContent,
  font: (size: number, weight?: number) => string,
) {
  const labelWidth = 88
  const textWidth = CONTENT_WIDTH - 56 - labelWidth
  const attractionText = compact.attractions.length ? compact.attractions.join(" · ") : "未记录具体景点"
  const attractionLines = wrapText(context, attractionText, textWidth, font(24, 650))
  const foodLines = compact.foods.length
    ? wrapText(context, compact.foods.join(" · "), textWidth, font(24, 650))
    : []
  const rows = 1 + (foodLines.length ? 1 : 0)
  const lineCount = attractionLines.length + foodLines.length
  return {
    height: 58 + lineCount * 34 + (rows - 1) * 18,
    attractionLines,
    foodLines,
  }
}

function measureScheduleCard(
  context: CanvasRenderingContext2D,
  days: CompactDay[],
  font: (size: number, weight?: number) => string,
) {
  const titleWidth = CONTENT_WIDTH - 140
  const eventWidth = CONTENT_WIDTH - 120
  const rows = days.map((day) => {
    const titleLines = wrapText(context, day.title, titleWidth, font(26, 800))
    const eventBlocks = day.events.map((event) => {
      const travelLines = event.travelLine
        ? wrapText(context, event.travelLine, eventWidth, font(22, 550))
        : []
      const bodyLines = wrapText(context, `${event.timeLabel}  ${event.place}`, eventWidth, font(24, 550))
      return { travelLines, bodyLines }
    })
    const eventsHeight = eventBlocks.reduce(
      (total, block) => total + block.travelLines.length * 30 + block.bodyLines.length * 34 + 10,
      0,
    )
    const height = Math.max(120, 70 + titleLines.length * 34 + eventsHeight + 18)
    return { height, titleLines, eventBlocks }
  })
  return {
    height: 28 + rows.reduce((total, row) => total + row.height, 0) + 28,
    rows,
  }
}

function drawHeader(
  context: CanvasRenderingContext2D,
  compact: CompactContent,
  metrics: HeaderMetrics,
  y: number,
  font: (size: number, weight?: number) => string,
  artFont: (size: number, weight?: number) => string,
) {
  fillRoundedRect(context, PADDING, y, CONTENT_WIDTH, metrics.height, 34, COLORS.paleTeal)
  fillRoundedRect(context, PADDING, y, 14, metrics.height, 7, COLORS.teal)

  const titleY = y + 34
  drawText(
    context,
    metrics.titleLines,
    PADDING + 48,
    titleY,
    artFont(metrics.titleFontSize, 700),
    COLORS.navy,
    metrics.titleLineHeight,
  )
  const subtitleY = titleY + metrics.titleLines.length * metrics.titleLineHeight + 12
  drawText(
    context,
    metrics.subtitleLines,
    PADDING + 50,
    subtitleY,
    font(metrics.subtitleFontSize, 650),
    COLORS.muted,
    metrics.subtitleFontSize + 8,
  )
  context.strokeStyle = COLORS.coral
  context.lineWidth = 5
  context.beginPath()
  context.moveTo(PADDING + 50, y + metrics.height - 29)
  context.lineTo(PADDING + 238, y + metrics.height - 29)
  context.stroke()
}

function drawOverview(
  context: CanvasRenderingContext2D,
  metrics: ReturnType<typeof measureOverview>,
  y: number,
  font: (size: number, weight?: number) => string,
) {
  fillRoundedRect(context, PADDING, y, CONTENT_WIDTH, metrics.height, 28, COLORS.paper)
  const labelX = PADDING + 28
  const textX = PADDING + 118
  let rowY = y + 27
  drawOverviewRow(context, "景点", metrics.attractionLines, labelX, textX, rowY, font, COLORS.teal)
  rowY += metrics.attractionLines.length * 34 + 18
  if (metrics.foodLines.length) {
    context.strokeStyle = COLORS.line
    context.lineWidth = 1.5
    context.beginPath()
    context.moveTo(textX, rowY - 9)
    context.lineTo(PADDING + CONTENT_WIDTH - 28, rowY - 9)
    context.stroke()
    drawOverviewRow(context, "美食", metrics.foodLines, labelX, textX, rowY, font, COLORS.coral)
  }
}

function drawOverviewRow(
  context: CanvasRenderingContext2D,
  label: string,
  lines: string[],
  labelX: number,
  textX: number,
  y: number,
  font: (size: number, weight?: number) => string,
  color: string,
) {
  const labelWidth = 70
  const labelHeight = 34
  fillRoundedRect(context, labelX, y, labelWidth, labelHeight, labelHeight / 2, color)
  drawCenteredText(context, label, labelX + labelWidth / 2, y + labelHeight / 2, font(19, 800), COLORS.white)
  drawText(context, lines, textX, y, font(24, 650), COLORS.ink, 34)
}

function drawSectionTitle(
  context: CanvasRenderingContext2D,
  y: number,
  artFont: (size: number, weight?: number) => string,
) {
  drawText(context, ["每日日程"], PADDING, y + 3, artFont(40, 700), COLORS.navy, 48)
  context.strokeStyle = COLORS.aqua
  context.lineWidth = 2
  context.beginPath()
  context.moveTo(PADDING + 205, y + 29)
  context.lineTo(WIDTH - PADDING, y + 29)
  context.stroke()
}

function drawScheduleCard(
  context: CanvasRenderingContext2D,
  days: CompactDay[],
  rows: DayRowMetrics[],
  y: number,
  font: (size: number, weight?: number) => string,
) {
  const cardHeight = 28 + rows.reduce((total, row) => total + row.height, 0) + 28
  fillRoundedRect(context, PADDING, y, CONTENT_WIDTH, cardHeight, 28, COLORS.paper)
  let rowY = y + 28
  days.forEach((day, index) => {
    const row = rows[index]
    const accent = index % 2 === 0 ? COLORS.teal : COLORS.coral
    const pale = index % 2 === 0 ? COLORS.paleTeal : COLORS.paleCoral
    fillRoundedRect(context, PADDING + 20, rowY + 8, 6, row.height - 16, 3, accent)
    fillRoundedRect(context, PADDING + 46, rowY + 14, 54, 32, 16, pale)
    drawCenteredText(context, `D${index + 1}`, PADDING + 73, rowY + 30, font(18, 850), accent)
    drawText(context, [day.date], PADDING + 116, rowY + 16, font(20, 650), COLORS.muted, 28)
    drawText(context, row.titleLines, PADDING + 46, rowY + 56, font(26, 800), COLORS.navy, 34)

    let eventY = rowY + 56 + row.titleLines.length * 34 + 8
    row.eventBlocks.forEach((block) => {
      if (block.travelLines.length) {
        drawText(context, block.travelLines, PADDING + 46, eventY, font(22, 550), COLORS.muted, 30)
        eventY += block.travelLines.length * 30
      }
      drawText(context, block.bodyLines, PADDING + 46, eventY, font(24, 550), COLORS.ink, 34)
      eventY += block.bodyLines.length * 34 + 10
    })

    if (index < days.length - 1) {
      context.strokeStyle = COLORS.line
      context.lineWidth = 1.5
      context.beginPath()
      context.moveTo(PADDING + 46, rowY + row.height)
      context.lineTo(WIDTH - PADDING - 28, rowY + row.height)
      context.stroke()
    }
    rowY += row.height
  })
}

function drawFooter(
  context: CanvasRenderingContext2D,
  y: number,
  font: (size: number, weight?: number) => string,
) {
  context.strokeStyle = COLORS.line
  context.lineWidth = 1.5
  context.beginPath()
  context.moveTo(PADDING, y)
  context.lineTo(WIDTH - PADDING, y)
  context.stroke()
  drawText(context, ["TRAVEL PLANET"], PADDING, y + 22, font(18, 800), COLORS.muted, 26)
}

function wrapText(
  context: CanvasRenderingContext2D,
  value: string,
  maxWidth: number,
  font: string,
): string[] {
  const text = cleanText(value)
  if (!text) return []
  const lines: string[] = []
  let line = ""
  for (const character of Array.from(text)) {
    const candidate = line + character
    if (measureText(context, candidate, font) <= maxWidth || !line) {
      line = candidate
    } else {
      lines.push(line)
      line = character
    }
  }
  if (line) lines.push(line)
  return lines
}

function measureText(context: CanvasRenderingContext2D, value: string, font: string): number {
  context.font = font
  return context.measureText(value).width
}

function drawText(
  context: CanvasRenderingContext2D,
  lines: string[],
  x: number,
  y: number,
  font: string,
  color: string,
  lineHeight: number,
) {
  context.font = font
  context.fillStyle = color
  context.textAlign = "left"
  context.textBaseline = "top"
  lines.forEach((line, index) => context.fillText(line, x, y + index * lineHeight))
}

function drawCenteredText(
  context: CanvasRenderingContext2D,
  value: string,
  centerX: number,
  centerY: number,
  font: string,
  color: string,
) {
  context.save()
  context.font = font
  context.fillStyle = color
  context.textAlign = "center"
  context.textBaseline = "middle"
  context.fillText(value, centerX, centerY)
  context.restore()
}

function fillRoundedRect(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number,
  color: string,
) {
  context.fillStyle = color
  roundedRect(context, x, y, width, height, radius)
  context.fill()
}

function roundedRect(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number,
) {
  const safeRadius = Math.min(radius, width / 2, height / 2)
  context.beginPath()
  context.moveTo(x + safeRadius, y)
  context.arcTo(x + width, y, x + width, y + height, safeRadius)
  context.arcTo(x + width, y + height, x, y + height, safeRadius)
  context.arcTo(x, y + height, x, y, safeRadius)
  context.arcTo(x, y, x + width, y, safeRadius)
  context.closePath()
}

function unique(values: string[]): string[] {
  return [...new Set(values.map(cleanText).filter(Boolean))]
}

function cleanText(value: string): string {
  return String(value || "").replace(/\s+/gu, " ").trim()
}

function visibleLength(value: string): number {
  return Array.from(cleanText(value)).length
}
