import type { DailyItinerary, ItineraryData, Schedule } from "@/types"

const WIDTH = 1080
const PADDING = 64
const CONTENT_WIDTH = WIDTH - PADDING * 2
const MAX_HEIGHT = 18_000
const DAY_TITLE_MAX = 9
const BODY_FONT = '"Microsoft YaHei UI", "PingFang SC", "Noto Sans CJK SC", sans-serif'
const ART_FONT = '"STKaiti", "KaiTi", "FangSong", "Songti SC", serif'

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

type CompactNode = {
  period: string
  place: string
  activity: string
  logistics: boolean
  dining: boolean
}

type CompactDay = {
  date: string
  title: string
  nodes: CompactNode[]
  summary: string
}

type CompactContent = {
  title: string
  subtitle: string
  attractions: string[]
  foods: string[]
  stations: string[]
  days: CompactDay[]
}

type HeaderMetrics = {
  height: number
  titleFontSize: number
  titleLineHeight: number
  titleLines: string[]
  subtitleFontSize: number
}

type DayRowMetrics = {
  height: number
  summaryLines: string[]
}

export function getItineraryImageDiagnostics(data: ItineraryData) {
  const compact = buildCompactContent(data)
  const bodyCopy = [
    ...compact.attractions,
    ...compact.foods,
    ...compact.days.flatMap((day) => [day.title, day.summary]),
  ].join("")
  return {
    bodyCharacters: visibleLength(bodyCopy),
    dayCount: compact.days.length,
    stationCount: compact.stations.length,
    scheduleCount: compact.days.reduce((total, day) => total + day.nodes.length, 0),
    attractionCount: compact.attractions.length,
    foodCount: compact.foods.length,
  }
}

export async function renderItineraryImage(data: ItineraryData): Promise<Blob> {
  await document.fonts?.ready

  const compact = buildCompactContent(data)
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

function buildCompactContent(data: ItineraryData): CompactContent {
  const destination = cleanText(data.trip_info.destination) || "旅行"
  const dayCount = data.itinerary.length
  const theme = cleanText(data.experience_summary?.tripTheme || "")
  const title = theme || `${destination}行程`
  const date = cleanText(data.trip_info.date_label)
  const subtitle = [date, dayCount && !/天/u.test(date) ? `${dayCount}天` : ""].filter(Boolean).join(" · ")

  const days = data.itinerary.map((day, dayIndex) => buildCompactDay(day, dayIndex))
  const selectedNodes = days.flatMap((day) => day.nodes)
  const attractions = unique(
    selectedNodes
      .filter((node) => !node.logistics && !node.dining)
      .map((node) => shorten(node.place, 14)),
  ).slice(0, 9)
  const foods = collectFoods(data, selectedNodes).slice(0, 6)
  const stations = unique(selectedNodes.filter((node) => node.logistics).map((node) => node.place))

  return { title, subtitle, attractions, foods, stations, days }
}

function buildCompactDay(day: DailyItinerary, dayIndex: number): CompactDay {
  const selected = selectCoreSchedules(day)
  const nodes = selected.map((schedule) => ({
    period: broadPeriod(schedule),
    place: schedulePlace(schedule),
    activity: scheduleActivity(schedule),
    logistics: isLogisticsOnly(schedule),
    dining: isDiningSchedule(schedule),
  }))

  return {
    date: cleanText(day.date),
    title: cleanDayTitle(day.title || `第 ${dayIndex + 1} 天`),
    nodes,
    summary: summarizeDay(nodes),
  }
}

function cleanDayTitle(value: string): string {
  const withoutNotes = cleanText(value).replace(/[（(].*$/u, "").trim()
  const firstClause = withoutNotes.split(/[，,；;]/u)[0]?.trim() || withoutNotes
  return shorten(firstClause || "当日行程", DAY_TITLE_MAX)
}

function summarizeDay(nodes: CompactNode[]): string {
  const sightseeingNodes = nodes.filter((node) => !node.logistics && !node.dining)
  const activityNodes = nodes.filter((node) => !node.logistics)
  const source = sightseeingNodes.length ? sightseeingNodes : activityNodes.length ? activityNodes : nodes
  if (!source.length) return "当天暂无具体日程。"

  const selected = evenlySelect(source, Math.min(2, source.length))
  const clauses = selected.map((node) => `${node.period}${summaryAction(node)}`)
  return `${clauses.join("，")}。`
}

function summaryAction(node: CompactNode): string {
  if (node.dining) return `品尝${shorten(node.place, 12)}`
  if (node.logistics) {
    if (/退房/u.test(node.activity)) return "办理退房"
    if (/返程|返回/u.test(node.activity)) return `从${shorten(node.place, 10)}返程`
    if (/抵达|到达/u.test(node.activity)) return `抵达${shorten(node.place, 10)}`
    return `前往${shorten(node.place, 10)}`
  }
  const place = shorten(node.place, 12)
  if (/骑行/u.test(node.activity)) return `骑行游览${place}`
  if (/参观/u.test(node.activity)) return `参观${place}`
  if (/漫步|步行|逛/u.test(node.activity)) return `漫步${place}`
  if (/登山|登上|攀登|爬山/u.test(node.activity)) return `登临${place}`
  if (/观看|观赏|看/u.test(node.activity)) return `观赏${place}`
  return `游览${place}`
}

function selectCoreSchedules(day: DailyItinerary): Schedule[] {
  const source = day.schedules.filter((schedule) => schedulePlace(schedule))
  if (source.length <= 4) return source

  const activities = source.filter((schedule) => !isLogisticsOnly(schedule))
  const selected: Schedule[] = []
  const firstLogistics = source.find((schedule) => isLogisticsOnly(schedule))
  const lastLogistics = [...source].reverse().find((schedule) => isLogisticsOnly(schedule))

  if (firstLogistics && source.indexOf(firstLogistics) === 0) selected.push(firstLogistics)
  for (const schedule of evenlySelect(activities, Math.max(1, 4 - selected.length))) {
    if (!selected.includes(schedule)) selected.push(schedule)
  }
  if (lastLogistics && source.indexOf(lastLogistics) === source.length - 1 && !selected.includes(lastLogistics)) {
    if (selected.length >= 4) selected.pop()
    selected.push(lastLogistics)
  }
  return selected.sort((a, b) => source.indexOf(a) - source.indexOf(b)).slice(0, 4)
}

function evenlySelect<T>(items: T[], count: number): T[] {
  if (items.length <= count) return items
  if (count <= 1) return items.slice(0, 1)
  return Array.from({ length: count }, (_, index) => {
    const sourceIndex = Math.round((index * (items.length - 1)) / (count - 1))
    return items[sourceIndex]
  })
}

function collectFoods(data: ItineraryData, nodes: CompactNode[]): string[] {
  const recommendations = (data.food_recommendations || []).flatMap(extractFoodCandidates)
  const scheduled = nodes.flatMap((node) => extractFoodCandidates(node.activity))
  return unique([...scheduled, ...recommendations]).filter(isDishLike)
}

function extractFoodCandidates(value: string): string[] {
  let text = stripSensitiveDetail(value)
    .replace(/\.{2,}|…+/gu, "")
    .replace(/[（(][^）)]*(?:推荐|地址|人均|营业|门店)[^）)]*[）)]/gu, "")
  const colonIndex = Math.max(text.lastIndexOf("："), text.lastIndexOf(":"))
  if (colonIndex >= 0 && colonIndex < text.length - 1) text = text.slice(colonIndex + 1)
  const foodMatch = text.match(/(?:品尝|享用|吃|用餐|推荐)[:：]?([^。；;]+)/u)
  if (foodMatch) text = foodMatch[1]
  return text
    .split(/[、，,；;/]|(?:和|与)/u)
    .map(cleanFoodName)
    .filter(Boolean)
}

function cleanFoodName(value: string): string {
  return shorten(
    cleanText(value)
      .replace(/^(?:早餐|午餐|晚餐|美食|当地特色)[:：]?/u, "")
      .replace(/[（(][^）)]*[）)]/gu, "")
      .trim(),
    12,
  )
}

function isDishLike(value: string): boolean {
  if (/酒店|宾馆|客栈/u.test(value)) return false
  return /汤|包|饭|面|粉|饼|肉|鸡|鸭|鹅|鱼|虾|蟹|菜|锅贴|烧烤|火锅|小吃|米线|饵丝|乳扇|粑粑|茶|咖啡|甜品|酒吧|酒馆|啤酒|米酒/u.test(value)
}

function isDiningSchedule(schedule: Schedule): boolean {
  const activity = cleanText(schedule.activity)
  const place = cleanText(schedule.place_name || schedule.map_label || "")
  if (/酒店|宾馆|客栈/u.test(place)) return false
  if (/餐厅|饭店|食馆|小吃店|烧烤店|咖啡店|茶馆/u.test(place) || isDishLike(place)) return true
  return /^(?:上午|中午|午后|下午|傍晚|夜间|晚上)?\s*(?:在.+)?(?:早餐|午餐|晚餐|品尝|享用|用餐|就餐)/u.test(activity)
}

function isLogisticsOnly(schedule: Schedule): boolean {
  const text = `${schedule.place_name || ""} ${schedule.activity || ""} ${schedule.transport || ""}`
  const locationOnly = /(?:火车站|高铁站|客运站|汽车站|机场|航站楼|码头|港口|地铁站|车站|[\p{Script=Han}]{2,10}站|酒店|宾馆|客栈)/u.test(text)
  const transferOnly = /(?:乘坐|搭乘|换乘|抵达|到达|出发|返程|办理入住|酒店入住|办理退房)/u.test(text)
  return locationOnly || (Boolean(schedule.transport) && transferOnly)
}

function broadPeriod(schedule: Schedule): string {
  const source = `${schedule.time_period || ""} ${schedule.start_time || ""}`
  if (/上午|早晨|清晨|早餐/u.test(source)) return "上午"
  if (/中午|午餐/u.test(source)) return "中午"
  if (/下午|午后/u.test(source)) return "午后"
  if (/傍晚|黄昏/u.test(source)) return "傍晚"
  if (/晚上|夜间|夜晚|晚餐/u.test(source)) return "夜间"
  const match = source.match(/(?:^|\s)(\d{1,2}):\d{2}/u)
  if (!match) return "灵活"
  const hour = Number(match[1])
  if (hour < 12) return "上午"
  if (hour < 17) return "午后"
  if (hour < 20) return "傍晚"
  return "夜间"
}

function schedulePlace(schedule: Schedule): string {
  const source = cleanText(schedule.place_name || schedule.map_label || schedule.activity)
  return shorten(stripSensitiveDetail(source), 18)
}

function scheduleActivity(schedule: Schedule): string {
  return shorten(stripSensitiveDetail(schedule.activity), 30)
}

function stripSensitiveDetail(value: string): string {
  return cleanText(value)
    .replace(/\b(?:G|D|C|Z|T|K)\s?\d{1,5}\b/giu, "")
    .replace(/\b[A-Z]{2}\s?\d{3,4}\b/gu, "")
    .replace(/\b\d{1,2}:\d{2}\b/gu, "")
    .replace(/\d{1,2}时(?:\d{1,2}分)?/gu, "")
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
  while (measureText(context, compact.subtitle, font(subtitleFontSize, 650)) > maxWidth && subtitleFontSize > 20) {
    subtitleFontSize -= 1
  }
  const height = 40 + titleLines.length * titleLineHeight + 18 + subtitleFontSize + 48
  return { height, titleFontSize, titleLineHeight, titleLines, subtitleFontSize }
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
  const summaryWidth = CONTENT_WIDTH - 360
  const rows = days.map((day) => {
    const summaryLines = wrapText(context, day.summary, summaryWidth, font(24, 550))
    const height = Math.max(112, 42 + summaryLines.length * 34)
    return { height, summaryLines }
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
  drawText(context, [compact.subtitle], PADDING + 50, subtitleY, font(metrics.subtitleFontSize, 650), COLORS.muted, 36)
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
    drawText(context, [day.title], PADDING + 46, rowY + 60, font(26, 800), COLORS.navy, 34)

    drawText(context, row.summaryLines, PADDING + 332, rowY + 25, font(24, 550), COLORS.ink, 34)

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

function shorten(value: string, maxLength: number): string {
  const text = cleanText(value)
  if (visibleLength(text) <= maxLength) return text
  return `${Array.from(text).slice(0, Math.max(1, maxLength - 1)).join("")}…`
}

function visibleLength(value: string): number {
  return Array.from(cleanText(value)).length
}
